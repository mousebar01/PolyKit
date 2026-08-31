"""Compile validation repair scopes into ordinary PolyKit workflow drafts.

Production recipes are advisory compilation artifacts, not execution state. The
compiler never starts a WorkflowRun, retries work, or mutates a World document.
It also never pretends a backend can honor a narrower repair scope than it really
supports: scope expansion requires an explicit opt-in from the caller.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from services.world_store import WorldStoreError
from services.world_workflows import build_repair_parts_workflow, build_structure_workflow


PRODUCTION_RECIPE_KIND = "polykit.production-recipe"
PRODUCTION_RECIPE_SCHEMA_VERSION = 1


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in result:
            result.append(item.strip())
    return result


def _runtime(world: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = world.get("runtime")
    if not isinstance(runtime, Mapping) or runtime.get("version") != 1:
        raise WorldStoreError("World requires runtime version 1")
    return runtime


def _buildings(world: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    runtime = _runtime(world)
    build = runtime.get("build")
    raw = build.get("buildings") if isinstance(build, Mapping) else None
    return [item for item in raw or [] if isinstance(item, Mapping)] if isinstance(raw, list) else []


def _scope_from_validation(validation: Mapping[str, Any], repair_scope_id: str) -> dict[str, Any]:
    scopes = validation.get("repair_scopes")
    if not isinstance(scopes, list):
        raise ValueError("Validation report has no repair_scopes")
    for item in scopes:
        if isinstance(item, Mapping) and item.get("id") == repair_scope_id:
            if item.get("kind") != "polykit.repair-scope" or item.get("schema_version") != 1:
                raise ValueError("Repair scope does not use polykit.repair-scope v1")
            return dict(item)
    raise ValueError(f"Repair scope was not found: {repair_scope_id}")


def _building_id_for_scope(world: Mapping[str, Any], scope: Mapping[str, Any]) -> str | None:
    buildings = _buildings(world)
    known = {
        str(item.get("id")): item
        for item in buildings
        if isinstance(item.get("id"), str) and item.get("id")
    }
    for candidate in [
        *_ids(scope.get("affected_subject_ids")),
        *_ids(scope.get("affected_object_ids")),
    ]:
        if candidate in known:
            return candidate
    if len(known) == 1:
        return next(iter(known))
    return None


def _building(world: Mapping[str, Any], building_id: str) -> Mapping[str, Any] | None:
    for item in _buildings(world):
        if item.get("id") == building_id:
            return item
    return None


def _source_mesh_workspace_path(validation: Mapping[str, Any]) -> str | None:
    """Find the authoritative final-mesh path embedded by the spatial judge."""

    found: list[str] = []

    def visit(value: Any) -> None:
        if found:
            return
        if isinstance(value, Mapping):
            if value.get("id") == "spatial.final-mesh":
                metrics = value.get("metrics")
                path = metrics.get("workspace_path") if isinstance(metrics, Mapping) else None
                if isinstance(path, str) and path.strip():
                    found.append(path.strip())
                    return
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(validation.get("details"))
    return found[0] if found else None


def _local_scope_ids(
    world: Mapping[str, Any],
    *,
    building_id: str,
    scope: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    """Validate exact part/relationship ids against the current BuildSpec."""

    building = _building(world, building_id)
    if building is None:
        raise WorldStoreError(f"BuildSpec has no building '{building_id}'")
    anchors = building.get("anchors")
    known_parts = {
        str(item.get("partId") or item.get("part_id"))
        for item in anchors or []
        if isinstance(item, Mapping) and (item.get("partId") or item.get("part_id"))
    } if isinstance(anchors, list) else set()
    attachments = building.get("attachments")
    known_relationships = {
        str(item.get("id"))
        for item in attachments or []
        if isinstance(item, Mapping) and isinstance(item.get("id"), str) and item.get("id")
    } if isinstance(attachments, list) else set()

    part_ids = _ids(scope.get("affected_object_ids"))
    relationship_ids = _ids(scope.get("affected_relationship_ids"))
    if not part_ids or not relationship_ids:
        raise ValueError("Local construction repair requires explicit part and relationship ids")
    unknown_parts = [item for item in part_ids if item not in known_parts]
    unknown_relationships = [item for item in relationship_ids if item not in known_relationships]
    if unknown_parts or unknown_relationships:
        details: list[str] = []
        if unknown_parts:
            details.append("parts=" + ",".join(unknown_parts))
        if unknown_relationships:
            details.append("relationships=" + ",".join(unknown_relationships))
        raise ValueError("Repair scope is stale or does not match current BuildSpec: " + "; ".join(details))
    return part_ids, relationship_ids


def _required_capability(scope: Mapping[str, Any]) -> str:
    causal = str(scope.get("causal_system") or "")
    mapping = {
        "construction_geometry": "blender-scene/repair-parts",
        "camera_composition": "blender-scene/repair-camera",
        "occlusion_geometry": "blender-scene/repair-visibility",
        "scene_layout": "world.scene.repair",
        "semantic_match": "blender-scene/repair-semantic-subject",
        "material": "blender-scene/repair-visual-subject",
        "lighting": "blender-scene/repair-visual-subject",
        "color": "blender-scene/repair-visual-subject",
        "surface": "blender-scene/repair-visual-subject",
        "silhouette": "blender-scene/repair-visual-subject",
        "luminance": "blender-scene/repair-visual-subject",
        "game_spec": "world.gameplay.repair",
        "world_spec": "world.spec.repair",
    }
    return mapping.get(causal, "production-recipe/repair-strategy")


def _workflow_definition(
    execution_request: Mapping[str, Any],
    *,
    recipe_id: str,
    world_id: str,
    repair_scope_id: str,
    scope_expanded: bool,
) -> dict[str, Any]:
    prompt = execution_request.get("prompt")
    if not isinstance(prompt, Mapping):
        raise ValueError("Execution request has no prompt graph")
    workflow_recipe = str(execution_request.get("workflow_id") or "")
    now = datetime.now(timezone.utc).isoformat()
    workflow_id = f"repair-{world_id}-{repair_scope_id.split(':')[-1]}"

    if workflow_recipe == "building-part-repair":
        source = prompt.get("source")
        repair = prompt.get("repair")
        output = prompt.get("output")
        if not all(isinstance(item, Mapping) for item in (source, repair, output)):
            raise ValueError("Part repair workflow is missing source/repair/output nodes")
        source_inputs = source.get("inputs") if isinstance(source, Mapping) else None
        mesh_value = source_inputs.get("mesh") if isinstance(source_inputs, Mapping) else None
        source_path = mesh_value.get("path") if isinstance(mesh_value, Mapping) else ""
        repair_inputs = repair.get("inputs") if isinstance(repair, Mapping) else None
        params = repair_inputs.get("params") if isinstance(repair_inputs, Mapping) else None
        nodes = [
            {
                "id": "source",
                "type": "meshNode",
                "position": {"x": 80, "y": 220},
                "data": {"enabled": True, "params": {"filePath": str(source_path or "")}},
            },
            {
                "id": "repair",
                "type": "nodePackNode",
                "position": {"x": 430, "y": 220},
                "data": {
                    "nodePackId": "blender-scene/repair-parts",
                    "enabled": True,
                    "params": dict(params or {}),
                },
            },
            {
                "id": "output",
                "type": "outputNode",
                "position": {"x": 800, "y": 220},
                "data": {"enabled": True, "params": {}},
            },
        ]
        edges = [
            {
                "id": "source-to-repair",
                "source": "source",
                "sourceHandle": "output",
                "target": "repair",
                "targetHandle": "input-0",
            },
            {
                "id": "repair-to-output",
                "source": "repair",
                "sourceHandle": "output",
                "target": "output",
            },
        ]
        name = "Repair World Parts"
    else:
        brief = prompt.get("brief")
        build = prompt.get("build")
        output = prompt.get("output")
        if not all(isinstance(item, Mapping) for item in (brief, build, output)):
            raise ValueError("Building repair workflow is missing canonical brief/build/output nodes")
        brief_inputs = brief.get("inputs") if isinstance(brief, Mapping) else None
        build_inputs = build.get("inputs") if isinstance(build, Mapping) else None
        params = build_inputs.get("params") if isinstance(build_inputs, Mapping) else None
        nodes = [
            {
                "id": "brief",
                "type": "textNode",
                "position": {"x": 80, "y": 220},
                "data": {
                    "enabled": True,
                    "params": {"text": str((brief_inputs or {}).get("text") or "")},
                },
            },
            {
                "id": "build",
                "type": "nodePackNode",
                "position": {"x": 430, "y": 220},
                "data": {
                    "nodePackId": "blender-scene/build",
                    "enabled": True,
                    "params": dict(params or {}),
                },
            },
            {
                "id": "output",
                "type": "outputNode",
                "position": {"x": 800, "y": 220},
                "data": {"enabled": True, "params": {}},
            },
        ]
        edges = [
            {
                "id": "brief-to-build",
                "source": "brief",
                "sourceHandle": "output",
                "target": "build",
                "targetHandle": "input-0",
            },
            {
                "id": "build-to-output",
                "source": "build",
                "sourceHandle": "output",
                "target": "output",
            },
        ]
        name = "Repair World Construction"

    return {
        "id": workflow_id,
        "name": name,
        "description": "Compiled from PolyKit validation evidence. Review scope metadata before running.",
        "nodes": nodes,
        "edges": edges,
        "createdAt": now,
        "updatedAt": now,
        "metadata": {
            "kind": "polykit.compiled-repair-workflow",
            "recipe_id": recipe_id,
            "world_id": world_id,
            "repair_scope_id": repair_scope_id,
            "scope_expanded": scope_expanded,
        },
    }


def _blocked(
    base: dict[str, Any],
    *,
    code: str,
    message: str,
    required_capability: str | None = None,
    available_fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(base)
    result["status"] = "blocked"
    blocker: dict[str, Any] = {"code": code, "message": message}
    if required_capability:
        blocker["required_capability"] = required_capability
    result["blockers"] = [blocker]
    result["workflow_definition"] = None
    result["execution_request"] = None
    if available_fallback is not None:
        result["available_fallback"] = dict(available_fallback)
    return result


def compile_repair_recipe(
    *,
    world_id: str,
    world: Mapping[str, Any],
    validation: Mapping[str, Any],
    repair_scope_id: str,
    collection: str = "Scenes",
    render_preview: bool = True,
    allow_scope_expansion: bool = False,
) -> dict[str, Any]:
    """Compile one authoritative repair scope without starting execution."""

    scope = _scope_from_validation(validation, repair_scope_id)
    capability = str(validation.get("capability") or "")
    source = scope.get("source")
    source_capability = source.get("capability") if isinstance(source, Mapping) else None
    if source_capability and source_capability != capability and capability != "world.final.validate":
        raise ValueError("Repair scope source capability does not match validation capability")

    recipe_id = f"recipe:{world_id}:{repair_scope_id.removeprefix('repair:')}"
    base = {
        "schema_version": PRODUCTION_RECIPE_SCHEMA_VERSION,
        "kind": PRODUCTION_RECIPE_KIND,
        "id": recipe_id,
        "world_id": world_id,
        "intent": "repair",
        "source": {
            "validation_capability": capability,
            "repair_scope_id": repair_scope_id,
        },
        "desired_scope": scope,
        "compiled_scope": None,
        "scope_expanded": False,
        "blockers": [],
        "workflow_definition": None,
        "execution_request": None,
    }

    locality = str(scope.get("locality") or "scene")
    causal_system = str(scope.get("causal_system") or "domain")
    safe_to_localize = scope.get("safe_to_localize") is True

    if locality == "evidence" or causal_system == "evidence_pipeline":
        result = dict(base)
        result["status"] = "no_workflow"
        result["next_action"] = scope.get("action_hint") or "regenerate_or_attach_evidence"
        result["reason"] = "The validator needs evidence, not a geometry repair workflow."
        return result

    if causal_system not in {"construction", "construction_geometry"}:
        return _blocked(
            base,
            code="repair-backend-missing",
            message="No installed server workflow can honor this repair system yet.",
            required_capability=_required_capability(scope),
        )

    building_id = _building_id_for_scope(world, scope)
    if building_id is None:
        return _blocked(
            base,
            code="repair-building-ambiguous",
            message="The repair scope cannot be mapped to exactly one BuildSpec building.",
            required_capability="blender-scene/repair-parts" if safe_to_localize else None,
        )

    requested_local = safe_to_localize and locality in {"object", "relationship"}
    fallback = {
        "workflow_recipe": "building-construction",
        "compiled_scope": {"locality": "building", "building_id": building_id},
        "scope_expanded": requested_local,
    }

    if requested_local:
        try:
            part_ids, relationship_ids = _local_scope_ids(
                world,
                building_id=building_id,
                scope=scope,
            )
        except (WorldStoreError, ValueError) as exc:
            return _blocked(
                base,
                code="repair-scope-stale",
                message=str(exc),
                required_capability="blender-scene/repair-parts",
            )
        source_mesh = _source_mesh_workspace_path(validation)
        if source_mesh:
            try:
                request = build_repair_parts_workflow(
                    world,
                    world_id=world_id,
                    source_mesh_workspace_path=source_mesh,
                    building_id=building_id,
                    part_ids=part_ids,
                    attachment_ids=relationship_ids,
                    collection=collection,
                    render_preview=render_preview,
                )
            except (WorldStoreError, ValueError) as exc:
                return _blocked(
                    base,
                    code="repair-workflow-unavailable",
                    message=str(exc),
                    required_capability="blender-scene/repair-parts",
                )

            compiled_scope = {
                "locality": locality,
                "building_id": building_id,
                "part_ids": part_ids,
                "relationship_ids": relationship_ids,
            }
            request.metadata.update(
                {
                    "production_recipe_id": recipe_id,
                    "repair_scope_id": repair_scope_id,
                    "desired_repair_scope": scope,
                    "compiled_repair_scope": compiled_scope,
                    "scope_expanded": False,
                }
            )
            execution = request.model_dump()
            result = dict(base)
            result.update(
                {
                    "status": "ready",
                    "compiled_scope": compiled_scope,
                    "scope_expanded": False,
                    "workflow_definition": _workflow_definition(
                        execution,
                        recipe_id=recipe_id,
                        world_id=world_id,
                        repair_scope_id=repair_scope_id,
                        scope_expanded=False,
                    ),
                    "execution_request": execution,
                }
            )
            return result

        if not allow_scope_expansion:
            return _blocked(
                base,
                code="repair-source-mesh-missing",
                message=(
                    "The local repair backend needs the authoritative final GLB from the validator run. "
                    "No source mesh path was present in validation evidence."
                ),
                required_capability="spatial.final-mesh",
                available_fallback=fallback,
            )

    try:
        request = build_structure_workflow(
            world,
            world_id=world_id,
            building_id=building_id,
            collection=collection,
            render_preview=render_preview,
        )
    except (WorldStoreError, ValueError) as exc:
        return _blocked(
            base,
            code="repair-workflow-unavailable",
            message=str(exc),
            required_capability="building-construction",
        )

    compiled_scope = {"locality": "building", "building_id": building_id}
    request.metadata.update(
        {
            "production_recipe_id": recipe_id,
            "repair_scope_id": repair_scope_id,
            "desired_repair_scope": scope,
            "compiled_repair_scope": compiled_scope,
            "scope_expanded": requested_local,
        }
    )
    execution = request.model_dump()
    result = dict(base)
    result.update(
        {
            "status": "ready",
            "compiled_scope": compiled_scope,
            "scope_expanded": requested_local,
            "workflow_definition": _workflow_definition(
                execution,
                recipe_id=recipe_id,
                world_id=world_id,
                repair_scope_id=repair_scope_id,
                scope_expanded=requested_local,
            ),
            "execution_request": execution,
        }
    )
    return result


__all__ = [
    "PRODUCTION_RECIPE_KIND",
    "PRODUCTION_RECIPE_SCHEMA_VERSION",
    "compile_repair_recipe",
]
