"""Deterministic validation for schema-v2 world domain state.

Validators inspect WorldDocument data and, where relevant, completed Workflow Run
evidence. They report facts only: pass/needs_review/fail, issues, details and an
evidence reference. They do not choose conversational actions or mutate task state.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from services.repair_scope import attach_repair_scope, derive_repair_scopes
from services.runtime_paths import runtime_paths
from services.spatial_validation import build_world_spatial_bundle
from services.visual_validation import (
    load_visual_validation_report,
    validate_visual_validation_report,
)
from services.world_runtime import refresh_runtime_quality
from services.world_store import WorldStoreError


WORLD_VALIDATION_CAPABILITIES = {
    "world.spec.validate",
    "world.blockout.validate",
    "world.construction.validate",
    "world.spatial.validate",
    "world.visual.validate",
    "world.gameplay.validate",
    "world.final.validate",
}


def _runtime(world: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = world.get("runtime")
    if not isinstance(runtime, Mapping) or runtime.get("version") != 1:
        raise WorldStoreError("World requires runtime version 1")
    return runtime


def _issue(code: str, severity: str, message: str, *, subject_id: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if subject_id:
        value["subject_id"] = subject_id
    return value


def _status(issues: list[dict[str, Any]]) -> str:
    if any(item.get("severity") == "error" for item in issues):
        return "fail"
    if any(item.get("severity") == "warning" for item in issues):
        return "needs_review"
    return "pass"


def _report(
    world_id: str,
    capability: str,
    evidence_kind: str,
    issues: list[dict[str, Any]],
    *,
    ref_suffix: str,
    details: Mapping[str, Any] | None = None,
    repair_checks: Sequence[Mapping[str, Any]] | None = None,
    repair_scopes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    status = _status(issues)
    summary = f"{capability}: {status}"
    if issues:
        summary += f" ({len(issues)} issue{'s' if len(issues) != 1 else ''})"

    scopes = (
        [dict(item) for item in repair_scopes if isinstance(item, Mapping)]
        if repair_scopes is not None
        else derive_repair_scopes(capability, issues, checks=repair_checks)
    )
    details_value = dict(details or {})
    if isinstance(details_value.get("earliest_failure"), Mapping):
        details_value["earliest_failure"] = attach_repair_scope(
            details_value["earliest_failure"],
            scopes,
        )

    return {
        "world_id": world_id,
        "capability": capability,
        "status": status,
        "issues": issues,
        "repair_scopes": scopes,
        "details": details_value,
        "evidence": {
            "kind": evidence_kind,
            "ref": f"world://{world_id}/{ref_suffix.lstrip('/')}",
            "summary": summary,
        },
    }


def _scene_objects(runtime: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, set[str]]:
    scene = runtime.get("scene")
    if not isinstance(scene, Mapping):
        return None, set()
    raw_objects = scene.get("objects")
    if not isinstance(raw_objects, list):
        return scene, set()
    return scene, {
        str(item.get("id"))
        for item in raw_objects
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }


def _validate_spec(world_id: str, world: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _runtime(world)
    issues: list[dict[str, Any]] = []

    intent = runtime.get("intent")
    prompt = intent.get("prompt") if isinstance(intent, Mapping) else None
    if not isinstance(prompt, str) or not prompt.strip():
        issues.append(_issue("world-intent-missing", "error", "World intent prompt is empty."))

    build = runtime.get("build")
    if not isinstance(build, Mapping) or build.get("kind") != "polykit.build-spec":
        issues.append(_issue("build-spec-missing", "error", "World has no valid BuildSpec."))

    scene, object_ids = _scene_objects(runtime)
    if scene is None or not object_ids:
        issues.append(_issue("scene-plan-missing", "error", "World needs a compiled ScenePlan with semantic objects."))

    game = runtime.get("game")
    if not isinstance(game, Mapping) or game.get("kind") != "polykit.game-spec":
        issues.append(_issue("game-spec-missing", "error", "World has no valid GameSpec."))

    return _report(
        world_id,
        "world.spec.validate",
        "spec-validation",
        issues,
        ref_suffix="runtime",
    )


def _validate_blockout(world_id: str, world: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _runtime(world)
    scene, object_ids = _scene_objects(runtime)
    issues: list[dict[str, Any]] = []
    if scene is None or not object_ids:
        issues.append(_issue("blockout-missing", "error", "No compiled scene blockout is available."))
    else:
        metadata = scene.get("metadata")
        layout = metadata.get("layoutQuality") if isinstance(metadata, Mapping) else None
        layout_status = layout.get("status") if isinstance(layout, Mapping) else None
        if layout_status == "invalid":
            issues.append(_issue("layout-invalid", "error", "Scene layout quality is invalid."))
        elif layout_status == "needs_review":
            issues.append(_issue("layout-needs-review", "warning", "Scene layout quality needs review."))
        elif layout_status not in {"pass", "valid"}:
            issues.append(_issue("layout-evidence-missing", "warning", "Scene has no passing layout-quality evidence."))

    return _report(
        world_id,
        "world.blockout.validate",
        "blockout-report",
        issues,
        ref_suffix="runtime/scene",
    )


def _workflow_metadata(run: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(run, Mapping):
        return None
    meta = run.get("meta")
    if not isinstance(meta, Mapping):
        return None
    nested = meta.get("workflow_metadata")
    return nested if isinstance(nested, Mapping) else None


def _construction_process_evidence(run: Mapping[str, Any] | None) -> tuple[str | None, Mapping[str, Any] | None]:
    """Return Blender construction evidence persisted by a completed run."""
    if not isinstance(run, Mapping):
        return None, None
    meta = run.get("meta")
    if not isinstance(meta, Mapping):
        return None, None
    process_metadata = meta.get("process_metadata")
    if not isinstance(process_metadata, Mapping):
        return None, None
    for raw_node_id, raw_metadata in process_metadata.items():
        if not isinstance(raw_metadata, Mapping):
            continue
        validation = raw_metadata.get("constructionValidation")
        if isinstance(validation, Mapping):
            return str(raw_node_id), validation
    return None, None


def _validate_construction(
    world_id: str,
    world: Mapping[str, Any],
    run: Mapping[str, Any] | None,
) -> dict[str, Any]:
    derived = refresh_runtime_quality(world)
    runtime = _runtime(derived)
    quality = runtime.get("quality")
    construction = quality.get("construction") if isinstance(quality, Mapping) else None
    issues: list[dict[str, Any]] = []

    domain_status = construction.get("status") if isinstance(construction, Mapping) else "pending"
    if isinstance(construction, Mapping):
        raw_issues = construction.get("issues")
        if isinstance(raw_issues, list):
            issues.extend(dict(item) for item in raw_issues if isinstance(item, Mapping))
    if domain_status == "pending":
        issues.append(_issue("construction-domain-evidence-missing", "error", "BuildSpec has no validated construction evidence."))

    run_metadata = _workflow_metadata(run)
    run_status = run.get("status") if isinstance(run, Mapping) else None
    run_recipe = run_metadata.get("workflow_recipe") if isinstance(run_metadata, Mapping) else None
    run_world_id = run_metadata.get("world_id") if isinstance(run_metadata, Mapping) else None
    evidence_node_id, blender_validation = _construction_process_evidence(run)
    if run_status != "done":
        issues.append(_issue("construction-run-missing", "error", "A completed building-construction Workflow Run is required."))
    elif run_recipe != "building-construction" or run_world_id != world_id:
        issues.append(_issue("construction-run-mismatch", "error", "Workflow Run does not prove construction for this world."))
    else:
        if blender_validation is None:
            issues.append(_issue(
                "construction-run-evidence-missing",
                "error",
                "The completed run has no Blender construction-validation evidence.",
            ))
        elif str(blender_validation.get("status") or "").lower() != "pass":
            issues.append(_issue(
                "construction-run-evidence-failed",
                "error",
                "Blender construction validation did not pass for the completed run.",
                subject_id=evidence_node_id,
            ))

    build = runtime.get("build")
    buildings = build.get("buildings") if isinstance(build, Mapping) else None
    if not isinstance(buildings, list) or not buildings:
        issues.append(_issue("building-spec-missing", "error", "BuildSpec has no building to validate."))

    return _report(
        world_id,
        "world.construction.validate",
        "construction-report",
        issues,
        ref_suffix="runtime/quality/construction",
        details={
            "domain_status": domain_status,
            "run_id": run.get("run_id") if isinstance(run, Mapping) else None,
            "run_status": run_status,
            "blender_validation_status": (
                blender_validation.get("status")
                if blender_validation is not None
                else None
            ),
            "blender_validation_node_id": evidence_node_id,
        },
    )


def _spatial_issues(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    checks = bundle.get("checks")
    if not isinstance(checks, list):
        return [_issue("spatial-checks-missing", "warning", "Spatial geometry checks are unavailable.")]
    for item in checks:
        if not isinstance(item, Mapping):
            continue
        status = item.get("status")
        if status == "pass" or item.get("required") is not True:
            continue
        check_id = str(item.get("id") or "spatial-check")
        subjects = item.get("subjects")
        subject_id = str(subjects[0]) if isinstance(subjects, list) and subjects else None
        if status == "fail":
            severity = "error"
        else:
            severity = "warning"
        issues.append(_issue(
            check_id,
            severity,
            str(item.get("message") or "Spatial validation is unresolved."),
            subject_id=subject_id,
        ))
    return issues


def _validate_spatial(
    world_id: str,
    world: Mapping[str, Any],
    run: Mapping[str, Any] | None,
    *,
    target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bundle = build_world_spatial_bundle(
        world_id,
        world,
        run,
        target=target,
        workspace_root=runtime_paths.workspace,
    )
    checks = bundle.get("checks")
    repair_checks = checks if isinstance(checks, list) else None
    return _report(
        world_id,
        "world.spatial.validate",
        "spatial-validation-bundle",
        _spatial_issues(bundle),
        ref_suffix="runtime/quality/spatial",
        repair_checks=repair_checks,
        details={
            "bundle_status": bundle.get("status"),
            "run_id": bundle.get("run_id"),
            "checks": bundle.get("checks", []),
            "snapshot": bundle.get("snapshot"),
        },
    )


def _visual_report_reference(
    world: Mapping[str, Any],
    run: Mapping[str, Any] | None,
) -> tuple[Any | None, str | None, str]:
    runtime = _runtime(world)
    quality = runtime.get("quality")
    visual = quality.get("visual") if isinstance(quality, Mapping) else None
    if isinstance(visual, Mapping):
        value = visual.get("report_ref") or visual.get("report")
        if value:
            return value, None, "world-quality"

    metadata = _workflow_metadata(run)
    if isinstance(metadata, Mapping):
        value = metadata.get("visual_validation_report")
        if value:
            expected_run_id = run.get("run_id") if isinstance(run, Mapping) and isinstance(run.get("run_id"), str) else None
            return value, expected_run_id, "workflow-run"
    return None, None, "missing"


def _validate_visual(
    world_id: str,
    world: Mapping[str, Any],
    run: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    report_ref, expected_run_id, source = _visual_report_reference(world, run)
    run_status = run.get("status") if isinstance(run, Mapping) else None
    metadata = _workflow_metadata(run)

    if source == "workflow-run":
        if run_status != "done":
            issues.append(_issue(
                "visual-run-incomplete",
                "error",
                "Visual validation evidence must come from a completed Workflow Run.",
            ))
        run_world_id = metadata.get("world_id") if isinstance(metadata, Mapping) else None
        if run_world_id != world_id:
            issues.append(_issue(
                "visual-run-world-mismatch",
                "error",
                "Workflow Run visual evidence does not belong to this world.",
            ))

    if report_ref is None:
        issues.append(_issue(
            "visual-report-missing",
            "warning",
            "Visual validation needs an explicit VisualValidationReport evidence artifact before it can pass.",
        ))
        return _report(
            world_id,
            "world.visual.validate",
            "visual-validation-report",
            issues,
            ref_suffix="runtime/quality/visual",
            details={"report_source": source, "run_id": expected_run_id},
        )

    validation: dict[str, Any] | None = None
    spatial: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    try:
        report = load_visual_validation_report(report_ref, workspace_root=runtime_paths.workspace)
        validation = validate_visual_validation_report(
            report,
            world_id=world_id,
            run_id=expected_run_id,
            workspace_root=runtime_paths.workspace,
        )
        target = report.get("target")
        if isinstance(target, Mapping) and bool(target.get("require_spatial")):
            spatial = build_world_spatial_bundle(
                world_id,
                world,
                run,
                target=target,
                workspace_root=runtime_paths.workspace,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        issues.append(_issue(
            "visual-report-unreadable",
            "error",
            f"Visual validation report could not be read: {exc}",
        ))
    except Exception as exc:  # keep validation failures fail-closed at the domain boundary
        issues.append(_issue(
            "visual-report-invalid",
            "error",
            f"Visual validation report is invalid: {exc}",
        ))

    if validation is not None:
        for raw_issue in validation.get("issues", []):
            if not isinstance(raw_issue, Mapping):
                continue
            severity = str(raw_issue.get("severity") or "warning")
            issues.append(_issue(
                str(raw_issue.get("code") or "visual-report-issue"),
                severity if severity in {"warning", "error"} else "warning",
                str(raw_issue.get("message") or "Visual validation issue"),
                subject_id=raw_issue.get("subject_id") if isinstance(raw_issue.get("subject_id"), str) else None,
            ))
        if validation.get("status") == "fail" and not any(item.get("severity") == "error" for item in issues):
            issues.append(_issue(
                "visual-validation-failed",
                "error",
                "Required visual checks failed.",
            ))
        elif validation.get("status") == "needs_review" and not any(item.get("severity") in {"warning", "error"} for item in issues):
            issues.append(_issue(
                "visual-validation-needs-review",
                "warning",
                "Visual validation has unresolved required checks.",
            ))

    if spatial is not None:
        issues.extend(_spatial_issues(spatial))

    repair_checks: list[Mapping[str, Any]] = []
    if validation is not None and isinstance(validation.get("checks"), list):
        repair_checks.extend(item for item in validation["checks"] if isinstance(item, Mapping))
    if spatial is not None and isinstance(spatial.get("checks"), list):
        repair_checks.extend(item for item in spatial["checks"] if isinstance(item, Mapping))

    return _report(
        world_id,
        "world.visual.validate",
        "visual-validation-report",
        issues,
        ref_suffix="runtime/quality/visual",
        repair_checks=repair_checks,
        details={
            "report_source": source,
            "run_id": expected_run_id,
            "validation_status": validation.get("status") if validation else "fail",
            "summary": validation.get("summary", {}) if validation else {},
            "earliest_failure": validation.get("earliest_failure") if validation else None,
            "authoritative_spatial_status": spatial.get("status") if spatial else "not_applicable",
            "authoritative_spatial_checks": spatial.get("checks", []) if spatial else [],
            "spatial_snapshot": spatial.get("snapshot") if spatial else None,
        },
    )


def _gameplay_issues(runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    scene, object_ids = _scene_objects(runtime)
    if scene is None or not object_ids:
        issues.append(_issue("gameplay-scene-missing", "error", "Gameplay validation requires a compiled scene."))

    game = runtime.get("game")
    if not isinstance(game, Mapping):
        return [*issues, _issue("game-spec-missing", "error", "Gameplay validation requires GameSpec.")]

    player = game.get("player")
    if not isinstance(player, Mapping) or not isinstance(player.get("spawn"), Mapping):
        issues.append(_issue("player-spawn-missing", "error", "GameSpec player needs a spawn contract."))

    interactions = game.get("interactions")
    interaction_ids: set[str] = set()
    if isinstance(interactions, list):
        for index, item in enumerate(interactions):
            if not isinstance(item, Mapping):
                continue
            interaction_id = item.get("id")
            if isinstance(interaction_id, str):
                interaction_ids.add(interaction_id)
            object_id = item.get("objectId")
            if not isinstance(object_id, str) or object_id not in object_ids:
                issues.append(_issue(
                    "interaction-target-missing",
                    "error",
                    f"Interaction {interaction_id or index!r} references a missing scene object.",
                    subject_id=str(interaction_id or index),
                ))

    objectives = game.get("objectives")
    if not isinstance(objectives, list) or not objectives:
        issues.append(_issue("objective-missing", "warning", "Playable world output needs at least one objective."))
    else:
        known_targets = object_ids | interaction_ids
        for index, item in enumerate(objectives):
            if not isinstance(item, Mapping):
                continue
            target_id = item.get("targetId")
            objective_id = item.get("id")
            if isinstance(target_id, str) and target_id and target_id not in known_targets:
                issues.append(_issue(
                    "objective-target-missing",
                    "error",
                    f"Objective {objective_id or index!r} references unknown target '{target_id}'.",
                    subject_id=str(objective_id or index),
                ))
    return issues


def _validate_gameplay(world_id: str, world: Mapping[str, Any]) -> dict[str, Any]:
    return _report(
        world_id,
        "world.gameplay.validate",
        "gameplay-report",
        _gameplay_issues(_runtime(world)),
        ref_suffix="runtime/game",
    )


def _validate_final(
    world_id: str,
    world: Mapping[str, Any],
    run: Mapping[str, Any] | None,
) -> dict[str, Any]:
    construction = _validate_construction(world_id, world, run)
    visual = _validate_visual(world_id, world, run)
    gameplay = _validate_gameplay(world_id, world)

    issues: list[dict[str, Any]] = []
    if construction["status"] != "pass":
        issues.append(_issue("final-construction-not-pass", "error", "Construction validation has not passed."))
    if visual["status"] == "fail":
        issues.append(_issue("final-visual-failed", "error", "Visual validation failed."))
    elif visual["status"] != "pass":
        issues.append(_issue(
            "final-visual-evidence-missing",
            "warning",
            "Final validation cannot pass until visual quality has explicit passing evidence.",
        ))
    if gameplay["status"] != "pass":
        issues.append(_issue("final-gameplay-not-pass", "error", "Gameplay validation has not passed."))

    inherited_scopes: list[Mapping[str, Any]] = []
    for child in (construction, visual, gameplay):
        raw_scopes = child.get("repair_scopes")
        if isinstance(raw_scopes, list):
            inherited_scopes.extend(item for item in raw_scopes if isinstance(item, Mapping))

    return _report(
        world_id,
        "world.final.validate",
        "final-report",
        issues,
        ref_suffix="runtime/quality",
        repair_scopes=inherited_scopes or None,
        details={
            "construction": construction["status"],
            "visual": visual["status"],
            "gameplay": gameplay["status"],
            "visual_earliest_failure": visual.get("details", {}).get("earliest_failure"),
            "visual_spatial": visual.get("details", {}).get("authoritative_spatial_status"),
        },
    )


def validate_world(
    world_id: str,
    world: Mapping[str, Any],
    capability: str,
    *,
    run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one named world validator and return domain evidence."""

    key = str(capability or "").strip()
    if key not in WORLD_VALIDATION_CAPABILITIES:
        raise WorldStoreError(f"Unsupported world validation capability: {key!r}")
    if key == "world.spec.validate":
        return _validate_spec(world_id, world)
    if key == "world.blockout.validate":
        return _validate_blockout(world_id, world)
    if key == "world.construction.validate":
        return _validate_construction(world_id, world, run)
    if key == "world.spatial.validate":
        return _validate_spatial(world_id, world, run)
    if key == "world.visual.validate":
        return _validate_visual(world_id, world, run)
    if key == "world.gameplay.validate":
        return _validate_gameplay(world_id, world)
    return _validate_final(world_id, world, run)


__all__ = ["WORLD_VALIDATION_CAPABILITIES", "validate_world"]
