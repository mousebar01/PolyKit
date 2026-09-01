"""Provenance-aware character proportion process nodes."""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from typing import Any


CANON_BY_HEADS: dict[int, dict[str, float]] = {
    8: {
        "shoulderWidth": 0.25,
        "waistWidth": 0.125,
        "hipWidth": 0.1875,
        "hipLineY": 0.50,
        "kneeLineY": 0.25,
        "upperArmLength": 0.187,
        "forearmLength": 0.187,
        "shinLength": 0.25,
    }
}
FACE_LANDMARKS_BY_HEAD_FRACTION = {"eyeLine": 0.50, "noseBase": 0.75, "mouthLine": 0.85}
UNSOURCED_LANDMARKS = ("chestWidth", "shoulderLineY", "waistLineY", "handLength", "thighLength", "footLength")
VALID_HAIR_TIERS = ("shell", "masses", "locks")
VALID_HAIR_REGIONS = {
    "crown", "fringe", "temple-left", "temple-right", "side-left", "side-right",
    "rear", "nape", "sideburn-left", "sideburn-right", "tail", "stray",
}
VALID_HAIR_PRIMITIVES = {"tapered-sweep", "curve-sweep", "lathe", "ellipsoid", "instanced-cluster"}
REJECTED_HAIR_PRIMITIVES = {
    "plane-card": "a card needs an alpha texture and would render as an opaque rectangle",
    "tube": "a constant-radius tube reads as a noodle; use a tapered sweep",
    "box": "a box cannot describe a hair mass; use a sweep or lathe",
}
UNCALIBRATED_HAIR_FIELDS = (
    "masses[].taper",
    "masses[].crossSection.aspect",
    "flowField.gravity",
    "flowField.whorls[].strength",
)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _style_heads(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"style_heads must be a finite number, got {value!r}")
    parsed = float(value)
    if parsed <= 0.0:
        raise ValueError("style_heads must be greater than 0")
    return parsed


def derive_anatomy(style_heads: float) -> dict[str, Any]:
    style_heads = _style_heads(style_heads)
    if not float(style_heads).is_integer() or int(style_heads) != 8:
        raise ValueError(
            f"no complete canon for {style_heads:g} heads; the built-in table is intentionally "
            "limited to 8 heads. Measure a different style from a reference instead of interpolating."
        )
    head_height = 1.0 / style_heads

    def in_heads(fraction: float) -> float:
        return round(fraction / head_height, 4)

    fractions = CANON_BY_HEADS[8]
    return {
        "applies": True,
        "source": "canon-table",
        "styleHeads": style_heads,
        "snappedToHeads": 8,
        "proportions": {
            "torso": in_heads(1.0 - fractions["hipLineY"]) - 1.0,
            "legs": in_heads(fractions["hipLineY"]),
            "headHeightFraction": round(head_height, 4),
            **{key: round(value, 4) for key, value in fractions.items()},
        },
        "faceLandmarks": dict(FACE_LANDMARKS_BY_HEAD_FRACTION),
        "pose": {"name": "bind", "description": "A-pose bind, arms lowered, palms inward"},
        "unsourced": list(UNSOURCED_LANDMARKS),
    }


def _report(style_heads: Any, spec: dict[str, Any] | None) -> dict[str, Any]:
    try:
        anatomy = derive_anatomy(style_heads)
    except ValueError as exc:
        return {
            "schemaVersion": 1,
            "kind": "polykit.humanoid-proportions",
            "status": "fail",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "passed": False,
            "errors": [str(exc)],
            "warnings": [],
        }
    if spec is not None:
        source_image = spec.get("sourceImage")
        if isinstance(source_image, str) and source_image.strip() and source_image != "/dev/null":
            return {
                "schemaVersion": 1,
                "kind": "polykit.humanoid-proportions",
                "status": "fail",
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "passed": False,
                "errors": [
                    f"spec names a reference image ({source_image}); measure anatomy from it rather than substituting the canon table"
                ],
                "warnings": [],
            }
        spec = dict(spec)
        assessment = spec.get("preSpecAssessment")
        if not isinstance(assessment, dict):
            assessment = {}
        spec["preSpecAssessment"] = dict(assessment)
        spec["preSpecAssessment"]["anatomy"] = anatomy
    return {
        "schemaVersion": 1,
        "kind": "polykit.humanoid-proportions",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "anatomy": anatomy,
        "spec": spec,
        "provenance": "canon-table",
        "unsourced": list(UNSOURCED_LANDMARKS),
        "reviewNotes": [
            "These values are a reference-free canon for an 8-head figure, not measurements of a specific image.",
            "Landmarks listed in unsourced remain intentionally open; do not treat their absence as a default dimension.",
        ],
    }


def _unit(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0


def _validate_hair_profile(profile: Any) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(profile, dict):
        return ["hairProfile must be an object"], warnings
    tier = profile.get("representationTier", "shell")
    if tier not in VALID_HAIR_TIERS:
        errors.append("hairProfile.representationTier must be one of: " + ", ".join(VALID_HAIR_TIERS))
        tier = "shell"
    scalp = profile.get("scalpComponentId")
    if not isinstance(scalp, str) or not scalp.strip():
        errors.append("hairProfile.scalpComponentId is required")
    hairline = profile.get("hairline")
    if hairline is None:
        warnings.append("hairProfile.hairline is missing; the fringe has no explicit scalp anchor")
    elif not isinstance(hairline, dict):
        errors.append("hairProfile.hairline must be an object")
    else:
        points = hairline.get("controlPoints")
        if not isinstance(points, list) or len(points) < 3:
            errors.append("hairProfile.hairline.controlPoints needs at least three points")
        else:
            for index, point in enumerate(points):
                if not isinstance(point, dict) or not _unit(point.get("u")) or not _unit(point.get("v")):
                    errors.append(f"hairProfile.hairline.controlPoints[{index}] needs u and v in [0,1]")
    flow = profile.get("flowField")
    if flow is None:
        warnings.append("hairProfile.flowField is missing; each mass must then carry its own direction")
    elif not isinstance(flow, dict):
        errors.append("hairProfile.flowField must be an object")
    else:
        if flow.get("gravity") is not None and not _unit(flow.get("gravity")):
            errors.append("hairProfile.flowField.gravity must be in [0,1]")
        part = flow.get("partLine")
        if part is not None and (not isinstance(part, dict) or not _unit(part.get("u"))):
            errors.append("hairProfile.flowField.partLine.u must be in [0,1]")
        whorls = flow.get("whorls")
        if whorls is not None:
            if not isinstance(whorls, list):
                errors.append("hairProfile.flowField.whorls must be an array")
            else:
                for index, whorl in enumerate(whorls):
                    if not isinstance(whorl, dict) or not _unit(whorl.get("u")) or not _unit(whorl.get("v")):
                        errors.append(f"hairProfile.flowField.whorls[{index}] needs u and v in [0,1]")
                    elif whorl.get("strength") is not None and not _unit(whorl.get("strength")):
                        errors.append(f"hairProfile.flowField.whorls[{index}].strength must be in [0,1]")
    masses = profile.get("masses")
    if masses is None:
        if tier != "shell":
            errors.append(f"hairProfile.masses is required for representationTier {tier!r}")
    elif not isinstance(masses, list):
        errors.append("hairProfile.masses must be an array")
    else:
        seen: set[str] = set()
        for index, mass in enumerate(masses):
            label = f"hairProfile.masses[{index}]"
            if not isinstance(mass, dict):
                errors.append(f"{label} must be an object")
                continue
            mass_id = mass.get("id")
            if not isinstance(mass_id, str) or not mass_id.strip():
                errors.append(f"{label}.id is required")
            elif mass_id in seen:
                errors.append(f"hairProfile.masses has a duplicate id {mass_id!r}")
            else:
                seen.add(mass_id)
            region = mass.get("region")
            if region is not None and region not in VALID_HAIR_REGIONS:
                errors.append(f"{label}.region {region!r} is not a supported scalp region")
            primitive = mass.get("primitive")
            if primitive in REJECTED_HAIR_PRIMITIVES:
                errors.append(f"{label} may not use primitive {primitive!r}: {REJECTED_HAIR_PRIMITIVES[primitive]}")
            elif primitive is not None and primitive not in VALID_HAIR_PRIMITIVES:
                errors.append(f"{label}.primitive {primitive!r} is not supported")
            root = mass.get("root")
            if not isinstance(root, dict) or "position" in root or "xyz" in root or not _unit(root.get("u")) or not _unit(root.get("v")):
                errors.append(f"{label}.root must be scalp {{u, v}} coordinates in [0,1]")
            for field in ("length", "width", "thickness"):
                value = mass.get(field)
                if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0.0):
                    errors.append(f"{label}.{field} must be a positive number")
            taper = mass.get("taper")
            if taper is not None and not _unit(taper):
                errors.append(f"{label}.taper must be a ratio in [0,1]")
            if tier == "locks" and primitive not in (None, "tapered-sweep", "curve-sweep"):
                warnings.append(f"quality: {label} is in locks tier but is not a swept primitive")
    if tier == "locks":
        warnings.append("locks-tier taper and cross-section values are derived; no separated hair reference mesh is available for calibration")
    return errors, warnings


def _hair_profile_report(profile: Any) -> dict[str, Any]:
    errors, warnings = _validate_hair_profile(profile)
    tier = profile.get("representationTier", "shell") if isinstance(profile, dict) else "shell"
    masses = profile.get("masses") if isinstance(profile, dict) else None
    return {
        "schemaVersion": 1,
        "kind": "polykit.hair-profile-report",
        "status": "fail" if errors else "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "passed": not errors,
        "representationTier": tier,
        "massCount": len(masses) if isinstance(masses, list) else 0,
        "errors": errors,
        "warnings": warnings,
        "uncalibratedFields": list(UNCALIBRATED_HAIR_FIELDS),
        "limitations": [
            "This node validates a hairProfile schema; it does not compile profile masses into geometry.",
            "A separated hair reference mesh is required before lock-tier numeric bounds can be called calibrated.",
        ],
    }


def _component_slug(value: Any, fallback: str) -> str:
    """Make a stable component-id fragment without changing authored semantics."""
    raw = str(value or "").strip()
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf]+", "-", raw).strip("-").lower()
    return slug or fallback


def _compile_hair_profile(profile: Any) -> dict[str, Any]:
    """Compile the profile schema into a declarative, scalp-attached component tree."""
    errors, warnings = _validate_hair_profile(profile)
    if not isinstance(profile, dict):
        errors = errors or ["hairProfile must be an object"]
        return {
            "schemaVersion": 1,
            "kind": "polykit.hair-profile-compile",
            "status": "fail",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "passed": False,
            "componentTree": [],
            "errors": errors,
            "warnings": warnings,
            "unresolved": [],
            "limitations": ["The compiler emits a declarative componentTree; a geometry node must construct the actual mesh primitives."],
        }

    tier = str(profile.get("representationTier") or "shell")
    scalp_id = str(profile.get("scalpComponentId") or "").strip()
    profile_id = _component_slug(profile.get("componentId"), "hair")
    if scalp_id and profile_id == scalp_id:
        errors.append("hairProfile.componentId must differ from scalpComponentId")
    masses = profile.get("masses") if isinstance(profile.get("masses"), list) else []
    unresolved: list[str] = []
    component_tree: list[dict[str, Any]] = []
    root_component: dict[str, Any] = {
        "id": profile_id,
        "role": "hair",
        "category": "hair",
        "kind": "group",
        "parent": scalp_id or None,
        "attachment": {"anchor": scalp_id} if scalp_id else None,
        "representationTier": tier,
        "generatedFrom": "hairProfile",
    }
    if isinstance(profile.get("hairline"), dict):
        root_component["hairline"] = profile["hairline"]
    if isinstance(profile.get("flowField"), dict):
        root_component["flowField"] = profile["flowField"]
    component_tree.append(root_component)

    used_ids = {profile_id}
    for index, mass in enumerate(masses):
        if not isinstance(mass, dict):
            continue
        authored_id = _component_slug(mass.get("id"), f"mass-{index + 1}")
        component_id = f"{profile_id}-{authored_id}"
        suffix = 2
        while component_id in used_ids:
            component_id = f"{profile_id}-{authored_id}-{suffix}"
            suffix += 1
        used_ids.add(component_id)
        primitive = mass.get("primitive")
        root = mass.get("root") if isinstance(mass.get("root"), dict) else {}
        component: dict[str, Any] = {
            "id": component_id,
            "role": "hair",
            "category": str(mass.get("region") or "hair"),
            "kind": "geometry",
            "parent": profile_id,
            "sourceMassId": str(mass.get("id") or f"mass-{index + 1}"),
            "primitive": primitive,
            "surfaceUv": {"u": root.get("u"), "v": root.get("v")},
            "generatedFrom": "hairProfile.masses",
        }
        parameters: dict[str, Any] = {}
        for field in ("length", "width", "thickness", "taper", "crossSection", "direction"):
            if field in mass:
                parameters[field] = mass[field]
        if parameters:
            component["parameters"] = parameters
        if isinstance(profile.get("flowField"), dict):
            component["flowField"] = profile["flowField"]
        if isinstance(mass.get("standProud"), dict):
            component["standProud"] = mass["standProud"]
        component_tree.append(component)
        if not primitive:
            unresolved.append(f"{component_id}.primitive")
        if not any(field in mass for field in ("length", "width", "thickness")):
            unresolved.append(f"{component_id}.parameters")

    if errors:
        status = "fail"
        component_tree = []
    else:
        status = "needs_review" if unresolved else "pass"
    return {
        "schemaVersion": 1,
        "kind": "polykit.hair-profile-compile",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "passed": status == "pass",
        "scalpComponentId": scalp_id,
        "representationTier": tier,
        "componentTree": component_tree,
        "massCount": len(masses),
        "unresolved": unresolved,
        "errors": errors,
        "warnings": warnings,
        "limitations": [
            "The compiler emits a declarative componentTree; a geometry node must construct the actual mesh primitives.",
            "Roots remain scalp UV coordinates and no world-space root is invented.",
        ],
    }


def _run_scalp_exposure(descriptor: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    try:
        from scalp_exposure import measure_exposure
    except ImportError as exc:
        raise RuntimeError(f"scalp exposure helper is unavailable: {exc}") from exc
    rings = descriptor.get("rings")
    hair_points = descriptor.get("hairPoints", descriptor.get("hair_points"))
    if not isinstance(rings, list) or not isinstance(hair_points, list):
        raise ValueError("scalp-exposure requires JSON fields rings and hairPoints")
    try:
        v_low = float(params.get("v_low", descriptor.get("vLow", 0.55)) or 0.55)
        v_high = float(params.get("v_high", descriptor.get("vHigh", 1.0)) or 1.0)
        hard_max = float(params.get("hard_max", 0.05) or 0.05)
        reach = params.get("reach")
        lateral = params.get("lateral")
        reach_value = float(reach) if reach is not None and str(reach).strip() and float(reach) > 0.0 else None
        lateral_value = float(lateral) if lateral is not None and str(lateral).strip() and float(lateral) > 0.0 else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"scalp-exposure numeric parameters are invalid: {exc}") from exc
    u_samples = max(8, min(128, int(params.get("u_samples", 32))))
    v_samples = max(4, min(64, int(params.get("v_samples", 16))))
    return measure_exposure(rings, hair_points, u_samples=u_samples, v_samples=v_samples, reach=reach_value, lateral=lateral_value, hard_max=hard_max, v_range=(v_low, v_high))


def main() -> None:
    try:
        payload = json.loads(sys.stdin.readline())
        input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        node_id = str(params.get("_node_id") or "humanoid-proportions")
        if node_id not in {"humanoid-proportions", "hair-profile", "hair-profile-compile", "scalp-exposure"}:
            error(f"character-evidence: unsupported node {node_id!r}")
            return
        text = input_data.get("text")
        if node_id in {"hair-profile", "hair-profile-compile"}:
            if not isinstance(text, str) or not text.strip():
                error("character-evidence: hair profile nodes require a JSON text profile")
                return
            parsed = json.loads(text)
            progress(10, "Validating hair profile…")
            if node_id == "hair-profile-compile":
                report = _compile_hair_profile(parsed)
                metadata = {"evidence_kind": "hair-profile-compile", "schema_version": 1, "status": report["status"], "mass_count": report["massCount"]}
            else:
                report = _hair_profile_report(parsed)
                metadata = {"evidence_kind": "hair-profile", "schema_version": 1, "status": report["status"], "mass_count": report["massCount"]}
        elif node_id == "scalp-exposure":
            if not isinstance(text, str) or not text.strip():
                error("character-evidence: scalp-exposure requires a JSON text descriptor")
                return
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError("scalp exposure descriptor must be a JSON object")
            progress(10, "Measuring geometric scalp exposure…")
            report = _run_scalp_exposure(parsed, params)
            metadata = {"evidence_kind": "scalp-exposure", "schema_version": 1, "verdict": report["verdict"], "exposed_fraction": report["exposedFraction"]}
        else:
            raw_style_heads = params.get("style_heads", 8.0)
            spec = None
            if isinstance(text, str) and text.strip():
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError("character spec must be a JSON object")
                spec = parsed
            progress(10, "Deriving provenance-aware proportions…")
            report = _report(raw_style_heads, spec)
            metadata = {"evidence_kind": "humanoid-proportions", "schema_version": 1, "status": report["status"], "style_heads": raw_style_heads}
        progress(90, "Writing character evidence…")
        progress(100, "Character evidence ready")
        emit({"type": "done", "result": {"text": json.dumps(report, ensure_ascii=False, indent=2), "metadata": metadata}})
    except Exception as exc:
        error(f"character-evidence: {exc}")


if __name__ == "__main__":
    main()
