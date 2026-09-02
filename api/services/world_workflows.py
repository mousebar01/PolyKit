"""Compile world-domain operations into canonical execution plans."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from schemas.execution import ExecutionNode, ExecutionPlan, ExecutionSource
from schemas.workflow import WorkflowExecutionRequest
from services.world_store import WorldStoreError
from services.workspace_paths import normalize_collection


STRUCTURE_PRESETS = frozenset({"cabin", "subway_station"})
RENDER_PROFILES = frozenset({"production", "gray", "toon"})


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


def _preset(parameters: Mapping[str, Any]) -> str:
    preset = str(parameters.get("preset") or "cabin").strip()
    if preset not in STRUCTURE_PRESETS:
        raise WorldStoreError(
            f"Unsupported structure preset '{preset}'. Expected one of: {', '.join(sorted(STRUCTURE_PRESETS))}"
        )
    return preset


def _render_profile(value: str) -> str:
    profile = str(value or "production").strip()
    if profile not in RENDER_PROFILES:
        raise WorldStoreError(
            f"Unsupported render profile '{profile}'. Expected one of: {', '.join(sorted(RENDER_PROFILES))}"
        )
    return profile


def _structure_params(
    building: Mapping[str, Any],
    *,
    render_preview: bool,
    render_profile: str,
) -> dict[str, Any]:
    if building.get("generator") != "blender-parametric":
        raise WorldStoreError(
            f"Building '{building.get('id', '?')}' is not supported by the current structure backend"
        )

    raw = building.get("parameters")
    parameters = raw if isinstance(raw, Mapping) else {}
    preset = _preset(parameters)
    result: dict[str, Any] = {
        "preset": preset,
        "scene_name": str(building.get("id") or "world_structure"),
        "render_preview": bool(render_preview),
        "render_profile": _render_profile(render_profile),
    }

    if preset == "subway_station":
        mapped = {
            "station_width": _number(parameters, "width", "stationWidth", "station_width"),
            "station_length": _number(parameters, "length", "stationLength", "station_length"),
            "ceiling_height": _number(parameters, "ceilingHeight", "ceiling_height"),
            "platform_width": _number(parameters, "platformWidth", "platform_width"),
            "platform_height": _number(parameters, "platformHeight", "platform_height"),
            "column_spacing": _number(parameters, "columnSpacing", "column_spacing"),
            "column_size": _number(parameters, "columnSize", "column_size"),
            "rail_gauge": _number(parameters, "railGauge", "rail_gauge"),
            "tactile_width_ratio": _number(parameters, "tactileWidthRatio", "tactile_width_ratio"),
            "tactile_inset_ratio": _number(parameters, "tactileInsetRatio", "tactile_inset_ratio"),
            "tactile_width": _number(parameters, "tactileWidth", "tactile_width"),
            "tactile_inset": _number(parameters, "tactileInset", "tactile_inset"),
            "contact_tolerance": _number(parameters, "contactTolerance", "contact_tolerance"),
        }
    else:
        mapped = {
            "cabin_width": _number(parameters, "width", "cabin_width"),
            "cabin_depth": _number(parameters, "depth", "cabin_depth"),
            "wall_height": _number(parameters, "wallHeight", "wall_height"),
            "wall_thickness": _number(parameters, "wallThickness", "wall_thickness"),
            "floor_thickness": _number(parameters, "floorThickness", "floor_thickness"),
            "roof_pitch_deg": _number(parameters, "roofPitchDeg", "roof_pitch_deg"),
            "roof_thickness": _number(parameters, "roofThickness", "roof_thickness"),
            "roof_overhang": _number(parameters, "roofOverhang", "roof_overhang"),
            "contact_tolerance": _number(parameters, "contactTolerance", "contact_tolerance"),
        }
    result.update({key: value for key, value in mapped.items() if value is not None})
    return result


def compile_structure_plan(
    world: Mapping[str, Any],
    *,
    world_id: str,
    building_id: str | None = None,
    collection: str = "Scenes",
    render_preview: bool = True,
    render_profile: str = "production",
) -> ExecutionPlan:
    """Compile one World BuildSpec building into an executable plan."""

    runtime = _runtime(world)
    building = _first_building(runtime, building_id)
    selected_id = str(building.get("id") or "building")
    intent = runtime.get("intent")
    prompt = intent.get("prompt") if isinstance(intent, Mapping) else ""
    brief = str(prompt or "").strip() or str(building.get("name") or selected_id)
    params = _structure_params(
        building,
        render_preview=render_preview,
        render_profile=render_profile,
    )

    nodes = {
        "brief": ExecutionNode(
            class_type="polykit.text",
            inputs={"text": brief},
        ),
        "build": ExecutionNode(
            class_type="blender-scene/build",
            inputs={"text": ["brief", "text"], "params": params},
        ),
        "output": ExecutionNode(
            class_type="polykit.output",
            inputs={"mesh": ["build", "mesh"]},
        ),
    }
    return ExecutionPlan(
        source=ExecutionSource(kind="world", id=world_id),
        # Retained until legacy WorkflowExecutionRequest callers are migrated.
        workflow_id="building-construction",
        prompt=nodes,
        output_node_id="output",
        collection=normalize_collection(collection),
        metadata={
            "world_id": world_id,
            "building_id": selected_id,
            "workflow_recipe": "building-construction",
            "artifact_kind": "scene",
            "structure_preset": params["preset"],
            "render_profile": params["render_profile"],
        },
    )


def build_structure_workflow(
    world: Mapping[str, Any],
    *,
    world_id: str,
    building_id: str | None = None,
    collection: str = "Scenes",
    render_preview: bool = True,
    render_profile: str = "production",
) -> WorkflowExecutionRequest:
    """Compatibility wrapper for callers that still expect a workflow request."""

    plan = compile_structure_plan(
        world,
        world_id=world_id,
        building_id=building_id,
        collection=collection,
        render_preview=render_preview,
        render_profile=render_profile,
    )
    return WorkflowExecutionRequest.model_validate(plan.model_dump(mode="python"))


__all__ = [
    "RENDER_PROFILES",
    "STRUCTURE_PRESETS",
    "build_structure_workflow",
    "compile_structure_plan",
]
