"""Structural rigging evidence nodes.

The attachment-anchor audit is intentionally declarative: it checks a component
tree and optional measured world positions, but it never silently invents a
parent, bone, or transform relationship.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from typing import Any


ATTACHMENT_TOKENS = {"attachment", "accessory", "worn", "held", "prop", "equipment"}
EXTENT_FIELDS = ("width", "height", "depth", "length")
DEFAULT_MAX_OFFSET = 0.3
ANCHOR_SIZE_FRACTION = 0.25
RIG_WEIGHT_TOLERANCE = 1e-4
RIG_MAX_INFLUENCES = 4


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _xyz(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_is_number(item) for item in value)


def _distance(first: list[float], second: list[float]) -> float:
    return math.sqrt(sum((float(first[index]) - float(second[index])) ** 2 for index in range(3)))


def _attachment_anchor(component: dict[str, Any]) -> str | None:
    attachment = component.get("attachment")
    if isinstance(attachment, dict) and isinstance(attachment.get("anchor"), str) and attachment["anchor"].strip():
        return str(attachment["anchor"]).strip()
    return None


def _is_attachment(component: dict[str, Any]) -> bool:
    if _attachment_anchor(component):
        return True
    return any(str(component.get(field) or "").strip().lower() in ATTACHMENT_TOKENS for field in ("role", "category", "kind"))


def _anchor_extent(component: dict[str, Any] | None) -> float | None:
    dimensions = component.get("dimensions") if isinstance(component, dict) else None
    if not isinstance(dimensions, dict):
        return None
    values = [float(dimensions[field]) for field in EXTENT_FIELDS if _is_number(dimensions.get(field))]
    if _is_number(dimensions.get("radius")):
        values.append(float(dimensions["radius"]) * 2.0)
    return max(values) if values else None


def _max_offset(component: dict[str, Any], anchor: dict[str, Any] | None) -> tuple[float, str]:
    attachment = component.get("attachment") if isinstance(component.get("attachment"), dict) else {}
    if _is_number(attachment.get("maxOffset")):
        return float(attachment["maxOffset"]), "declared attachment.maxOffset"
    extent = _anchor_extent(anchor)
    if extent is not None:
        return extent * ANCHOR_SIZE_FRACTION, f"default: {ANCHOR_SIZE_FRACTION:.0%} of anchor extent ({extent:.4f})"
    return DEFAULT_MAX_OFFSET, f"default: constant fallback ({DEFAULT_MAX_OFFSET})"


def _cycle(start: str, anchors: dict[str, str]) -> list[str] | None:
    path: list[str] = []
    current = start
    while current in anchors:
        if current in path:
            return path[path.index(current):]
        path.append(current)
        current = anchors[current]
    return None


def _analyze(payload: dict[str, Any]) -> dict[str, Any]:
    components = [item for item in payload.get("componentTree", []) if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()]
    by_id = {str(item["id"]): item for item in components}
    rig = payload.get("rig") if isinstance(payload.get("rig"), dict) else {}
    bones = [item for item in rig.get("bones", []) if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()]
    bones_by_id = {str(item["id"]): item for item in bones}
    roots = [str(item["id"]) for item in components if not item.get("parent")]
    bone_roots = [str(item["id"]) for item in bones if not item.get("parent")]
    attachments = [item for item in components if _is_attachment(item)]
    anchors: dict[str, str] = {}
    errors: list[str] = []
    warnings: list[str] = []
    records: list[dict[str, Any]] = []

    for component in attachments:
        component_id = str(component["id"])
        anchor = _attachment_anchor(component)
        record: dict[str, Any] = {
            "component": component_id,
            "anchor": anchor,
            "anchorKind": None,
            "distance": None,
            "maxOffset": None,
            "offsetRule": None,
            "withinLimit": None,
        }
        if anchor is None:
            errors.append(f"ANCHOR_DECLARED: attachment {component_id!r} has no attachment.anchor")
            records.append(record)
            continue
        anchors[component_id] = anchor
        if anchor in by_id:
            record["anchorKind"] = "component"
        elif anchor in bones_by_id:
            record["anchorKind"] = "bone"
        else:
            errors.append(f"ANCHOR_RESOLVES: attachment {component_id!r} anchors to unknown target {anchor!r}")
            records.append(record)
            continue
        if (record["anchorKind"] == "component" and anchor in roots) or (record["anchorKind"] == "bone" and anchor in bone_roots):
            errors.append(f"ANCHOR_NOT_ROOT: attachment {component_id!r} anchors directly to model root {anchor!r}")
        records.append(record)

    reported_cycles: set[frozenset[str]] = set()
    for start in anchors:
        cycle = _cycle(start, anchors)
        if not cycle:
            continue
        key = frozenset(cycle)
        if key in reported_cycles:
            continue
        reported_cycles.add(key)
        chain = " -> ".join([*cycle, cycle[0]])
        errors.append(f"ANCHOR_NOT_CYCLIC: attachment anchor cycle detected: {chain}")

    measured = payload.get("measured") if isinstance(payload.get("measured"), dict) else None
    unmeasured: list[str] = []
    if measured is not None:
        for record in records:
            if record["anchorKind"] is None:
                continue
            component_id = str(record["component"])
            anchor = str(record["anchor"])
            item_position = measured.get(component_id)
            anchor_position = measured.get(anchor)
            if not _xyz(item_position) or not _xyz(anchor_position):
                unmeasured.append(component_id)
                warnings.append(f"ANCHOR_PROXIMITY: {component_id!r} or anchor {anchor!r} has no measured world position")
                continue
            anchor_component = by_id.get(anchor) if record["anchorKind"] == "component" else None
            maximum, rule = _max_offset(by_id[component_id], anchor_component)
            distance = _distance(item_position, anchor_position)
            record.update({"distance": round(distance, 6), "maxOffset": round(maximum, 6), "offsetRule": rule, "withinLimit": distance <= maximum})
            if distance > maximum:
                errors.append(f"ANCHOR_PROXIMITY: attachment {component_id!r} is {distance:.4f} from {anchor!r}, exceeding {maximum:.4f}")

    status = "fail" if errors else "needs_review" if unmeasured else "pass"
    return {
        "schemaVersion": 1,
        "kind": "polykit.attachment-anchor-audit",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "attachments": records,
        "attachmentCount": len(attachments),
        "unmeasuredAttachments": unmeasured,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "componentCount": len(components),
            "boneCount": len(bones),
            "proximityEvaluated": measured is not None,
            "cycleCount": len(reported_cycles),
        },
        "passed": not errors and not unmeasured,
        "reviewNotes": [
            "Worn, held, and hung components must resolve to a concrete component or bone rather than the model root.",
            "Measured positions are optional; when supplied, missing measurements remain visible as needs_review.",
        ],
    }


def _rig_vector(value: Any, label: str, errors: list[str]) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) != 3 or not all(_is_number(item) for item in value):
        errors.append(f"{label} must be a finite length-3 vector")
        return None
    return tuple(float(item) for item in value)


def _rig_matrix(value: Any, label: str, errors: list[str]) -> bool:
    if isinstance(value, list) and len(value) == 16 and all(_is_number(item) for item in value):
        flat = [float(item) for item in value]
    elif isinstance(value, list) and len(value) == 4 and all(isinstance(row, list) and len(row) == 4 for row in value) and all(_is_number(item) for row in value for item in row):
        flat = [float(item) for row in value for item in row]
    else:
        errors.append(f"{label} must be a finite 4x4 or length-16 matrix")
        return False
    if any(abs(actual - expected) > RIG_WEIGHT_TOLERANCE for actual, expected in zip(flat[12:16], (0.0, 0.0, 0.0, 1.0))):
        errors.append(f"{label} must have affine last row [0, 0, 0, 1]")
    for column in range(3):
        scale = math.sqrt(sum(flat[row * 4 + column] ** 2 for row in range(3)))
        if scale <= 1e-6:
            errors.append(f"{label} contains a zero-scale basis column")
    return True


def _analyze_rig_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the portable skeleton/skin payload before file export."""
    errors: list[str] = []
    warnings: list[str] = []
    if payload.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    coordinate = payload.get("coordinateSystem")
    if not isinstance(coordinate, dict):
        errors.append("coordinateSystem is required")
    else:
        if coordinate.get("up") != "Y":
            errors.append("coordinateSystem.up must be Y")
        if coordinate.get("handedness") != "right":
            errors.append("coordinateSystem.handedness must be right")
        if not isinstance(coordinate.get("unit"), str) or not coordinate["unit"].strip():
            errors.append("coordinateSystem.unit must be a non-empty string")

    joints_raw = payload.get("joints")
    joints: list[tuple[float, float, float]] = []
    if not isinstance(joints_raw, list) or not joints_raw:
        errors.append("joints must be a non-empty list")
        joints_raw = []
    else:
        for index, value in enumerate(joints_raw):
            parsed = _rig_vector(value, f"joints[{index}]", errors)
            if parsed is not None:
                joints.append(parsed)
    joint_count = len(joints_raw)

    parents = payload.get("parents")
    if not isinstance(parents, list) or len(parents) != joint_count:
        errors.append("parents must have one entry per joint")
        parents = parents if isinstance(parents, list) else []
    elif parents and parents[0] is not None:
        errors.append("parents[0] must be null for the single root")
    for index, parent in enumerate(parents[1:], start=1):
        if not isinstance(parent, int) or isinstance(parent, bool):
            errors.append(f"parents[{index}] must be an integer parent index")
        elif parent < 0 or parent >= index:
            errors.append(f"parents[{index}] must satisfy 0 <= parent < child index")
        elif index < len(joints) and parent < len(joints) and _distance(list(joints[index]), list(joints[parent])) <= 1e-6:
            errors.append(f"joint {index} has a zero-length parent bone {parent}")

    names = payload.get("names")
    if not isinstance(names, list) or len(names) != joint_count:
        errors.append("names must have one entry per joint")
        names = names if isinstance(names, list) else []
    seen_names: set[str] = set()
    for index, name in enumerate(names):
        if not isinstance(name, str) or not name.strip():
            errors.append(f"names[{index}] must be a non-empty string")
        elif name in seen_names:
            errors.append(f"duplicate joint name: {name}")
        seen_names.add(str(name))

    matrices = payload.get("matrix_local")
    if not isinstance(matrices, list) or len(matrices) != joint_count:
        errors.append("matrix_local must have one matrix per joint")
        matrices = matrices if isinstance(matrices, list) else []
    for index, matrix in enumerate(matrices):
        _rig_matrix(matrix, f"matrix_local[{index}]", errors)

    skin_indices = payload.get("skinIndex")
    skin_weights = payload.get("skinWeight")
    if not isinstance(skin_indices, list) or not isinstance(skin_weights, list):
        errors.append("skinIndex and skinWeight are required packed arrays")
        skin_indices = skin_indices if isinstance(skin_indices, list) else []
        skin_weights = skin_weights if isinstance(skin_weights, list) else []
    if len(skin_indices) != len(skin_weights):
        errors.append("skinIndex and skinWeight must have the same vertex count")
    vertex_count = min(len(skin_indices), len(skin_weights))
    active_counts = [0] * joint_count
    for vertex in range(vertex_count):
        indices = skin_indices[vertex]
        weights = skin_weights[vertex]
        if not isinstance(indices, list) or len(indices) != RIG_MAX_INFLUENCES:
            errors.append(f"skinIndex[{vertex}] must have exactly {RIG_MAX_INFLUENCES} slots")
            continue
        if not isinstance(weights, list) or len(weights) != RIG_MAX_INFLUENCES:
            errors.append(f"skinWeight[{vertex}] must have exactly {RIG_MAX_INFLUENCES} slots")
            continue
        for slot, joint in enumerate(indices):
            if not isinstance(joint, int) or isinstance(joint, bool) or joint < 0 or joint >= joint_count:
                errors.append(f"skinIndex[{vertex}][{slot}] points outside the joint array")
            elif _is_number(weights[slot]) and float(weights[slot]) > 1e-6:
                active_counts[joint] += 1
        if not all(_is_number(weight) and float(weight) >= 0.0 for weight in weights):
            errors.append(f"skinWeight[{vertex}] must contain finite non-negative values")
        else:
            total = sum(float(weight) for weight in weights)
            if abs(total - 1.0) > RIG_WEIGHT_TOLERANCE:
                errors.append(f"skinWeight[{vertex}] must sum to 1 (got {total:.6f})")
            if total <= 1e-6:
                errors.append(f"skinWeight[{vertex}] must influence at least one joint")
    if isinstance(names, list):
        unweighted = [str(names[index]) if index < len(names) else str(index) for index, count in enumerate(active_counts) if count == 0]
        if unweighted:
            warnings.append("joints with no active vertex influence: " + ", ".join(unweighted))

    return {
        "schemaVersion": 1,
        "kind": "polykit.rig-payload-audit",
        "status": "pass" if not errors else "fail",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {"jointCount": joint_count, "vertexCount": vertex_count, "maxInfluences": RIG_MAX_INFLUENCES, "activeVertexCountByJoint": active_counts},
        "reviewNotes": [
            "This gate validates the portable skeleton and skin payload; runtime deformation and screenshot checks remain separate.",
            "Unweighted joints are warnings because attachment-only bones may intentionally have no skinned vertices.",
        ],
    }


def _chirality_point(value: Any, label: str, errors: list[str]) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) != 3 or not all(_is_number(item) for item in value):
        errors.append(f"{label} must be a finite length-3 point")
        return None
    return tuple(float(item) for item in value)


def _mirror_point(point: tuple[float, float, float]) -> tuple[float, float, float]:
    # PolyKit's portable rig convention is Y-up, right-handed, forward +Z. A sagittal mirror
    # negates the lateral X axis only; negating Z too is a 180-degree Y rotation and preserves the
    # wrong hand.
    return (-point[0], point[1], point[2])


def _classify_chirality(right: tuple[float, float, float], left: tuple[float, float, float], tolerance: float = 1e-6) -> str:
    def close(expected: tuple[float, float, float]) -> bool:
        return all(abs(actual - target) <= tolerance for actual, target in zip(left, expected))

    if close(_mirror_point(right)):
        return "reflection"
    if close((-right[0], right[1], -right[2])):
        return "rotation"
    if close(right):
        return "translation"
    return "unrelated"


def _analyze_chirality(payload: dict[str, Any]) -> dict[str, Any]:
    """Check that declared left/right landmarks are true sagittal reflections."""
    errors: list[str] = []
    warnings: list[str] = []
    raw_pairs = payload.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        errors.append("pairs must be a non-empty list")
        raw_pairs = []
    records: list[dict[str, Any]] = []
    for index, raw_pair in enumerate(raw_pairs):
        if not isinstance(raw_pair, dict):
            errors.append(f"pairs[{index}] must be an object")
            continue
        stem = str(raw_pair.get("stem") or raw_pair.get("id") or f"pair-{index + 1}").strip() or f"pair-{index + 1}"
        right = _chirality_point(raw_pair.get("right"), f"pairs[{index}].right", errors)
        left = _chirality_point(raw_pair.get("left"), f"pairs[{index}].left", errors)
        if right is None or left is None:
            continue
        relation = _classify_chirality(right, left)
        expected = _mirror_point(right)
        record = {
            "stem": stem,
            "right": [round(value, 6) for value in right],
            "left": [round(value, 6) for value in left],
            "expectedLeft": [round(value, 6) for value in expected],
            "relation": relation,
            "passed": relation == "reflection",
        }
        records.append(record)
        if relation != "reflection":
            errors.append(f"{stem}: left/right landmarks form {relation}, not a sagittal reflection; negate lateral X only")

    raw_points = payload.get("points")
    symmetry_error = None
    if raw_points is not None:
        points: list[tuple[float, float, float]] = []
        if not isinstance(raw_points, list):
            errors.append("points must be a list when supplied")
        else:
            for index, value in enumerate(raw_points):
                point = _chirality_point(value, f"points[{index}]", errors)
                if point is not None:
                    points.append(point)
        if points:
            mirrored = [_mirror_point(point) for point in points]
            total = 0.0
            for point in points:
                nearest = min(sum((candidate[axis] - point[axis]) ** 2 for axis in range(3)) for candidate in mirrored)
                total += nearest
            symmetry_error = round(math.sqrt(total / len(points)), 6)
            if symmetry_error > 0.25:
                warnings.append(f"whole-figure sagittal symmetry error is {symmetry_error:.4f}; deliberate asymmetry may be valid")

    status = "fail" if errors else "pass" if records else "needs_review"
    return {
        "schemaVersion": 1,
        "kind": "polykit.chirality-audit",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "passed": not errors and bool(records),
        "pairs": records,
        "pairCount": len(records),
        "errors": errors,
        "warnings": warnings,
        "summary": {"symmetryError": symmetry_error, "lateralAxis": "X", "mirrorRule": "left=(-right.x, right.y, right.z)"},
        "reviewNotes": [
            "A correct pair is a sagittal reflection: negate X only in the Y-up, right-handed +Z-forward convention.",
            "Whole-figure symmetry is reported, not gated, because intentional asymmetry is legitimate.",
        ],
    }


def main() -> None:
    try:
        payload = json.loads(sys.stdin.readline())
        input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        text = input_data.get("text")
        if not isinstance(text, str) or not text.strip():
            error("rigging-evidence: attachment anchor audit requires a JSON text descriptor")
            return
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        node_id = str(params.get("_node_id") or "attachment-anchor-audit")
        if node_id not in {"attachment-anchor-audit", "rig-payload-audit", "chirality-audit"}:
            error(f"rigging-evidence: unsupported node '{node_id}'")
            return
        descriptor = json.loads(text)
        if not isinstance(descriptor, dict):
            raise ValueError("attachment descriptor must be a JSON object")
        progress(5, "Reading rigging evidence…")
        if node_id == "attachment-anchor-audit":
            report = _analyze(descriptor)
        elif node_id == "rig-payload-audit":
            report = _analyze_rig_payload(descriptor)
        else:
            report = _analyze_chirality(descriptor)
        progress(90, "Writing rigging evidence…")
        progress(100, "Rigging evidence ready")
        metadata = {"evidence_kind": node_id, "schema_version": 1, "status": report["status"]}
        if node_id == "attachment-anchor-audit":
            metadata["attachment_count"] = report["attachmentCount"]
        elif node_id == "rig-payload-audit":
            metadata["joint_count"] = report["summary"]["jointCount"]
        else:
            metadata["pair_count"] = report["pairCount"]
        emit({"type": "done", "result": {"text": json.dumps(report, ensure_ascii=False, indent=2), "metadata": metadata}})
    except Exception as exc:
        error(f"rigging-evidence: {exc}")


if __name__ == "__main__":
    main()
