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
        if node_id != "attachment-anchor-audit":
            error(f"rigging-evidence: unsupported node '{node_id}'")
            return
        descriptor = json.loads(text)
        if not isinstance(descriptor, dict):
            raise ValueError("attachment descriptor must be a JSON object")
        progress(5, "Reading attachment relationships…")
        report = _analyze(descriptor)
        progress(90, "Writing rigging evidence…")
        progress(100, "Attachment audit ready")
        emit({"type": "done", "result": {"text": json.dumps(report, ensure_ascii=False, indent=2), "metadata": {"evidence_kind": "attachment-anchor-audit", "schema_version": 1, "status": report["status"], "attachment_count": report["attachmentCount"]}}})
    except Exception as exc:
        error(f"rigging-evidence: {exc}")


if __name__ == "__main__":
    main()
