"""Agent-facing world planning helpers.

The language model remains the director: it decides what the world means,
which regions and prototypes are needed, and which local workflow to call.
This module only keeps that plan and its workspace-owned artifact references
in a predictable shape so the MCP tools can be small and auditable.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from services.world_store import WorldStoreError, new_world_id, validate_workspace_relative_path


# These stages mirror the coarse-to-fine flow described by WorldClaw: intent
# and scene planning, a terrain foundation, spatial placement, asset/material
# work, and a render/critique pass.  ``materials`` is kept explicit because a
# local PolyKit workflow may complete it independently of asset generation.
WORLDCLAW_STAGE_IDS = (
    "intent",
    "plan",
    "terrain",
    "placement",
    "assets",
    "materials",
    "refine",
)
WORLDCLAW_STAGE_STATUSES = ("pending", "running", "done", "blocked")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldStoreError(f"{label} is required")
    return value.strip()


def create_world_document(
    *,
    name: str | None = None,
    prompt: str | None = None,
    parent_world_id: str | None = None,
) -> dict[str, Any]:
    """Create the initial record for one Agent generation request.

    This is not an extra workflow stage.  It gives the seven visible planning
    stages a stable document to update while the Agent progressively fills in
    the actual scene specification and artifact references.
    """

    world_id = new_world_id()
    timestamp = _now()
    title = (name or "Untitled scene").strip() or "Untitled scene"
    plan: dict[str, Any] = {
        "version": 1,
        "source": "worldclaw-paper",
        "stages": [
            {"id": stage_id, "status": "pending"}
            for stage_id in WORLDCLAW_STAGE_IDS
        ],
        "updated_at": timestamp,
    }
    if prompt and prompt.strip():
        plan["prompt"] = prompt.strip()

    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "polykit.world",
        "id": world_id,
        "world_id": world_id,
        "name": title,
        "created_at": timestamp,
        "updated_at": timestamp,
        # The Agent replaces this with the real WorldSpec during the plan
        # stage.  Keeping the field JSON-compatible makes the shell resumable
        # without inventing a second draft format.
        "spec": {},
        "instances": [],
        "artifacts": {},
        "agent_plan": plan,
    }
    if parent_world_id and parent_world_id.strip():
        result["parent_world_id"] = parent_world_id.strip()
    return result


def _normalise_artifacts(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        result: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, Mapping):
                raise WorldStoreError("World artifacts must be an object or a list of objects")
            artifact_id = item.get("id")
            if not isinstance(artifact_id, str) or not artifact_id.strip():
                raise WorldStoreError("List artifacts must have a non-empty id")
            result[artifact_id.strip()] = dict(item)
        return result
    raise WorldStoreError("World artifacts must be an object or a list of objects")


def attach_world_artifact(
    world: Mapping[str, Any],
    *,
    proto_id: str,
    workspace_path: str,
    workflow_id: str | None = None,
    run_id: str | None = None,
    concept_image: str | None = None,
) -> dict[str, Any]:
    """Return a world document with one generated mesh attached.

    Paths are validated here as well as by ``world_store`` so an Agent gets a
    useful error before the document is sent over HTTP.  The helper accepts
    the legacy artifact list shape and normalises it to the Web editor's
    prototype-id map.
    """

    safe_proto_id = _require_text(proto_id, "proto_id")
    safe_mesh_path = validate_workspace_relative_path(workspace_path)
    safe_concept_image = (
        validate_workspace_relative_path(concept_image) if concept_image else None
    )

    result = dict(world)
    artifacts = _normalise_artifacts(result.get("artifacts"))
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
    """Update one paper-derived Agent stage without executing anything."""

    safe_stage = _require_text(stage_id, "stage_id")
    if safe_stage not in WORLDCLAW_STAGE_IDS:
        raise WorldStoreError(
            f"Unknown world stage {safe_stage!r}; expected one of {', '.join(WORLDCLAW_STAGE_IDS)}"
        )
    safe_status = _require_text(status, "status")
    if safe_status not in WORLDCLAW_STAGE_STATUSES:
        raise WorldStoreError(
            f"Unknown world stage status {safe_status!r}; expected one of {', '.join(WORLDCLAW_STAGE_STATUSES)}"
        )

    result = dict(world)
    existing_plan = result.get("agent_plan")
    plan = dict(existing_plan) if isinstance(existing_plan, Mapping) else {}
    plan.setdefault("version", 1)
    plan.setdefault("source", "worldclaw-paper")
    if prompt and prompt.strip():
        plan["prompt"] = prompt.strip()

    raw_stages = plan.get("stages")
    stage_map: dict[str, dict[str, Any]] = {}
    if isinstance(raw_stages, Mapping):
        for key, value in raw_stages.items():
            if isinstance(key, str) and isinstance(value, Mapping):
                stage_map[key] = dict(value)
    elif isinstance(raw_stages, list):
        for value in raw_stages:
            if isinstance(value, Mapping) and isinstance(value.get("id"), str):
                stage_map[value["id"]] = dict(value)

    for known_stage in WORLDCLAW_STAGE_IDS:
        entry = stage_map.setdefault(known_stage, {"id": known_stage, "status": "pending"})
        entry.setdefault("id", known_stage)
        entry.setdefault("status", "pending")
    stage = stage_map[safe_stage]
    stage["status"] = safe_status
    if note is not None:
        stage["note"] = note.strip()
    stage["updated_at"] = _now()

    plan["stages"] = [stage_map[stage_name] for stage_name in WORLDCLAW_STAGE_IDS]
    plan["updated_at"] = _now()
    result["agent_plan"] = plan
    result["updated_at"] = _now()
    return result


__all__ = [
    "WORLDCLAW_STAGE_IDS",
    "WORLDCLAW_STAGE_STATUSES",
    "attach_world_artifact",
    "create_world_document",
    "update_world_stage",
]
