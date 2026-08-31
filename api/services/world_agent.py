"""Agent-facing helpers for the strict spec-first world runtime."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from services.world_store import WorldStoreError, new_world_id, validate_workspace_relative_path


WORLD_STAGE_IDS = (
    "intent",
    "blockout",
    "structure",
    "environment",
    "assets",
    "materials",
    "lighting",
    "gameplay",
    "optimization",
)
WORLD_STAGE_STATUSES = ("locked", "ready", "running", "passed", "failed")
WORLD_GATE_IDS = ("construction", "visual", "gameplay")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldStoreError(f"{label} is required")
    return value.strip()


def _default_build_spec() -> dict[str, Any]:
    return {
        "kind": "polykit.build-spec",
        "version": 1,
        "environment": None,
        "buildings": [],
    }


def _default_game_spec() -> dict[str, Any]:
    return {
        "kind": "polykit.game-spec",
        "version": 1,
        "player": {
            "controller": "walk",
            "radius": 0.28,
            "height": 1.7,
            "move_speed": 2.8,
            "spawn": {"mode": "auto"},
        },
        "collision": {"mode": "semantic-aabb"},
        "interactions": [],
        "objectives": [],
    }


def _initial_runtime(prompt: str, timestamp: str) -> dict[str, Any]:
    return {
        "version": 1,
        "intent": {"prompt": prompt},
        "build": _default_build_spec(),
        "scene": None,
        "compiled": {"instances": []},
        "game": _default_game_spec(),
        "state": {
            "stages": [
                {"id": stage_id, "status": "ready" if index == 0 else "locked"}
                for index, stage_id in enumerate(WORLD_STAGE_IDS)
            ],
            "gates": {
                gate_id: {"status": "pending", "issues": []}
                for gate_id in WORLD_GATE_IDS
            },
            "updated_at": timestamp,
        },
    }


def create_world_document(
    *,
    name: str | None = None,
    prompt: str | None = None,
    parent_world_id: str | None = None,
) -> dict[str, Any]:
    """Allocate a fresh schema-v2 world with one resumable runtime state."""

    world_id = new_world_id()
    timestamp = _now()
    title = (name or "Untitled scene").strip() or "Untitled scene"
    intent = (prompt or "").strip()
    result: dict[str, Any] = {
        "schema_version": 2,
        "kind": "polykit.world",
        "id": world_id,
        "name": title,
        "created_at": timestamp,
        "updated_at": timestamp,
        "runtime": _initial_runtime(intent, timestamp),
        "artifacts": {},
    }
    if parent_world_id and parent_world_id.strip():
        result["parent_world_id"] = parent_world_id.strip()
    return result


def _runtime(world: Mapping[str, Any]) -> dict[str, Any]:
    value = world.get("runtime")
    if not isinstance(value, Mapping) or value.get("version") != 1:
        raise WorldStoreError("World requires runtime version 1")
    return dict(value)


def _artifact_map(world: Mapping[str, Any]) -> dict[str, Any]:
    value = world.get("artifacts")
    if not isinstance(value, Mapping):
        raise WorldStoreError("World artifacts must be an object keyed by asset id")
    return dict(value)


def attach_world_artifact(
    world: Mapping[str, Any],
    *,
    proto_id: str,
    workspace_path: str,
    workflow_id: str | None = None,
    run_id: str | None = None,
    concept_image: str | None = None,
) -> dict[str, Any]:
    """Attach one workspace asset and bind it to a matching semantic object."""

    safe_proto_id = _require_text(proto_id, "proto_id")
    safe_mesh_path = validate_workspace_relative_path(workspace_path)
    safe_concept_image = validate_workspace_relative_path(concept_image) if concept_image else None

    result = dict(world)
    runtime = _runtime(result)
    artifacts = _artifact_map(result)
    current = artifacts.get(safe_proto_id)
    artifact = dict(current) if isinstance(current, Mapping) else {}
    artifact["mode"] = "workspace-mesh"
    if safe_concept_image:
        artifact["concept_image"] = safe_concept_image

    mesh: dict[str, str] = {"kind": "mesh", "workspace_path": safe_mesh_path}
    if workflow_id and workflow_id.strip():
        mesh["workflow_id"] = workflow_id.strip()
    if run_id and run_id.strip():
        mesh["run_id"] = run_id.strip()
    artifact["mesh"] = mesh
    artifacts[safe_proto_id] = artifact

    scene = runtime.get("scene")
    if isinstance(scene, Mapping):
        scene_copy = dict(scene)
        objects = scene_copy.get("objects")
        if isinstance(objects, list):
            next_objects: list[Any] = []
            for value in objects:
                if isinstance(value, Mapping) and value.get("id") == safe_proto_id:
                    obj = dict(value)
                    obj["asset"] = {
                        "workspacePath": safe_mesh_path,
                        "source": "world-artifact",
                        **({"runId": run_id.strip()} if run_id and run_id.strip() else {}),
                    }
                    next_objects.append(obj)
                else:
                    next_objects.append(value)
            scene_copy["objects"] = next_objects
            runtime["scene"] = scene_copy

    result["runtime"] = runtime
    result["artifacts"] = artifacts
    result["updated_at"] = _now()
    return result


def update_world_stage(
    world: Mapping[str, Any],
    *,
    stage_id: str,
    status: str,
    note: str | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Advance one spec-first pass and unlock the next pass on success."""

    safe_stage = _require_text(stage_id, "stage_id")
    if safe_stage not in WORLD_STAGE_IDS:
        raise WorldStoreError(
            f"Unknown world stage {safe_stage!r}; expected one of {', '.join(WORLD_STAGE_IDS)}"
        )
    safe_status = _require_text(status, "status")
    if safe_status not in WORLD_STAGE_STATUSES:
        raise WorldStoreError(
            f"Unknown world stage status {safe_status!r}; expected one of {', '.join(WORLD_STAGE_STATUSES)}"
        )

    result = dict(world)
    runtime = _runtime(result)
    state = runtime.get("state")
    if not isinstance(state, Mapping):
        raise WorldStoreError("World runtime requires state")
    state_copy = dict(state)
    raw_stages = state_copy.get("stages")
    if not isinstance(raw_stages, list):
        raise WorldStoreError("World runtime state requires an ordered stages list")

    stage_map = {
        value.get("id"): dict(value)
        for value in raw_stages
        if isinstance(value, Mapping) and value.get("id") in WORLD_STAGE_IDS
    }
    if tuple(stage_id for stage_id in WORLD_STAGE_IDS if stage_id in stage_map) != WORLD_STAGE_IDS:
        raise WorldStoreError("World runtime state is missing one or more required stages")

    stage = stage_map[safe_stage]
    current_status = stage.get("status")
    if current_status == "locked" and safe_status in {"running", "passed", "failed"}:
        raise WorldStoreError(f"World stage {safe_stage!r} is locked; pass the previous stage first")
    if safe_status == "ready" and current_status == "locked":
        raise WorldStoreError(f"World stage {safe_stage!r} cannot be manually unlocked")

    timestamp = _now()
    stage["status"] = safe_status
    if note is not None:
        stage["note"] = note.strip()
    stage["updated_at"] = timestamp

    stage_index = WORLD_STAGE_IDS.index(safe_stage)
    if safe_status == "passed" and stage_index + 1 < len(WORLD_STAGE_IDS):
        next_stage = stage_map[WORLD_STAGE_IDS[stage_index + 1]]
        if next_stage.get("status") == "locked":
            next_stage["status"] = "ready"
            next_stage["updated_at"] = timestamp
    elif safe_status == "failed":
        for later_id in WORLD_STAGE_IDS[stage_index + 1 :]:
            later = stage_map[later_id]
            if later.get("status") != "passed":
                later["status"] = "locked"
                later["updated_at"] = timestamp

    state_copy["stages"] = [stage_map[stage_name] for stage_name in WORLD_STAGE_IDS]
    state_copy["updated_at"] = timestamp
    runtime["state"] = state_copy
    if prompt is not None:
        runtime["intent"] = {"prompt": prompt.strip()}

    result["runtime"] = runtime
    result["updated_at"] = timestamp
    return result


__all__ = [
    "WORLD_GATE_IDS",
    "WORLD_STAGE_IDS",
    "WORLD_STAGE_STATUSES",
    "attach_world_artifact",
    "create_world_document",
    "update_world_stage",
]
