"""Evidence-first visual validation for reference-guided worlds.

The module deliberately separates deterministic image metrics from semantic and
spatial review. It produces immutable ``polykit.visual-validation-report``
artifacts and validates their evidence; it never owns WorkflowRun state, retries,
rollback, or repair decisions.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from services.workspace_paths import resolve_workspace_path


VISUAL_REPORT_KIND = "polykit.visual-validation-report"
VISUAL_REPORT_SCHEMA_VERSION = 1
CHECK_STATUSES = {"pass", "needs_review", "fail", "not_evaluated", "not_applicable"}
JUDGE_KINDS = {"metric", "semantic", "spatial"}
CATEGORY_ORDER = {
    "frame": 0,
    "camera": 1,
    "negative_space": 2,
    "spatial": 3,
    "silhouette": 4,
    "construction": 5,
    "luminance": 6,
    "material": 7,
    "lighting": 8,
    "color": 9,
    "surface": 10,
    "semantic": 11,
}


def _mae(image: Image.Image) -> float:
    return sum(ImageStat.Stat(image).mean) / (len(image.getbands()) * 255.0)


def _normalized_bbox(value: Any) -> list[float] | None:
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) == 4
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    ):
        bbox = [float(item) for item in value]
        if (
            0.0 <= bbox[0] <= 1.0
            and 0.0 <= bbox[1] <= 1.0
            and bbox[2] > 0.0
            and bbox[3] > 0.0
            and bbox[0] + bbox[2] <= 1.000001
            and bbox[1] + bbox[3] <= 1.000001
        ):
            return bbox
    return None


def _crop_normalized(image: Image.Image, bbox: list[float]) -> Image.Image:
    x, y, width, height = bbox
    left = max(0, min(image.width - 1, round(x * image.width)))
    top = max(0, min(image.height - 1, round(y * image.height)))
    right = max(left + 1, min(image.width, round((x + width) * image.width)))
    bottom = max(top + 1, min(image.height, round((y + height) * image.height)))
    return image.crop((left, top, right, bottom))


def _threshold_status(value: float, *, warn: float, fail: float) -> str:
    if value > fail:
        return "fail"
    if value > warn:
        return "needs_review"
    return "pass"


def _check(
    check_id: str,
    category: str,
    status: str,
    *,
    required: bool = True,
    subjects: Sequence[str] | None = None,
    metrics: Mapping[str, Any] | None = None,
    thresholds: Mapping[str, Any] | None = None,
    evidence_refs: Sequence[str] | None = None,
    message: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "category": category,
        "judge": "metric",
        "required": bool(required),
        "status": status,
        "subjects": list(subjects or []),
        "metrics": dict(metrics or {}),
        "thresholds": dict(thresholds or {}),
        "evidence_refs": list(evidence_refs or []),
        "message": message,
    }


def _required_status(checks: Sequence[Mapping[str, Any]]) -> str:
    required = [
        item
        for item in checks
        if item.get("required") is True and item.get("status") != "not_applicable"
    ]
    if any(item.get("status") == "fail" for item in required):
        return "fail"
    if any(item.get("status") in {"needs_review", "not_evaluated"} for item in required):
        return "needs_review"
    if not required:
        return "needs_review"
    return "pass"


def _judge_status(checks: Sequence[Mapping[str, Any]], judge: str) -> str:
    selected = [item for item in checks if item.get("judge") == judge]
    return _required_status(selected) if selected else "needs_review"


def _first_unresolved(checks: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    unresolved = [
        item
        for item in checks
        if item.get("required") is True
        and item.get("status") in {"fail", "needs_review", "not_evaluated"}
    ]
    if not unresolved:
        return None
    unresolved.sort(
        key=lambda item: (
            CATEGORY_ORDER.get(str(item.get("category") or ""), 999),
            0 if item.get("status") == "fail" else 1,
            str(item.get("id") or ""),
        )
    )
    item = unresolved[0]
    return {
        "check_id": item.get("id"),
        "category": item.get("category"),
        "status": item.get("status"),
        "subjects": list(item.get("subjects") or []),
    }


def _summary(checks: Sequence[Mapping[str, Any]], *, spatial_applicable: bool) -> dict[str, Any]:
    required = [item for item in checks if item.get("required") is True]
    return {
        "metric_status": _judge_status(checks, "metric"),
        "semantic_status": _judge_status(checks, "semantic"),
        "spatial_status": _judge_status(checks, "spatial") if spatial_applicable else "not_applicable",
        "required_checks": len(required),
        "passed_checks": sum(item.get("status") == "pass" for item in required),
        "review_checks": sum(
            item.get("status") in {"needs_review", "not_evaluated"} for item in required
        ),
        "failed_checks": sum(item.get("status") == "fail" for item in required),
    }


def compare_reference_images(
    reference_path: Path,
    candidate_path: Path,
    output_dir: Path,
    *,
    tag: str,
    observations: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create deterministic comparison evidence and metric checks.

    P0 observations are evaluated at their reference bounds. If an observation also
    includes ``candidate_bbox_normalized`` (for example from segmentation or a
    camera projection pass), center and size errors are measured explicitly.
    Missing candidate bounds remain ``not_evaluated`` instead of being invented.
    """

    reference_path = reference_path.expanduser().resolve()
    candidate_path = candidate_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not reference_path.is_file():
        raise FileNotFoundError(reference_path)
    if not candidate_path.is_file():
        raise FileNotFoundError(candidate_path)

    with Image.open(reference_path) as source:
        reference = source.convert("RGB")
    with Image.open(candidate_path) as source:
        candidate = source.convert("RGB")

    reference_aspect = reference.width / max(reference.height, 1)
    candidate_aspect = candidate.width / max(candidate.height, 1)
    aspect_error = abs(candidate_aspect - reference_aspect) / max(reference_aspect, 1e-9)
    thumb_size = (256, max(1, round(256 / reference_aspect)))
    reference_thumb = reference.resize(thumb_size, Image.Resampling.LANCZOS)
    candidate_thumb = candidate.resize(thumb_size, Image.Resampling.LANCZOS)
    reference_gray = ImageOps.grayscale(reference_thumb)
    candidate_gray = ImageOps.grayscale(candidate_thumb)

    grayscale_mae = _mae(ImageChops.difference(reference_gray, candidate_gray))
    color_mae = _mae(ImageChops.difference(reference_thumb, candidate_thumb))
    edge_mae = _mae(
        ImageChops.difference(
            reference_gray.filter(ImageFilter.FIND_EDGES),
            candidate_gray.filter(ImageFilter.FIND_EDGES),
        )
    )

    grid_luminance: list[float] = []
    for row in range(3):
        for column in range(4):
            bbox = [column / 4.0, row / 3.0, 1.0 / 4.0, 1.0 / 3.0]
            grid_luminance.append(
                _mae(
                    ImageChops.difference(
                        _crop_normalized(reference_gray, bbox),
                        _crop_normalized(candidate_gray, bbox),
                    )
                )
            )
    max_grid_luminance = max(grid_luminance, default=0.0)

    output_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = output_dir / f"reference-overlay-{tag}.png"
    difference_path = output_dir / f"reference-difference-{tag}.png"
    edge_path = output_dir / f"reference-edges-{tag}.png"
    Image.blend(reference_thumb, candidate_thumb, 0.5).save(overlay_path)
    ImageChops.difference(reference_thumb, candidate_thumb).point(
        lambda value: min(255, value * 3)
    ).save(difference_path)
    ImageChops.difference(
        reference_gray.filter(ImageFilter.FIND_EDGES),
        candidate_gray.filter(ImageFilter.FIND_EDGES),
    ).point(lambda value: min(255, value * 3)).save(edge_path)

    evidence = [
        {"id": "evidence:reference", "kind": "reference", "path": str(reference_path)},
        {"id": "evidence:candidate", "kind": "hero_render", "path": str(candidate_path)},
        {"id": "evidence:overlay", "kind": "overlay", "path": str(overlay_path)},
        {"id": "evidence:difference", "kind": "difference", "path": str(difference_path)},
        {"id": "evidence:edges", "kind": "edge_comparison", "path": str(edge_path)},
    ]
    common_evidence = ["evidence:reference", "evidence:candidate"]
    checks: list[dict[str, Any]] = [
        _check(
            "frame.aspect-ratio",
            "frame",
            _threshold_status(aspect_error, warn=0.005, fail=0.01),
            metrics={"error": aspect_error},
            thresholds={"warn": 0.005, "fail": 0.01},
            evidence_refs=common_evidence,
            message="Candidate aspect ratio compared with the reference.",
        ),
        _check(
            "luminance.global-grayscale-mae",
            "luminance",
            _threshold_status(grayscale_mae, warn=0.12, fail=0.20),
            metrics={"mae": grayscale_mae},
            thresholds={"warn": 0.12, "fail": 0.20},
            evidence_refs=[*common_evidence, "evidence:difference"],
            message="Global grayscale thumbnail error.",
        ),
        _check(
            "silhouette.global-edge-mae",
            "silhouette",
            _threshold_status(edge_mae, warn=0.18, fail=0.30),
            metrics={"mae": edge_mae},
            thresholds={"warn": 0.18, "fail": 0.30},
            evidence_refs=[*common_evidence, "evidence:edges"],
            message="Global edge-map thumbnail error.",
        ),
        _check(
            "luminance.grid-max-mae",
            "luminance",
            _threshold_status(max_grid_luminance, warn=0.18, fail=0.32),
            metrics={"max_mae": max_grid_luminance, "cells": grid_luminance},
            thresholds={"warn": 0.18, "fail": 0.32},
            evidence_refs=common_evidence,
            message="Maximum luminance error across a 4x3 image grid.",
        ),
    ]

    p0_observations = [item for item in (observations or []) if item.get("priority") == "P0"]
    checks.append(
        _check(
            "p0.observations-present",
            "silhouette",
            "pass" if p0_observations else "needs_review",
            metrics={"count": len(p0_observations)},
            evidence_refs=["evidence:reference"],
            message=(
                "P0 observations are available for regional validation."
                if p0_observations
                else "Reference-locked validation requires at least one measurable P0 observation."
            ),
        )
    )

    for index, observation in enumerate(p0_observations):
        observation_id = str(observation.get("id") or f"p0-{index}")
        bbox = _normalized_bbox(observation.get("bbox_normalized"))
        if bbox is None:
            checks.append(
                _check(
                    f"p0.{observation_id}.bounds",
                    "silhouette",
                    "not_evaluated",
                    subjects=[observation_id],
                    evidence_refs=["evidence:reference"],
                    message="P0 observation has no valid normalized reference bounds.",
                )
            )
            continue

        reference_region = _crop_normalized(reference_thumb, bbox)
        candidate_region = _crop_normalized(candidate_thumb, bbox)
        reference_region_gray = ImageOps.grayscale(reference_region)
        candidate_region_gray = ImageOps.grayscale(candidate_region)
        region_gray_mae = _mae(ImageChops.difference(reference_region_gray, candidate_region_gray))
        region_edge_mae = _mae(
            ImageChops.difference(
                reference_region_gray.filter(ImageFilter.FIND_EDGES),
                candidate_region_gray.filter(ImageFilter.FIND_EDGES),
            )
        )
        checks.append(
            _check(
                f"p0.{observation_id}.grayscale-mae",
                "luminance",
                _threshold_status(region_gray_mae, warn=0.15, fail=0.25),
                subjects=[observation_id],
                metrics={"mae": region_gray_mae, "bbox_normalized": bbox},
                thresholds={"warn": 0.15, "fail": 0.25},
                evidence_refs=common_evidence,
                message="P0 regional grayscale error.",
            )
        )
        checks.append(
            _check(
                f"p0.{observation_id}.edge-mae",
                "silhouette",
                _threshold_status(region_edge_mae, warn=0.20, fail=0.30),
                subjects=[observation_id],
                metrics={"mae": region_edge_mae, "bbox_normalized": bbox},
                thresholds={"warn": 0.20, "fail": 0.30},
                evidence_refs=[*common_evidence, "evidence:edges"],
                message="P0 regional edge-map error.",
            )
        )

        candidate_bbox = _normalized_bbox(observation.get("candidate_bbox_normalized"))
        if candidate_bbox is None:
            checks.append(
                _check(
                    f"p0.{observation_id}.bbox-geometry",
                    "silhouette",
                    "not_evaluated",
                    subjects=[observation_id],
                    metrics={"reference_bbox_normalized": bbox},
                    evidence_refs=common_evidence,
                    message="Candidate P0 bounds were not supplied; center and size errors remain unevaluated.",
                )
            )
            continue

        ref_center = (bbox[0] + bbox[2] / 2.0, bbox[1] + bbox[3] / 2.0)
        candidate_center = (
            candidate_bbox[0] + candidate_bbox[2] / 2.0,
            candidate_bbox[1] + candidate_bbox[3] / 2.0,
        )
        center_error = math.hypot(
            candidate_center[0] - ref_center[0],
            candidate_center[1] - ref_center[1],
        ) / math.sqrt(2.0)
        width_error = abs(candidate_bbox[2] - bbox[2]) / max(bbox[2], 1e-9)
        height_error = abs(candidate_bbox[3] - bbox[3]) / max(bbox[3], 1e-9)
        bbox_status = "pass"
        if center_error > 0.04 or width_error > 0.10 or height_error > 0.10:
            bbox_status = "fail"
        elif center_error > 0.02 or width_error > 0.05 or height_error > 0.05:
            bbox_status = "needs_review"
        checks.append(
            _check(
                f"p0.{observation_id}.bbox-geometry",
                "silhouette",
                bbox_status,
                subjects=[observation_id],
                metrics={
                    "center_error_diagonal_fraction": center_error,
                    "width_error_fraction": width_error,
                    "height_error_fraction": height_error,
                    "reference_bbox_normalized": bbox,
                    "candidate_bbox_normalized": candidate_bbox,
                },
                thresholds={
                    "center_warn": 0.02,
                    "center_fail": 0.04,
                    "size_warn": 0.05,
                    "size_fail": 0.10,
                },
                evidence_refs=common_evidence,
                message="P0 center and size error from supplied candidate bounds.",
            )
        )

    return {
        "schema_version": 1,
        "kind": "polykit.visual-metric-bundle",
        "status": _required_status(checks),
        "thumbnail_size": list(thumb_size),
        "metrics": {
            "aspect_ratio_error": aspect_error,
            "grayscale_mae": grayscale_mae,
            "color_mae": color_mae,
            "edge_mae": edge_mae,
            "grid_luminance_mae": grid_luminance,
            "max_grid_luminance_mae": max_grid_luminance,
        },
        "checks": checks,
        "evidence": evidence,
    }


def build_visual_validation_report(
    *,
    world_id: str,
    run_id: str,
    target: Mapping[str, Any],
    candidate: Mapping[str, Any],
    metric_bundle: Mapping[str, Any],
    semantic_checks: Sequence[Mapping[str, Any]] | None = None,
    spatial_checks: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble one immutable VisualValidationReport.

    Missing semantic review is represented explicitly as ``needs_review``. This
    keeps v1 fail-closed while allowing a future multimodal or human semantic judge
    to plug into the same contract without changing runtime ownership.
    """

    checks = [dict(item) for item in metric_bundle.get("checks", []) if isinstance(item, Mapping)]
    evidence = [dict(item) for item in metric_bundle.get("evidence", []) if isinstance(item, Mapping)]

    if semantic_checks:
        checks.extend(dict(item) for item in semantic_checks if isinstance(item, Mapping))
    else:
        checks.append(
            {
                "id": "semantic.final-review",
                "category": "semantic",
                "judge": "semantic",
                "required": True,
                "status": "needs_review",
                "subjects": [],
                "confidence": None,
                "evidence_refs": ["evidence:reference", "evidence:candidate"],
                "message": "Semantic visual review has not been performed.",
            }
        )

    require_spatial = bool(target.get("require_spatial"))
    if spatial_checks:
        checks.extend(dict(item) for item in spatial_checks if isinstance(item, Mapping))
    elif require_spatial:
        checks.append(
            {
                "id": "spatial.final-review",
                "category": "spatial",
                "judge": "spatial",
                "required": True,
                "status": "not_evaluated",
                "subjects": [],
                "evidence_refs": [],
                "message": "Required World/geometry spatial review has not been performed.",
            }
        )

    status = _required_status(checks)
    return {
        "schema_version": VISUAL_REPORT_SCHEMA_VERSION,
        "kind": VISUAL_REPORT_KIND,
        "world_id": world_id,
        "run_id": run_id,
        "validator": "world.visual.validate",
        "status": status,
        "target": dict(target),
        "candidate": dict(candidate),
        "summary": _summary(
            checks,
            spatial_applicable=require_spatial or bool(spatial_checks),
        ),
        "checks": checks,
        "earliest_failure": _first_unresolved(checks),
        "evidence": evidence,
        "provenance": {"validator_version": "visual-v1"},
    }


def _workspace_value(value: str) -> str:
    text = value.strip()
    if text.startswith("workspace://"):
        return text[len("workspace://") :].lstrip("/")
    if text.startswith("/workspace/"):
        return text[len("/workspace/") :]
    return text


def load_visual_validation_report(value: Any, *, workspace_root: Path) -> dict[str, Any]:
    """Load an embedded report or a workspace-relative report reference."""

    if isinstance(value, Mapping) and value.get("kind") == VISUAL_REPORT_KIND:
        return dict(value)
    workspace_path = None
    if isinstance(value, str):
        workspace_path = value
    elif isinstance(value, Mapping):
        candidate = value.get("workspace_path") or value.get("workspacePath") or value.get("ref")
        if isinstance(candidate, str):
            workspace_path = candidate
    if not workspace_path:
        raise ValueError("Visual validation report reference is missing")
    path = resolve_workspace_path(workspace_root, _workspace_value(workspace_path))
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Visual validation report must be a JSON object")
    return payload


def _evidence_path(
    item: Mapping[str, Any],
    *,
    workspace_root: Path,
) -> tuple[Path | None, str | None]:
    """Resolve evidence while enforcing the server-owned workspace boundary."""

    root = workspace_root.expanduser().resolve()
    workspace_path = item.get("workspace_path") or item.get("workspacePath")
    if isinstance(workspace_path, str) and workspace_path.strip():
        try:
            return resolve_workspace_path(root, _workspace_value(workspace_path)), None
        except ValueError:
            return None, "invalid"

    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "missing"
    path = Path(raw_path).expanduser().resolve()
    if not path.is_relative_to(root):
        return None, "outside-workspace"
    return path, None


def validate_visual_validation_report(
    report: Mapping[str, Any],
    *,
    world_id: str,
    run_id: str | None = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Validate report integrity and recompute its fail-closed status."""

    issues: list[dict[str, Any]] = []

    def issue(code: str, severity: str, message: str, subject_id: str | None = None) -> None:
        item: dict[str, Any] = {"code": code, "severity": severity, "message": message}
        if subject_id:
            item["subject_id"] = subject_id
        issues.append(item)

    if report.get("schema_version") != VISUAL_REPORT_SCHEMA_VERSION:
        issue("visual-report-schema", "error", "Visual report schema_version must be 1.")
    if report.get("kind") != VISUAL_REPORT_KIND:
        issue("visual-report-kind", "error", f"Visual report kind must be {VISUAL_REPORT_KIND!r}.")
    if report.get("validator") != "world.visual.validate":
        issue("visual-report-validator", "error", "Visual report validator must be 'world.visual.validate'.")
    if report.get("world_id") != world_id:
        issue("visual-report-world-mismatch", "error", "Visual report does not belong to this world.")
    if run_id and report.get("run_id") != run_id:
        issue("visual-report-run-mismatch", "error", "Visual report does not belong to the requested WorkflowRun.")

    target = report.get("target")
    candidate = report.get("candidate")
    target_map = target if isinstance(target, Mapping) else {}
    candidate_map = candidate if isinstance(candidate, Mapping) else {}
    reference_locked = target_map.get("kind") == "reference-image"
    require_spatial = bool(target_map.get("require_spatial"))

    target_camera_id = target_map.get("camera_id")
    if isinstance(target_camera_id, str) and target_camera_id:
        if candidate_map.get("camera_id") != target_camera_id:
            issue(
                "visual-camera-id-mismatch",
                "error",
                "Candidate camera does not match the validated target camera.",
                target_camera_id,
            )
    if "camera_revision" in target_map and target_map.get("camera_revision") is not None:
        if candidate_map.get("camera_revision") != target_map.get("camera_revision"):
            issue(
                "visual-camera-revision-mismatch",
                "error",
                "Candidate render was produced from a different camera revision.",
                str(target_camera_id) if target_camera_id else None,
            )

    raw_checks = report.get("checks")
    checks = [item for item in raw_checks if isinstance(item, Mapping)] if isinstance(raw_checks, list) else []
    if not checks:
        issue("visual-checks-missing", "error", "Visual report has no validation checks.")

    raw_evidence = report.get("evidence")
    evidence = [item for item in raw_evidence if isinstance(item, Mapping)] if isinstance(raw_evidence, list) else []
    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    for item in evidence:
        evidence_id = item.get("id")
        if not isinstance(evidence_id, str) or not evidence_id:
            issue("visual-evidence-id-missing", "error", "Visual evidence requires a stable id.")
            continue
        if evidence_id in evidence_by_id:
            issue("visual-evidence-id-duplicate", "error", f"Duplicate visual evidence id: {evidence_id}")
            continue
        evidence_by_id[evidence_id] = item

    seen_judges: set[str] = set()
    required_judges: set[str] = set()
    check_ids: set[str] = set()
    for item in checks:
        check_id = item.get("id")
        if not isinstance(check_id, str) or not check_id:
            issue("visual-check-id-missing", "error", "Every visual check requires an id.")
            continue
        if check_id in check_ids:
            issue("visual-check-id-duplicate", "error", f"Duplicate visual check id: {check_id}", check_id)
        check_ids.add(check_id)

        judge = item.get("judge")
        if judge not in JUDGE_KINDS:
            issue("visual-check-judge-invalid", "error", f"Check {check_id} has invalid judge {judge!r}.", check_id)
        else:
            seen_judges.add(str(judge))
            if item.get("required") is True:
                required_judges.add(str(judge))

        status = item.get("status")
        if status not in CHECK_STATUSES:
            issue("visual-check-status-invalid", "error", f"Check {check_id} has invalid status {status!r}.", check_id)

        refs = item.get("evidence_refs")
        if item.get("required") is True and status in {"pass", "needs_review", "fail"}:
            if not isinstance(refs, list) or not refs:
                issue("visual-check-evidence-empty", "error", f"Required check {check_id} has no evidence.", check_id)
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, str) or not ref:
                    issue("visual-check-evidence-ref-invalid", "error", f"Check {check_id} has an invalid evidence reference.", check_id)
                elif ref not in evidence_by_id:
                    issue("visual-check-evidence-missing", "error", f"Check {check_id} references missing evidence {ref}.", check_id)

    if reference_locked:
        if "metric" not in required_judges:
            issue("visual-metric-judge-missing", "error", "Reference-image validation requires deterministic required metric checks.")
        if "semantic" not in required_judges:
            issue("visual-semantic-judge-missing", "warning", "Reference-image validation needs a required semantic review before PASS.")
        p0_check = next(
            (
                item
                for item in checks
                if item.get("id") == "p0.observations-present"
                and item.get("judge") == "metric"
                and item.get("required") is True
            ),
            None,
        )
        if p0_check is None:
            issue("visual-p0-check-missing", "error", "Reference-image validation requires the P0 completeness check.")

    if require_spatial and "spatial" not in required_judges:
        issue("visual-spatial-judge-missing", "error", "This target requires a World/geometry spatial review.")

    if workspace_root is not None:
        for evidence_id, item in evidence_by_id.items():
            path, path_error = _evidence_path(item, workspace_root=workspace_root)
            if path_error == "missing":
                issue("visual-evidence-path-missing", "warning", f"Evidence {evidence_id} has no file path.", evidence_id)
            elif path_error == "invalid":
                issue("visual-evidence-path-invalid", "error", f"Evidence {evidence_id} has an invalid workspace path.", evidence_id)
            elif path_error == "outside-workspace":
                issue("visual-evidence-outside-workspace", "error", f"Evidence {evidence_id} resolves outside the PolyKit workspace.", evidence_id)
            elif path is not None and not path.is_file():
                issue("visual-evidence-file-missing", "error", f"Evidence file does not exist: {path}", evidence_id)

    derived_status = _required_status(checks)
    if report.get("status") != derived_status:
        issue(
            "visual-report-status-mismatch",
            "error",
            f"Report claims {report.get('status')!r} but required checks derive {derived_status!r}.",
        )

    if any(item["severity"] == "error" for item in issues):
        status = "fail"
    elif derived_status == "fail":
        status = "fail"
    elif any(item["severity"] == "warning" for item in issues) or derived_status == "needs_review":
        status = "needs_review"
    else:
        status = "pass"

    spatial_applicable = require_spatial or "spatial" in seen_judges
    return {
        "status": status,
        "derived_status": derived_status,
        "issues": issues,
        "checks": [dict(item) for item in checks],
        "summary": _summary(checks, spatial_applicable=spatial_applicable),
        "earliest_failure": _first_unresolved(checks),
    }


__all__ = [
    "VISUAL_REPORT_KIND",
    "VISUAL_REPORT_SCHEMA_VERSION",
    "build_visual_validation_report",
    "compare_reference_images",
    "load_visual_validation_report",
    "validate_visual_validation_report",
]
