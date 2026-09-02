"""Compile World scene state into canonical ExecutionPlans."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from schemas.execution import ExecutionNode, ExecutionPlan, ExecutionSource
from application.generate_asset import GenerateAssetCommand, compile_generate_asset_plan
from services.runtime_paths import runtime_paths
from services.scene_planner import ScenePlanError
from services.workspace_paths import normalize_collection, resolve_workspace_path


def _scene_asset_path(
    world: Mapping[str, Any],
    object_id: str,
    object_data: Mapping[str, Any],
) -> str | None:
    asset = object_data.get("asset")
    if isinstance(asset, Mapping):
        value = asset.get("workspacePath") or asset.get("workspace_path")
        if isinstance(value, str) and value.strip():
            return value.strip()
    artifacts = world.get("artifacts")
    if isinstance(artifacts, Mapping):
        entry = artifacts.get(object_id)
        mesh = entry.get("mesh") if isinstance(entry, Mapping) else None
        if isinstance(mesh, Mapping):
            value = mesh.get("workspace_path")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _runtime_scene(world: Mapping[str, Any]) -> dict[str, Any]:
    runtime = world.get("runtime")
    if not isinstance(runtime, Mapping) or runtime.get("version") != 1:
        raise ScenePlanError("World has no valid runtime")
    plan = runtime.get("scene")
    if not isinstance(plan, dict):
        raise ScenePlanError("World runtime has no compiled scene")
    return plan


def compile_scene_composition_plan(
    world: Mapping[str, Any],
    *,
    world_id: str,
    collection: str = "Scenes",
    output_name: str = "scene",
    allow_missing: bool = False,
) -> ExecutionPlan:
    """Compile ``runtime.scene`` into a multi-mesh composition plan."""

    scene = _runtime_scene(world)
    raw_metadata = scene.get("metadata")
    quality = raw_metadata.get("layoutQuality") if isinstance(raw_metadata, dict) else None
    if isinstance(quality, dict) and quality.get("status") == "invalid":
        raise ScenePlanError(
            "Scene layout is invalid; fix its bounds or spatial relations before composing."
        )

    raw_objects = scene.get("objects")
    raw_instances = scene.get("instances")
    if not isinstance(raw_objects, list) or not raw_objects:
        raise ScenePlanError("Runtime scene has no objects to compose")
    objects = {
        item.get("id"): item
        for item in raw_objects
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    instances = {
        item.get("objectId"): item
        for item in (raw_instances if isinstance(raw_instances, list) else [])
        if isinstance(item, dict) and isinstance(item.get("objectId"), str)
    }

    nodes: dict[str, ExecutionNode] = {}
    mesh_refs: list[list[str]] = []
    placements: list[dict[str, Any]] = []
    missing: list[str] = []
    for object_id, object_data in objects.items():
        workspace_path = _scene_asset_path(world, object_id, object_data)
        if not workspace_path:
            if str(object_data.get("role") or "").lower() in {"room", "background"}:
                continue
            missing.append(object_id)
            continue
        try:
            resolved = resolve_workspace_path(runtime_paths.workspace, workspace_path)
        except ValueError as exc:
            raise ScenePlanError(f"Invalid asset path for '{object_id}': {exc}") from exc
        if not resolved.is_file():
            missing.append(object_id)
            continue

        node_id = f"asset_{object_id}"
        nodes[node_id] = ExecutionNode(
            class_type="polykit.mesh",
            inputs={"mesh": {"kind": "workspace_path", "path": workspace_path}},
        )
        mesh_refs.append([node_id, "mesh"])
        instance = instances.get(object_id) or {}
        placements.append(
            {
                "position": instance.get("position", [0, 0, 0]),
                "rotation": instance.get("rotation", [0, 0, 0]),
                "scale": instance.get("scale", 1),
                "size": object_data.get("size", [1, 1, 1]),
            }
        )

    if missing and not allow_missing:
        raise ScenePlanError("Scene objects are missing mesh assets: " + ", ".join(sorted(missing)))
    if not mesh_refs:
        raise ScenePlanError("Runtime scene has no resolved mesh assets to compose")

    nodes["compose"] = ExecutionNode(
        class_type="scene-composer/compose",
        inputs={
            "mesh": mesh_refs,
            "params": {
                "output_name": output_name,
                "placements": placements,
                "coordinate_system": "glTF-Y-up",
            },
        },
    )
    nodes["output"] = ExecutionNode(
        class_type="polykit.output",
        inputs={"mesh": ["compose", "mesh"]},
    )

    return ExecutionPlan(
        source=ExecutionSource(kind="world", id=world_id),
        workflow_id="world-compose-scene",
        prompt=nodes,
        output_node_id="output",
        collection=normalize_collection(collection),
        metadata={
            "world_id": world_id,
            "artifact_kind": "scene",
            "workflow_recipe": "world-compose-scene",
            "missing_objects": missing,
            "composition": "scene-composer",
            "layout_quality": quality if isinstance(quality, dict) else None,
        },
    )


def compile_scene_asset_generation_plan(
    *,
    world_id: str,
    object_id: str,
    prompt: str,
    collection: str = "WorldAssets",
    image_model_id: str = "anima/generate",
    mesh_model_id: str = "trellis2/generate",
    enable_texture: bool = True,
    enable_optimize: bool = True,
    target_faces: int = 100_000,
) -> ExecutionPlan:
    """Compile one unresolved semantic scene slot into the shared local model pipeline."""

    object_prompt = str(prompt or "").strip()
    if not object_prompt:
        raise ScenePlanError(f"Scene object '{object_id}' has no generation prompt")
    plan = compile_generate_asset_plan(
        GenerateAssetCommand(
            prompt=object_prompt,
            image_model_id=image_model_id,
            mesh_model_id=mesh_model_id,
            enable_texture=enable_texture,
            enable_optimize=enable_optimize,
            target_faces=target_faces,
            collection=collection,
            world_id=world_id,
            proto_id=object_id,
        )
    )

    output = plan.prompt.get("output")
    if output is None:
        raise ScenePlanError("Generated asset plan is missing its output node")
    source_ref = output.inputs.get("mesh")
    if not isinstance(source_ref, list) or len(source_ref) != 2:
        raise ScenePlanError("Generated asset plan has no final mesh reference")

    plan.prompt["normalize"] = ExecutionNode(
        class_type="asset-evidence/normalize-mesh",
        inputs={
            "mesh": source_ref,
            "params": {"target_size": 1.0, "up_axis": "Y", "center_horizontal": True, "ground": True},
        },
    )
    plan.prompt["integrity"] = ExecutionNode(
        class_type="mesh-production/geometry-integrity",
        inputs={
            "mesh": ["normalize", "mesh"],
            "params": {"require_watertight": False},
        },
    )
    output.inputs["mesh"] = ["integrity", "mesh"]
    plan.source = ExecutionSource(kind="world", id=world_id)
    plan.workflow_id = "world-generate-scene-asset"
    plan.metadata.update({
        "world_id": world_id,
        "proto_id": object_id,
        "artifact_kind": "mesh",
        "workflow_recipe": "world-generate-scene-asset",
        "bind_world_artifact": True,
        "asset_resolution": "generate",
    })
    return plan


__all__ = ["compile_scene_composition_plan", "compile_scene_asset_generation_plan"]
