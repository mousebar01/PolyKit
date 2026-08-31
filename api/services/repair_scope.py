"""Pure repair-scope derivation for World validation evidence.

Repair scopes are advisory facts. They describe the smallest trustworthy area a
caller may choose to rebuild or review after validation. They never schedule a
retry, mutate World state, or advance a WorkflowRun.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


REPAIR_SCOPE_KIND = "polykit.repair-scope"
REPAIR_SCOPE_SCHEMA_VERSION = 1
_UNRESOLVED_CHECK_STATUSES = {"fail", "needs_review", "not_evaluated"}
_TOKEN_RE = re.compile(r"[^a-zA-Z0-9._:-]+")


def _ids(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if item and item not in result:
            result.append(item)
    return result


def _stable_id(value: str) -> str:
    cleaned = _TOKEN_RE.sub("-", value.strip()).strip("-")
    return cleaned or "validation"


def _check_map(checks: Sequence[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in checks or []:
        check_id = item.get("id")
        if isinstance(check_id, str) and check_id and check_id not in result:
            result[check_id] = item
    return result


def _parts_from_metrics(metrics: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("source_part", "target_part", "world_object_id"):
        value = metrics.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in result:
            result.append(value.strip())
    return result


def _classification(
    capability: str,
    source_id: str,
    *,
    check: Mapping[str, Any] | None,
    issue: Mapping[str, Any] | None,
) -> dict[str, Any]:
    check = check or {}
    issue = issue or {}
    category = str(check.get("category") or "").strip().lower()
    judge = str(check.get("judge") or "").strip().lower()
    code = source_id.lower()
    metrics = check.get("metrics") if isinstance(check.get("metrics"), Mapping) else {}
    subjects = _ids(check.get("subjects"))
    subject_id = issue.get("subject_id")
    if isinstance(subject_id, str) and subject_id.strip() and subject_id.strip() not in subjects:
        subjects.append(subject_id.strip())

    object_ids = _parts_from_metrics(metrics)
    relationship_ids: list[str] = []
    locality = "scene"
    causal_system = "domain"
    action_hint = "review_validation_evidence"
    safe_to_localize = False

    if "attachment" in code:
        locality = "relationship"
        causal_system = "construction_geometry"
        action_hint = "repair_relationship_geometry"
        safe_to_localize = True
        if subjects:
            relationship_ids.append(subjects[-1])
        if not object_ids:
            object_ids.extend(subjects[:-1])
    elif "line-of-sight" in code or "visibility" in code or "occlud" in code:
        locality = "object" if subjects else "scene"
        causal_system = "occlusion_geometry"
        action_hint = "adjust_occluder_or_target_transform"
        safe_to_localize = bool(subjects)
        object_ids.extend(item for item in subjects if item not in object_ids)
    elif "frustum" in code or "camera" in code or category in {"camera", "frame"}:
        locality = "object" if subjects else "scene"
        causal_system = "camera_composition"
        action_hint = "adjust_camera_or_object_transform"
        safe_to_localize = bool(subjects)
        object_ids.extend(item for item in subjects if item not in object_ids)
    elif "world-object" in code or "scene-plan" in code or "layout" in code or "blockout" in code:
        locality = "object" if subjects and "world-object" in code else "scene"
        causal_system = "scene_layout"
        action_hint = "restore_object_or_recompile_layout" if locality == "object" else "recompile_scene_layout"
        safe_to_localize = locality == "object"
        if locality == "object":
            object_ids.extend(item for item in subjects if item not in object_ids)
    elif any(token in code for token in ("evidence", "report-missing", "report-unreadable", "run-missing", "run-incomplete", "final-mesh")):
        locality = "evidence"
        causal_system = "evidence_pipeline"
        action_hint = "regenerate_or_attach_evidence"
    elif category in {"silhouette", "negative_space", "luminance", "material", "lighting", "color", "surface"} or judge == "metric":
        locality = "object" if subjects else "scene"
        causal_system = category or "visual_metrics"
        action_hint = "adjust_visual_system_or_subject"
        safe_to_localize = bool(subjects)
    elif category == "semantic" or judge == "semantic" or "semantic" in code:
        locality = "object" if subjects else "scene"
        causal_system = "semantic_match"
        action_hint = "review_or_rebuild_semantic_subject"
        safe_to_localize = bool(subjects)
    elif "game" in capability or any(token in code for token in ("interaction", "objective", "game-spec")):
        locality = "object" if subjects else "scene"
        causal_system = "game_spec"
        action_hint = "repair_gameplay_reference"
        safe_to_localize = bool(subjects)
    elif "construction" in capability or "construction" in code or "building" in code:
        locality = "object" if subjects else "scene"
        causal_system = "construction"
        action_hint = "repair_construction_part" if subjects else "rebuild_construction_evidence"
        safe_to_localize = bool(subjects)
    elif "spec" in capability or any(token in code for token in ("build-spec", "world-intent", "game-spec")):
        locality = "scene"
        causal_system = "world_spec"
        action_hint = "repair_world_spec"

    object_ids = _ids(object_ids)
    relationship_ids = _ids(relationship_ids)
    return {
        "locality": locality,
        "causal_system": causal_system,
        "affected_object_ids": object_ids,
        "affected_relationship_ids": relationship_ids,
        "affected_subject_ids": subjects,
        "action_hint": action_hint,
        "safe_to_localize": safe_to_localize,
    }


def _scope(
    capability: str,
    source_id: str,
    source_status: str,
    reason: str,
    *,
    check: Mapping[str, Any] | None = None,
    issue: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    classified = _classification(capability, source_id, check=check, issue=issue)
    source: dict[str, Any] = {"capability": capability}
    if check is not None:
        source["check_id"] = source_id
    else:
        source["issue_code"] = source_id
    return {
        "schema_version": REPAIR_SCOPE_SCHEMA_VERSION,
        "kind": REPAIR_SCOPE_KIND,
        "id": f"repair:{_stable_id(capability)}:{_stable_id(source_id)}",
        "source": source,
        "source_status": source_status,
        "locality": classified["locality"],
        "causal_system": classified["causal_system"],
        "affected_object_ids": classified["affected_object_ids"],
        "affected_relationship_ids": classified["affected_relationship_ids"],
        "affected_subject_ids": classified["affected_subject_ids"],
        "action_hint": classified["action_hint"],
        "safe_to_localize": classified["safe_to_localize"],
        "reason": reason,
    }


def derive_repair_scopes(
    capability: str,
    issues: Sequence[Mapping[str, Any]],
    *,
    checks: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Derive deterministic advisory scopes from unresolved checks and issues."""

    issue_by_code = {
        str(item.get("code")): item
        for item in issues
        if isinstance(item.get("code"), str) and item.get("code")
    }
    check_by_id = _check_map(checks)
    scopes: list[dict[str, Any]] = []
    represented: set[str] = set()

    for check_id, check in check_by_id.items():
        if check.get("required") is not True or check.get("status") not in _UNRESOLVED_CHECK_STATUSES:
            continue
        issue = issue_by_code.get(check_id)
        reason = str(check.get("message") or (issue or {}).get("message") or "Validation is unresolved.")
        scopes.append(
            _scope(
                capability,
                check_id,
                str(check.get("status")),
                reason,
                check=check,
                issue=issue,
            )
        )
        represented.add(check_id)

    for issue in issues:
        code = issue.get("code")
        if not isinstance(code, str) or not code or code in represented:
            continue
        severity = str(issue.get("severity") or "warning")
        if severity not in {"warning", "error"}:
            continue
        scopes.append(
            _scope(
                capability,
                code,
                "fail" if severity == "error" else "needs_review",
                str(issue.get("message") or "Validation is unresolved."),
                issue=issue,
            )
        )

    return scopes


def attach_repair_scope(
    failure: Mapping[str, Any] | None,
    scopes: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Attach the matching repair scope to an earliest-failure summary."""

    if not isinstance(failure, Mapping):
        return None
    result = dict(failure)
    check_id = failure.get("check_id")
    if not isinstance(check_id, str) or not check_id:
        return result
    for scope in scopes:
        source = scope.get("source")
        if isinstance(source, Mapping) and source.get("check_id") == check_id:
            result["repair_scope_id"] = scope.get("id")
            result["repair_scope"] = dict(scope)
            break
    return result


__all__ = [
    "REPAIR_SCOPE_KIND",
    "REPAIR_SCOPE_SCHEMA_VERSION",
    "attach_repair_scope",
    "derive_repair_scopes",
]
