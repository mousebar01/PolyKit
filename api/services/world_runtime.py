"""Operations on the strict schema-v2 world runtime envelope.

The construction gate is derived data.  Every writer supplies declarative
``runtime.build`` / ``runtime.scene`` data; this module evaluates the available
geometric evidence and owns the resulting gate state.
"""
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


def _distance(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def _normal_dot(first: tuple[float, float, float], second: tuple[float, float, float]) -> float | None:
    first_length = math.sqrt(sum(value * value for value in first))
    second_length = math.sqrt(sum(value * value for value in second))
    if first_length <= 1e-8 or second_length <= 1e-8:
        return None
    return sum(a * b for a, b in zip(first, second)) / (first_length * second_length)


def _issue(
    *,
    issue_id: str,
    code: str,
    severity: str,
    message: str,
    subject_id: str | None = None,
    measured: float | None = None,
    expected: float | str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": issue_id,
        "code": code,
        "gate": "construction",
        "severity": severity,
        "message": message,
    }
    if subject_id:
        result["subjectId"] = subject_id
    if measured is not None:
        result["measured"] = round(float(measured), 6)
    if expected is not None:
        result["expected"] = expected
    return result


def evaluate_build_attachments(build: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Evaluate attachment evidence contained in one ``BuildSpec``.

    ``flush`` and ``support`` can be checked from anchor positions.  Flush also
    validates opposing normals when they are authored.  Volume relations such
    as ``inside`` and ``passes-through`` deliberately remain ``needs_review``
    until a geometry backend supplies stronger evidence; they never silently
    pass from a point anchor alone.

    Returns ``(checked, issues)``.  ``checked`` is false when no building
    topology exists, which keeps the gate pending rather than manufacturing a
    successful construction result from an empty spec.
    """

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
        raw_anchors = raw_building.get("anchors")
        raw_attachments = raw_building.get("attachments")
        anchors: dict[str, Mapping[str, Any]] = {}
        if isinstance(raw_anchors, list):
            for anchor_index, raw_anchor in enumerate(raw_anchors):
                if not isinstance(raw_anchor, Mapping):
                    continue
                anchor_id = raw_anchor.get("id")
                if not isinstance(anchor_id, str) or not anchor_id:
                    continue
                if anchor_id in anchors:
                    issues.append(_issue(
                        issue_id=f"{building_id}:anchor:{anchor_index}",
                        code="duplicate-build-anchor",
                        severity="error",
                        message=f"Building '{building_id}' defines anchor '{anchor_id}' more than once.",
                        subject_id=building_id,
                    ))
                    continue
                anchors[anchor_id] = raw_anchor

        if not isinstance(raw_attachments, list) or not raw_attachments:
            issues.append(_issue(
                issue_id=f"{building_id}:attachments",
                code="building-topology-unverified",
                severity="warning",
                message=f"Building '{building_id}' has no attachment topology to validate.",
                subject_id=building_id,
            ))
            continue

        checked = True
        for attachment_index, raw_attachment in enumerate(raw_attachments):
            if not isinstance(raw_attachment, Mapping):
                continue
            attachment_id = str(raw_attachment.get("id") or f"attachment-{attachment_index}")
            issue_prefix = f"{building_id}:{attachment_id}"
            from_id = raw_attachment.get("from")
            to_id = raw_attachment.get("to")
            mode = str(raw_attachment.get("mode") or "")
            try:
                tolerance = max(0.0, float(raw_attachment.get("tolerance", 0.02)))
            except (TypeError, ValueError):
                tolerance = 0.02

            source = anchors.get(from_id) if isinstance(from_id, str) else None
            target = anchors.get(to_id) if isinstance(to_id, str) else None
            if source is None or target is None:
                missing = from_id if source is None else to_id
                issues.append(_issue(
                    issue_id=issue_prefix,
                    code="missing-build-anchor",
                    severity="error",
                    message=f"Attachment '{attachment_id}' references missing anchor '{missing}'.",
                    subject_id=building_id,
                ))
                continue

            source_position = _vector3(source.get("position"))
            target_position = _vector3(target.get("position"))
            if source_position is None or target_position is None:
                issues.append(_issue(
                    issue_id=issue_prefix,
                    code="attachment-position-evidence-missing",
                    severity="warning",
                    message=(
                        f"Attachment '{attachment_id}' cannot be checked because one or both anchors "
                        "have no finite world position."
                    ),
                    subject_id=building_id,
                ))
                continue

            gap = _distance(source_position, target_position)
            if mode in {"flush", "support"}:
                if gap > tolerance:
                    issues.append(_issue(
                        issue_id=issue_prefix,
                        code="attachment-gap",
                        severity="error",
                        message=(
                            f"Attachment '{attachment_id}' has a {gap:.4f} m contact gap, "
                            f"above its {tolerance:.4f} m tolerance."
                        ),
                        subject_id=building_id,
                        measured=gap,
                        expected=f"<= {tolerance:.6f}",
                    ))
                if mode == "flush":
                    source_normal = _vector3(source.get("normal"))
                    target_normal = _vector3(target.get("normal"))
                    if source_normal is None or target_normal is None:
                        issues.append(_issue(
                            issue_id=f"{issue_prefix}:normal",
                            code="attachment-normal-evidence-missing",
                            severity="warning",
                            message=(
                                f"Flush attachment '{attachment_id}' has no complete normal evidence; "
                                "position contact was checked but surface orientation still needs review."
                            ),
                            subject_id=building_id,
                        ))
                    else:
                        dot = _normal_dot(source_normal, target_normal)
                        if dot is None:
                            issues.append(_issue(
                                issue_id=f"{issue_prefix}:normal",
                                code="attachment-normal-invalid",
                                severity="warning",
                                message=f"Flush attachment '{attachment_id}' contains a zero-length normal.",
                                subject_id=building_id,
                            ))
                        elif dot > -0.85:
                            issues.append(_issue(
                                issue_id=f"{issue_prefix}:normal",
                                code="attachment-normal-mismatch",
                                severity="error",
                                message=(
                                    f"Flush attachment '{attachment_id}' surfaces do not face each other "
                                    f"(normal dot {dot:.3f})."
                                ),
                                subject_id=building_id,
                                measured=dot,
                                expected="<= -0.85",
                            ))
                continue

            if mode in {"inside", "passes-through"}:
                issues.append(_issue(
                    issue_id=issue_prefix,
                    code="attachment-volume-evidence-required",
                    severity="warning",
                    message=(
                        f"Attachment '{attachment_id}' uses '{mode}', which requires geometry-volume "
                        "evidence from the build backend; point anchors alone cannot prove it."
                    ),
                    subject_id=building_id,
                    measured=gap,
                ))
                continue

            issues.append(_issue(
                issue_id=issue_prefix,
                code="unsupported-attachment-mode",
                severity="error",
                message=f"Attachment '{attachment_id}' uses unsupported mode '{mode}'.",
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
                issue_id=f"scene-layout-{index}",
                code="scene-layout",
                severity=severity if severity in {"info", "warning", "error"} else "warning",
                message=str(diagnostic.get("message") or "Scene layout quality issue"),
                subject_id=diagnostic.get("object_id") if isinstance(diagnostic.get("object_id"), str) else None,
            ))
    raw_status = quality.get("status")
    if raw_status == "invalid" and not any(item["severity"] == "error" for item in issues):
        issues.append(_issue(
            issue_id="scene-layout-status",
            code="scene-layout-invalid",
            severity="error",
            message="Scene layout quality is invalid.",
        ))
    elif raw_status == "needs_review" and not any(item["severity"] in {"warning", "error"} for item in issues):
        issues.append(_issue(
            issue_id="scene-layout-status",
            code="scene-layout-review",
            severity="warning",
            message="Scene layout quality requires review.",
        ))
    return True, issues


def _gate_status(checked: bool, issues: list[dict[str, Any]]) -> str:
    if any(item.get("severity") == "error" for item in issues):
        return "fail"
    if any(item.get("severity") == "warning" for item in issues):
        return "needs_review"
    return "pass" if checked else "pending"


def refresh_runtime_quality(world: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute derived runtime quality gates from current specs/evidence."""

    result = dict(world)
    runtime = _runtime(result)
    build_checked, build_issues = evaluate_build_attachments(runtime.get("build"))
    scene_checked, scene_issues = _scene_construction_evidence(runtime.get("scene"))
    issues = [*build_issues, *scene_issues]
    checked = build_checked or scene_checked

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
        "checked_at": timestamp if checked or issues else None,
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
    """Install one compiled ScenePlan as ``runtime.scene`` without mirrors."""

    if not isinstance(world, Mapping) or not isinstance(plan, Mapping):
        raise WorldStoreError("World and scene plan must be objects")
    result = dict(world)
    runtime = _runtime(result)
    artifacts = result.get("artifacts")
    artifact_map = artifacts if isinstance(artifacts, Mapping) else {}
    runtime["scene"] = _bind_artifacts(plan, artifact_map)
    result["runtime"] = runtime
    return refresh_runtime_quality(result)


__all__ = [
    "attach_scene_plan_to_runtime",
    "evaluate_build_attachments",
    "refresh_runtime_quality",
]
