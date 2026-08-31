"""Derived operations for the strict schema-v2 world runtime."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
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


def _vector3(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 3:
        return None
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        result.append(number)
    return result[0], result[1], result[2]


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _normal_dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float | None:
    a_len = math.sqrt(sum(value * value for value in a))
    b_len = math.sqrt(sum(value * value for value in b))
    if a_len <= 1e-8 or b_len <= 1e-8:
        return None
    return sum(x * y for x, y in zip(a, b)) / (a_len * b_len)


def _issue(
    issue_id: str,
    code: str,
    severity: str,
    message: str,
    *,
    subject_id: str | None = None,
    measured: float | None = None,
    expected: float | str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": issue_id,
        "code": code,
        "gate": "construction",
        "severity": severity,
        "message": message,
    }
    if subject_id:
        value["subjectId"] = subject_id
    if measured is not None:
        value["measured"] = round(float(measured), 6)
    if expected is not None:
        value["expected"] = expected
    return value


def evaluate_build_attachments(build: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Validate BuildSpec attachment evidence without pretending missing evidence passed."""

    if not isinstance(build, Mapping) or build.get("kind") != "polykit.build-spec":
        return False, []
    buildings = build.get("buildings")
    if not isinstance(buildings, list) or not buildings:
        return False, []

    checked = False
    issues: list[dict[str, Any]] = []
    for building_index, raw_building in enumerate(buildings):
        if not isinstance(raw_building, Mapping):
            continue
        building_id = str(raw_building.get("id") or f"building-{building_index}")
        anchors: dict[str, Mapping[str, Any]] = {}
        raw_anchors = raw_building.get("anchors")
        if isinstance(raw_anchors, list):
            for anchor_index, raw_anchor in enumerate(raw_anchors):
                if not isinstance(raw_anchor, Mapping):
                    continue
                anchor_id = raw_anchor.get("id")
                if not isinstance(anchor_id, str) or not anchor_id:
                    continue
                if anchor_id in anchors:
                    issues.append(_issue(
                        f"{building_id}:anchor:{anchor_index}",
                        "duplicate-build-anchor",
                        "error",
                        f"Building '{building_id}' defines anchor '{anchor_id}' more than once.",
                        subject_id=building_id,
                    ))
                else:
                    anchors[anchor_id] = raw_anchor

        attachments = raw_building.get("attachments")
        if not isinstance(attachments, list) or not attachments:
            issues.append(_issue(
                f"{building_id}:attachments",
                "building-topology-unverified",
                "warning",
                f"Building '{building_id}' has no attachment topology to validate.",
                subject_id=building_id,
            ))
            continue

        checked = True
        for index, raw_attachment in enumerate(attachments):
            if not isinstance(raw_attachment, Mapping):
                continue
            attachment_id = str(raw_attachment.get("id") or f"attachment-{index}")
            prefix = f"{building_id}:{attachment_id}"
            from_id = raw_attachment.get("from")
            to_id = raw_attachment.get("to")
            source = anchors.get(from_id) if isinstance(from_id, str) else None
            target = anchors.get(to_id) if isinstance(to_id, str) else None
            mode = str(raw_attachment.get("mode") or "")
            try:
                tolerance = max(0.0, float(raw_attachment.get("tolerance", 0.02)))
            except (TypeError, ValueError):
                tolerance = 0.02

            if source is None or target is None:
                missing = from_id if source is None else to_id
                issues.append(_issue(
                    prefix,
                    "missing-build-anchor",
                    "error",
                    f"Attachment '{attachment_id}' references missing anchor '{missing}'.",
                    subject_id=building_id,
                ))
                continue

            source_position = _vector3(source.get("position"))
            target_position = _vector3(target.get("position"))
            if source_position is None or target_position is None:
                issues.append(_issue(
                    prefix,
                    "attachment-position-evidence-missing",
                    "warning",
                    f"Attachment '{attachment_id}' has no complete finite world-position evidence.",
                    subject_id=building_id,
                ))
                continue

            gap = _distance(source_position, target_position)
            if mode in {"support", "flush"}:
                if gap > tolerance:
                    issues.append(_issue(
                        prefix,
                        "attachment-gap",
                        "error",
                        f"Attachment '{attachment_id}' gap {gap:.4f} m exceeds tolerance {tolerance:.4f} m.",
                        subject_id=building_id,
                        measured=gap,
                        expected=f"<= {tolerance:.6f}",
                    ))
                if mode == "flush":
                    source_normal = _vector3(source.get("normal"))
                    target_normal = _vector3(target.get("normal"))
                    if source_normal is None or target_normal is None:
                        issues.append(_issue(
                            f"{prefix}:normal",
                            "attachment-normal-evidence-missing",
                            "warning",
                            f"Flush attachment '{attachment_id}' needs both surface normals.",
                            subject_id=building_id,
                        ))
                    else:
                        dot = _normal_dot(source_normal, target_normal)
                        if dot is None:
                            issues.append(_issue(
                                f"{prefix}:normal",
                                "attachment-normal-invalid",
                                "warning",
                                f"Flush attachment '{attachment_id}' contains a zero-length normal.",
                                subject_id=building_id,
                            ))
                        elif dot > -0.85:
                            issues.append(_issue(
                                f"{prefix}:normal",
                                "attachment-normal-mismatch",
                                "error",
                                f"Flush attachment '{attachment_id}' surfaces do not face each other.",
                                subject_id=building_id,
                                measured=dot,
                                expected="<= -0.85",
                            ))
                continue

            if mode in {"inside", "passes-through"}:
                issues.append(_issue(
                    prefix,
                    "attachment-volume-evidence-required",
                    "warning",
                    f"Attachment '{attachment_id}' mode '{mode}' requires geometry-volume evidence.",
                    subject_id=building_id,
                    measured=gap,
                ))
                continue

            issues.append(_issue(
                prefix,
                "unsupported-attachment-mode",
                "error",
                f"Attachment '{attachment_id}' uses unsupported mode '{mode}'.",
                subject_id=building_id,
            ))

    return checked, issues


def _scene_construction_evidence(scene: Any) -> tuple[bool, list[dict[str, Any]]]:
    if not isinstance(scene, Mapping):
        return False, []
    metadata = scene.get("metadata")
    quality = metadata.get("layoutQuality") if isinstance(metadata, Mapping) else None
    if not isinstance(quality, Mapping):
        return False, []

    issues: list[dict[str, Any]] = []
    diagnostics = scene.get("diagnostics")
    if isinstance(diagnostics, list):
        for index, diagnostic in enumerate(diagnostics):
            if not isinstance(diagnostic, Mapping) or diagnostic.get("code") != "layout-quality":
                continue
            severity = str(diagnostic.get("severity") or "warning")
            issues.append(_issue(
                f"scene-layout-{index}",
                "scene-layout",
                severity if severity in {"info", "warning", "error"} else "warning",
                str(diagnostic.get("message") or "Scene layout quality issue"),
                subject_id=diagnostic.get("object_id") if isinstance(diagnostic.get("object_id"), str) else None,
            ))
    raw_status = quality.get("status")
    if raw_status == "invalid" and not any(item["severity"] == "error" for item in issues):
        issues.append(_issue("scene-layout-status", "scene-layout-invalid", "error", "Scene layout quality is invalid."))
    elif raw_status == "needs_review" and not any(item["severity"] in {"warning", "error"} for item in issues):
        issues.append(_issue("scene-layout-status", "scene-layout-review", "warning", "Scene layout quality requires review."))
    return True, issues


def _gate_status(checked: bool, issues: list[dict[str, Any]]) -> str:
    if any(item.get("severity") == "error" for item in issues):
        return "fail"
    if any(item.get("severity") == "warning" for item in issues):
        return "needs_review"
    return "pass" if checked else "pending"


def refresh_runtime_quality(world: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute construction quality only when the runtime contains evidence."""

    result = dict(world)
    runtime = _runtime(result)
    build_checked, build_issues = evaluate_build_attachments(runtime.get("build"))
    scene_checked, scene_issues = _scene_construction_evidence(runtime.get("scene"))
    issues = [*build_issues, *scene_issues]
    checked = build_checked or scene_checked

    # An empty runtime has nothing to derive.  Returning it byte-for-byte stable
    # preserves ordinary PUT/GET round trips and avoids fake quality timestamps.
    if not checked and not issues:
        return result

    state = runtime.get("state")
    if not isinstance(state, Mapping):
        raise WorldStoreError("World runtime requires state")
    state_copy = dict(state)
    gates = state_copy.get("gates")
    if not isinstance(gates, Mapping):
        raise WorldStoreError("World runtime state requires gates")
    gate_copy = dict(gates)
    timestamp = _now()
    gate_copy["construction"] = {
        "status": _gate_status(checked, issues),
        "issues": issues,
        "checked_at": timestamp,
    }
    state_copy["gates"] = gate_copy
    state_copy["updated_at"] = timestamp
    runtime["state"] = state_copy
    result["runtime"] = runtime
    result["updated_at"] = timestamp
    return result


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
    """Install one compiled ScenePlan at ``runtime.scene`` without mirrors."""

    if not isinstance(world, Mapping) or not isinstance(plan, Mapping):
        raise WorldStoreError("World and scene plan must be objects")
    result = dict(world)
    runtime = _runtime(result)
    artifacts = result.get("artifacts")
    runtime["scene"] = _bind_artifacts(plan, artifacts if isinstance(artifacts, Mapping) else {})
    result["runtime"] = runtime
    return refresh_runtime_quality(result)


__all__ = ["attach_scene_plan_to_runtime", "evaluate_build_attachments", "refresh_runtime_quality"]
