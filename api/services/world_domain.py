"""Helpers for schema-v2 world domain documents.

Task progression is intentionally absent here. UI, CLI, automation, or other
clients may call the same World APIs; this module only creates domain documents
and binds produced artifacts back to semantic world objects.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from services.world_store import WorldStoreError, new_world_id, validate_workspace_relative_path


WORLD_QUALITY_GATE_IDS = ("construction", "visual", "gameplay")
CURRENT_TERRAIN_AUTHORING_VERSION = 2


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


def _initial_runtime(prompt: str) -> dict[str, Any]:
    return {
        "version": 1,
        "intent": {"prompt": prompt},
        "build": _default_build_spec(),
        "scene": None,
        "compiled": {"instances": []},
        "game": _default_game_spec(),
        "quality": {
            gate_id: {"status": "pending", "issues": []}
            for gate_id in WORLD_QUALITY_GATE_IDS
        },
    }


def create_world_document(
    *,
    name: str | None = None,
    prompt: str | None = None,
    parent_world_id: str | None = None,
) -> dict[str, Any]:
    """Allocate a fresh schema-v2 world containing domain state only."""

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
        # Creation-time defaults are explicit so later saves never have to
        # guess whether an older world should be migrated to new terrain math.
        "authoring": {"terrain_version": CURRENT_TERRAIN_AUTHORING_VERSION},
        "runtime": _initial_runtime(intent),
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


__all__ = [
    "CURRENT_TERRAIN_AUTHORING_VERSION",
    "WORLD_QUALITY_GATE_IDS",
    "attach_world_artifact",
    "create_world_document",
]
