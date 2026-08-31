"""Operations on the strict schema-v2 world runtime envelope."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from services.world_store import WorldStoreError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime(world: Mapping[str, Any]) -> dict[str, Any]:
    runtime = world.get("runtime")
    if not isinstance(runtime, Mapping) or runtime.get("version") != 1:
        raise WorldStoreError("World requires runtime version 1")
    return dict(runtime)


def _bind_artifacts(plan: Mapping[str, Any], artifacts: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(plan)
    objects = result.get("objects")
    if not isinstance(objects, list):
        return result

    bound: list[Any] = []
    for value in objects:
        if not isinstance(value, Mapping):
            bound.append(value)
            continue
        obj = dict(value)
        object_id = obj.get("id")
        artifact = artifacts.get(object_id) if isinstance(object_id, str) else None
        mesh = artifact.get("mesh") if isinstance(artifact, Mapping) else None
        mesh_path = mesh.get("workspace_path") if isinstance(mesh, Mapping) else None
        if isinstance(mesh_path, str) and mesh_path.strip() and not obj.get("asset"):
            obj["asset"] = {
                "workspacePath": mesh_path.strip(),
                "source": "world-artifact",
                **({"runId": mesh["run_id"]} if isinstance(mesh.get("run_id"), str) else {}),
            }
        bound.append(obj)
    result["objects"] = bound
    return result


def attach_scene_plan_to_runtime(world: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Install one compiled ScenePlan as ``runtime.scene`` without mirrors."""

    if not isinstance(world, Mapping) or not isinstance(plan, Mapping):
        raise WorldStoreError("World and scene plan must be objects")
    result = dict(world)
    runtime = _runtime(result)
    artifacts = result.get("artifacts")
    artifact_map = artifacts if isinstance(artifacts, Mapping) else {}
    scene = _bind_artifacts(plan, artifact_map)
    runtime["scene"] = scene

    metadata = scene.get("metadata")
    quality = metadata.get("layoutQuality") if isinstance(metadata, Mapping) else None
    state = runtime.get("state")
    if isinstance(state, Mapping) and isinstance(quality, Mapping):
        state_copy = dict(state)
        gates = state_copy.get("gates")
        if isinstance(gates, Mapping):
            gate_copy = dict(gates)
            raw_status = quality.get("status")
            status = {
                "pass": "pass",
                "needs_review": "needs_review",
                "invalid": "fail",
            }.get(raw_status, "pending")
            issues: list[dict[str, Any]] = []
            diagnostics = scene.get("diagnostics")
            if isinstance(diagnostics, list):
                for index, diagnostic in enumerate(diagnostics):
                    if not isinstance(diagnostic, Mapping) or diagnostic.get("code") != "layout-quality":
                        continue
                    severity = str(diagnostic.get("severity") or "warning")
                    issues.append({
                        "id": f"scene-layout-{index}",
                        "code": "scene-layout",
                        "gate": "construction",
                        "severity": severity if severity in {"info", "warning", "error"} else "warning",
                        "message": str(diagnostic.get("message") or "Scene layout quality issue"),
                        **({"subjectId": diagnostic["object_id"]} if isinstance(diagnostic.get("object_id"), str) else {}),
                    })
            gate_copy["construction"] = {
                "status": status,
                "issues": issues,
                "checked_at": _now(),
            }
            state_copy["gates"] = gate_copy
            state_copy["updated_at"] = _now()
            runtime["state"] = state_copy

    result["runtime"] = runtime
    result["updated_at"] = _now()
    return result


__all__ = ["attach_scene_plan_to_runtime"]
