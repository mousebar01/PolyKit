"""World application commands shared by Web and Agent entry points."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, Field

from application.execution import PreparedExecution, prepare_execution_run
from schemas.execution import ExecutionInitiator
from services.scene_assets import find_asset_candidates, resolve_scene_asset_slots
from services.scene_planner import normalize_scene_plan
from services.world_plans import compile_scene_asset_generation_plan, compile_scene_composition_plan
from services.world_workflows import compile_structure_plan


class ComposeWorldCommand(BaseModel):
    collection: str = Field(default="Scenes", max_length=160)
    output_name: str = Field(default="scene", min_length=1, max_length=120)
    allow_missing: bool = False


class BuildWorldStructureCommand(BaseModel):
    building_id: str | None = Field(default=None, max_length=160)
    collection: str = Field(default="Scenes", max_length=160)
    render_preview: bool = True
    render_profile: str = Field(default="production", max_length=32)


class ResolveWorldAssetsCommand(BaseModel):
    collection: str = Field(default="WorldAssets", max_length=160)
    generate_missing: bool = True
    include_context: bool = False
    include_scatter: bool = False
    min_library_score: float = Field(default=3.0, ge=0, le=100)
    image_model_id: str = Field(default="anima/generate", min_length=1, max_length=200)
    mesh_model_id: str = Field(default="trellis2/generate", min_length=1, max_length=200)
    enable_texture: bool = True
    enable_optimize: bool = True
    target_faces: int = Field(default=100_000, ge=100, le=1_000_000)


@dataclass(frozen=True)
class CompiledWorldAssetResolution:
    scene: dict[str, Any] | None
    decisions: list[dict[str, Any]]
    generation_plans: list[Any]
    library_bindings: list[tuple[str, str]]


def compile_world_asset_resolution(
    world: Mapping[str, Any],
    *,
    world_id: str,
    command: ResolveWorldAssetsCommand,
) -> CompiledWorldAssetResolution:
    runtime = world.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("World has no valid runtime")

    generation_plans: list[Any] = []
    library_bindings: list[tuple[str, str]] = []
    decisions: list[dict[str, Any]] = []
    scene = runtime.get("scene")
    world_prompt = ""
    intent = runtime.get("intent")
    if isinstance(intent, Mapping):
        world_prompt = str(intent.get("prompt") or "").strip()

    if isinstance(scene, Mapping):
        plan = normalize_scene_plan(scene, scene_id=world_id)
        resolved, decisions = resolve_scene_asset_slots(
            plan,
            min_score=command.min_library_score,
            include_context=command.include_context,
        )
        by_id = {obj.id: obj for obj in resolved.objects}
        if command.generate_missing:
            for decision in decisions:
                if decision.get("mode") != "generate":
                    continue
                object_id = str(decision.get("object_id") or "")
                obj = by_id.get(object_id)
                if obj is None:
                    continue
                object_prompt = str(decision.get("prompt") or obj.name).strip()
                object_prompt = (
                    f"Isolated 3D asset concept: {object_prompt}. "
                    "Centered full object, plain background, no surrounding scene, no text."
                )
                if world_prompt:
                    object_prompt += f" Match the visual style of this world brief: {world_prompt}"
                generation_plans.append(
                    compile_scene_asset_generation_plan(
                        world_id=world_id,
                        object_id=object_id,
                        prompt=object_prompt,
                        collection=command.collection,
                        image_model_id=command.image_model_id,
                        mesh_model_id=command.mesh_model_id,
                        enable_texture=command.enable_texture,
                        enable_optimize=command.enable_optimize,
                        target_faces=command.target_faces,
                    )
                )
        return CompiledWorldAssetResolution(
            scene=resolved.model_dump(mode="json", by_alias=True, exclude_none=True),
            decisions=decisions,
            generation_plans=generation_plans,
            library_bindings=library_bindings,
        )

    build = runtime.get("build")
    environment = build.get("environment") if isinstance(build, Mapping) else None
    assets = environment.get("assets") if isinstance(environment, Mapping) else None
    if not isinstance(assets, list) or not assets:
        raise ValueError("World has neither a semantic scene nor environment asset prototypes to resolve")

    artifacts = world.get("artifacts")
    artifact_map = artifacts if isinstance(artifacts, Mapping) else {}
    for raw in assets:
        if not isinstance(raw, Mapping):
            continue
        object_id = str(raw.get("id") or "").strip()
        if not object_id:
            continue
        attached = artifact_map.get(object_id)
        mesh = attached.get("mesh") if isinstance(attached, Mapping) else None
        workspace_path = mesh.get("workspace_path") if isinstance(mesh, Mapping) else None
        if isinstance(workspace_path, str) and workspace_path.strip():
            decisions.append({"object_id": object_id, "mode": "existing", "workspace_path": workspace_path.strip()})
            continue

        name = str(raw.get("name") or object_id)
        category = str(raw.get("category") or "")
        image_prompt = str(raw.get("imagePrompt") or raw.get("image_prompt") or name)
        matches = find_asset_candidates(
            " ".join(part for part in (name, category, image_prompt) if part),
            category=category or None,
            limit=1,
            meshes_only=True,
        )
        best = matches[0] if matches else None
        if best and float(best["score"]) >= command.min_library_score:
            path = str(best["workspace_path"])
            decisions.append({
                "object_id": object_id,
                "mode": "library",
                "workspace_path": path,
                "score": best["score"],
            })
            library_bindings.append((object_id, path))
            continue

        tier = str(raw.get("tier") or "scatter").lower()
        should_generate = command.generate_missing and (tier == "hero" or command.include_scatter)
        if should_generate:
            prompt = (
                f"Isolated 3D asset concept: {image_prompt}. "
                "Centered full object, plain background, no surrounding scene, no text."
            )
            if world_prompt:
                prompt += f" Match the visual style of this world brief: {world_prompt}"
            decisions.append({
                "object_id": object_id,
                "mode": "generate",
                "tier": tier,
                "prompt": prompt,
                "target_height": raw.get("targetHeight"),
            })
            generation_plans.append(
                compile_scene_asset_generation_plan(
                    world_id=world_id,
                    object_id=object_id,
                    prompt=prompt,
                    collection=command.collection,
                    image_model_id=command.image_model_id,
                    mesh_model_id=command.mesh_model_id,
                    enable_texture=command.enable_texture,
                    enable_optimize=command.enable_optimize,
                    target_faces=command.target_faces,
                )
            )
        else:
            decisions.append({
                "object_id": object_id,
                "mode": "procedural" if raw.get("proceduralHint") else "unresolved",
                "tier": tier,
                **({"procedural_hint": raw.get("proceduralHint")} if raw.get("proceduralHint") else {}),
                "reason": "Scatter generation is disabled by default." if tier == "scatter" else "No asset source resolved.",
            })

    return CompiledWorldAssetResolution(
        scene=None,
        decisions=decisions,
        generation_plans=generation_plans,
        library_bindings=library_bindings,
    )

def prepare_world_composition_run(
    world: Mapping[str, Any],
    *,
    world_id: str,
    command: ComposeWorldCommand,
    initiator: ExecutionInitiator,
) -> PreparedExecution:
    plan = compile_scene_composition_plan(
        world,
        world_id=world_id,
        collection=command.collection,
        output_name=command.output_name,
        allow_missing=command.allow_missing,
    )
    return prepare_execution_run(plan, initiator=initiator)


def prepare_world_structure_run(
    world: Mapping[str, Any],
    *,
    world_id: str,
    command: BuildWorldStructureCommand,
    initiator: ExecutionInitiator,
) -> PreparedExecution:
    plan = compile_structure_plan(
        world,
        world_id=world_id,
        building_id=command.building_id,
        collection=command.collection,
        render_preview=command.render_preview,
        render_profile=command.render_profile,
    )
    return prepare_execution_run(plan, initiator=initiator)


__all__ = [
    "BuildWorldStructureCommand",
    "CompiledWorldAssetResolution",
    "ResolveWorldAssetsCommand",
    "compile_world_asset_resolution",
    "ComposeWorldCommand",
    "prepare_world_composition_run",
    "prepare_world_structure_run",
]
