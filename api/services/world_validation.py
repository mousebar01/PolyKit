"""Deterministic validation adapters for Agent-owned world workflows.

These validators inspect domain state and completed Workflow Run evidence. They
never mutate AgentWorkflowSession state; callers use the returned ``outcome``
and evidence with the generic Agent Workflow Protocol.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.world_runtime import refresh_runtime_quality
from services.world_store import WorldStoreError


WORLD_VALIDATION_CAPABILITIES = {
    "world.spec.validate",
    "world.blockout.validate",
    "world.construction.validate",
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
    outcome: str,
    issues: list[dict[str, Any]],
    *,
    ref_suffix: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status = _status(issues)
    summary = f"{capability}: {status}"
    if issues:
        summary += f" ({len(issues)} issue{'s' if len(issues) != 1 else ''})"
    return {
        "world_id": world_id,
        "capability": capability,
        "status": status,
        "outcome": outcome,
        "issues": issues,
        "details": dict(details or {}),
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
    ids = {
        str(item.get("id"))
        for item in raw_objects if isinstance(raw_objects, list) and isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    return scene, ids


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
        "continue" if _status(issues) == "pass" else "revise-spec",
        issues,
        ref_suffix="runtime",
    )


def _validate_blockout(world_id: str, world: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _runtime(world)
    scene, object_ids = _scene_objects(runtime)
    issues: list[dict[str, Any]] = []
    if scene is None or not object_ids:
        issues.append(_issue("blockout-missing", "error", "No compiled scene blockout is available."))
        outcome = "revise-spec"
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
        outcome = "continue" if _status(issues) == "pass" else "retry-step"

    return _report(
        world_id,
        "world.blockout.validate",
        "review-report",
        outcome,
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
            for item in raw_issues:
                if isinstance(item, Mapping):
                    issues.append(dict(item))
    if domain_status == "pending":
        issues.append(_issue("construction-domain-evidence-missing", "error", "BuildSpec has no validated construction evidence."))

    run_metadata = _workflow_metadata(run)
    run_status = run.get("status") if isinstance(run, Mapping) else None
    run_recipe = run_metadata.get("workflow_recipe") if isinstance(run_metadata, Mapping) else None
    run_world_id = run_metadata.get("world_id") if isinstance(run_metadata, Mapping) else None
    if run_status != "done":
        issues.append(_issue("construction-run-missing", "error", "A completed building-construction Workflow Run is required."))
    elif run_recipe != "building-construction" or run_world_id != world_id:
        issues.append(_issue("construction-run-mismatch", "error", "Workflow Run does not prove construction for this world."))

    build = runtime.get("build")
    buildings = build.get("buildings") if isinstance(build, Mapping) else None
    if not isinstance(buildings, list) or not buildings:
        outcome = "revise-spec"
    else:
        outcome = "continue" if _status(issues) == "pass" else "retry-step"

    return _report(
        world_id,
        "world.construction.validate",
        "construction-report",
        outcome,
        issues,
        ref_suffix="runtime/quality/construction",
        details={
            "domain_status": domain_status,
            "run_id": run.get("run_id") if isinstance(run, Mapping) else None,
            "run_status": run_status,
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
        issues.append(_issue("objective-missing", "warning", "Playable World Builder output needs at least one objective."))
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
    runtime = _runtime(world)
    issues = _gameplay_issues(runtime)
    status = _status(issues)
    return _report(
        world_id,
        "world.gameplay.validate",
        "gameplay-report",
        "continue" if status == "pass" else ("retry-step" if status == "needs_review" else "revise-spec"),
        issues,
        ref_suffix="runtime/game",
    )


def _validate_final(
    world_id: str,
    world: Mapping[str, Any],
    run: Mapping[str, Any] | None,
) -> dict[str, Any]:
    construction = _validate_construction(world_id, world, run)
    gameplay = _validate_gameplay(world_id, world)
    runtime = _runtime(world)
    quality = runtime.get("quality")
    visual = quality.get("visual") if isinstance(quality, Mapping) else None
    visual_status = visual.get("status") if isinstance(visual, Mapping) else "pending"

    issues: list[dict[str, Any]] = []
    if construction["status"] != "pass":
        issues.append(_issue("final-construction-not-pass", "error", "Construction review has not passed."))
    if gameplay["status"] != "pass":
        issues.append(_issue("final-gameplay-not-pass", "error", "Gameplay review has not passed."))
    if visual_status != "pass":
        issues.append(_issue(
            "final-visual-evidence-missing",
            "warning",
            "Final review cannot pass until visual quality has explicit passing evidence.",
        ))

    status = _status(issues)
    if construction["status"] != "pass":
        outcome = "retry-structure"
    elif gameplay["status"] != "pass":
        outcome = "retry-gameplay"
    elif status == "pass":
        outcome = "continue"
    else:
        # There is deliberately no silent visual pass. The v1 workflow reaches
        # final-review and stops here until a visual evidence producer is wired.
        outcome = "stop"

    return _report(
        world_id,
        "world.final.validate",
        "final-report",
        outcome,
        issues,
        ref_suffix="runtime/quality",
        details={
            "construction": construction["status"],
            "gameplay": gameplay["status"],
            "visual": visual_status,
        },
    )


def validate_world(
    world_id: str,
    world: Mapping[str, Any],
    capability: str,
    *,
    run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one named world validator and return workflow-compatible evidence."""

    key = str(capability or "").strip()
    if key not in WORLD_VALIDATION_CAPABILITIES:
        raise WorldStoreError(f"Unsupported world validation capability: {key!r}")
    if key == "world.spec.validate":
        return _validate_spec(world_id, world)
    if key == "world.blockout.validate":
        return _validate_blockout(world_id, world)
    if key == "world.construction.validate":
        return _validate_construction(world_id, world, run)
    if key == "world.gameplay.validate":
        return _validate_gameplay(world_id, world)
    return _validate_final(world_id, world, run)


__all__ = ["WORLD_VALIDATION_CAPABILITIES", "validate_world"]
