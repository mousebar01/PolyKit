"""Compile world-domain operations into canonical Workflow Engine requests."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.world_store import WorldStoreError
from services.workspace_paths import normalize_collection


def _runtime(world: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = world.get("runtime")
    if not isinstance(runtime, Mapping) or runtime.get("version") != 1:
        raise WorldStoreError("World requires runtime version 1")
    return runtime


def _first_building(runtime: Mapping[str, Any], building_id: str | None) -> Mapping[str, Any]:
    build = runtime.get("build")
    buildings = build.get("buildings") if isinstance(build, Mapping) else None
    if not isinstance(buildings, list) or not buildings:
        raise WorldStoreError("World BuildSpec has no buildings to construct")
    candidates = [item for item in buildings if isinstance(item, Mapping)]
    if building_id:
        for item in candidates:
            if item.get("id") == building_id:
                return item
        raise WorldStoreError(f"BuildSpec has no building '{building_id}'")
    if not candidates:
        raise WorldStoreError("World BuildSpec has no valid building entries")
    return candidates[0]


def _number(parameters: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = parameters.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _structure_params(building: Mapping[str, Any], *, render_preview: bool) -> dict[str, Any]:
    if building.get("generator") != "blender-parametric":
        raise WorldStoreError(
            f"Building '{building.get('id', '?')}' is not supported by the current structure backend"
        )
    raw = building.get("parameters")
    parameters = raw if isinstance(raw, Mapping) else {}
    result: dict[str, Any] = {
        "preset": "cabin",
        "scene_name": str(building.get("id") or "world_structure"),
        "render_preview": bool(render_preview),
    }
    mapped = {
        "cabin_width": _number(parameters, "width", "cabin_width"),
        "cabin_depth": _number(parameters, "depth", "cabin_depth"),
        "wall_height": _number(parameters, "wallHeight", "wall_height"),
        "roof_pitch_deg": _number(parameters, "roofPitchDeg", "roof_pitch_deg"),
        "roof_overhang": _number(parameters, "roofOverhang", "roof_overhang"),
        "contact_tolerance": _number(parameters, "contactTolerance", "contact_tolerance"),
    }
    result.update({key: value for key, value in mapped.items() if value is not None})
    return result


def _ids(values: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if isinstance(value, str) and value.strip() and value.strip() not in result:
            result.append(value.strip())
    return result


def build_structure_workflow(
    world: Mapping[str, Any],
    *,
    world_id: str,
    building_id: str | None = None,
    collection: str = "Scenes",
    render_preview: bool = True,
) -> WorkflowExecutionRequest:
    """Compile one BuildSpec building into text → Blender → GLB."""

    runtime = _runtime(world)
    building = _first_building(runtime, building_id)
    selected_id = str(building.get("id") or "building")
    intent = runtime.get("intent")
    prompt = intent.get("prompt") if isinstance(intent, Mapping) else ""
    brief = str(prompt or "").strip() or str(building.get("name") or selected_id)
    params = _structure_params(building, render_preview=render_preview)

    nodes = {
        "brief": WorkflowExecutionNode(
            class_type="polykit.text",
            inputs={"text": brief},
        ),
        "build": WorkflowExecutionNode(
            class_type="blender-scene/build",
            inputs={"text": ["brief", "text"], "params": params},
        ),
        "output": WorkflowExecutionNode(
            class_type="polykit.output",
            inputs={"mesh": ["build", "mesh"]},
        ),
    }
    return WorkflowExecutionRequest(
        workflow_id="building-construction",
        prompt=nodes,
        output_node_id="output",
        collection=normalize_collection(collection),
        metadata={
            "world_id": world_id,
            "building_id": selected_id,
            "workflow_recipe": "building-construction",
            "artifact_kind": "scene",
        },
    )


def build_repair_parts_workflow(
    world: Mapping[str, Any],
    *,
    world_id: str,
    source_mesh_workspace_path: str,
    building_id: str,
    part_ids: Sequence[str],
    attachment_ids: Sequence[str],
    collection: str = "Scenes",
    render_preview: bool = True,
) -> WorkflowExecutionRequest:
    """Compile one bounded BuildSpec part repair against an existing final GLB.

    This workflow does not mutate World/BuildSpec and does not expand scope. The
    Blender backend receives exact part/attachment ids plus the authoritative
    building spec and may only adjust those selected parts.
    """

    runtime = _runtime(world)
    building = _first_building(runtime, building_id)
    selected_id = str(building.get("id") or "building")
    selected_parts = _ids(part_ids)
    selected_attachments = _ids(attachment_ids)
    if not selected_parts:
        raise WorldStoreError("Scoped part repair requires at least one BuildSpec part id")
    source_path = str(source_mesh_workspace_path or "").strip()
    if not source_path:
        raise WorldStoreError("Scoped part repair requires an authoritative source mesh path")

    repair_params = {
        "repair_mode": "parts",
        "repair_strategy": "translation-anchor-snap-v1",
        "scene_name": f"{selected_id}_repair",
        "building_spec": dict(building),
        "part_ids": selected_parts,
        "attachment_ids": selected_attachments,
        "render_preview": bool(render_preview),
    }
    nodes = {
        "source": WorkflowExecutionNode(
            class_type="polykit.mesh",
            inputs={
                "mesh": {
                    "kind": "workspace_path",
                    "path": source_path,
                }
            },
        ),
        "repair": WorkflowExecutionNode(
            class_type="blender-scene/repair-parts",
            inputs={
                "mesh": ["source", "mesh"],
                "params": repair_params,
            },
        ),
        "output": WorkflowExecutionNode(
            class_type="polykit.output",
            inputs={"mesh": ["repair", "mesh"]},
        ),
    }
    return WorkflowExecutionRequest(
        workflow_id="building-part-repair",
        prompt=nodes,
        output_node_id="output",
        collection=normalize_collection(collection),
        metadata={
            "world_id": world_id,
            "building_id": selected_id,
            "workflow_recipe": "building-part-repair",
            "artifact_kind": "scene",
            "source_mesh_workspace_path": source_path,
            "repair_part_ids": selected_parts,
            "repair_attachment_ids": selected_attachments,
            "repair_strategy": "translation-anchor-snap-v1",
        },
    )


__all__ = ["build_repair_parts_workflow", "build_structure_workflow"]
