"""Structural rigging evidence nodes.

The attachment-anchor audit is intentionally declarative: it checks a component
tree and optional measured world positions, but it never silently invents a
parent, bone, or transform relationship.
"""
from __future__ import annotations

import json
import heapq
import math
import sys
from collections import deque
from datetime import datetime, timezone
from typing import Any


ATTACHMENT_TOKENS = {"attachment", "accessory", "worn", "held", "prop", "equipment"}
EXTENT_FIELDS = ("width", "height", "depth", "length")
DEFAULT_MAX_OFFSET = 0.3
ANCHOR_SIZE_FRACTION = 0.25
RIG_WEIGHT_TOLERANCE = 1e-4
RIG_MAX_INFLUENCES = 4
GEODESIC_MAX_RESOLUTION = 96
GEODESIC_RIGID_ROLES = frozenset({"hair", "detail", "decal", "panel"})
GEODESIC_NEIGHBOURS = [
    (dx, dy, dz, math.sqrt(dx * dx + dy * dy + dz * dz))
    for dx in (-1, 0, 1)
    for dy in (-1, 0, 1)
    for dz in (-1, 0, 1)
    if (dx, dy, dz) != (0, 0, 0)
]


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


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


def _geodesic_triangles(indices: Any) -> list[tuple[int, int, int]]:
    if not isinstance(indices, list):
        raise ValueError("mesh.indices must be a list")
    if indices and isinstance(indices[0], (list, tuple)):
        triangles = []
        for index, triangle in enumerate(indices):
            if len(triangle) != 3:
                raise ValueError(f"mesh.indices[{index}] must contain three vertex indices")
            triangles.append((int(triangle[0]), int(triangle[1]), int(triangle[2])))
        return triangles
    if len(indices) < 3 or len(indices) % 3:
        raise ValueError("mesh.indices must contain complete triangles")
    return [(int(indices[index]), int(indices[index + 1]), int(indices[index + 2])) for index in range(0, len(indices), 3)]


class _GeodesicVoxelGrid:
    """A padded voxel solid used to route distance through the mesh volume."""

    def __init__(self, vertices: list[list[float]], faces: list[tuple[int, int, int]], resolution: int):
        low = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
        high = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
        extent = max(high[axis] - low[axis] for axis in range(3)) or 1.0
        self.step = extent / resolution
        self.low = [low[axis] - self.step for axis in range(3)]
        self.dims = [int(math.ceil((high[axis] - low[axis]) / self.step)) + 3 for axis in range(3)]
        self.surface: set[tuple[int, int, int]] = set()
        for face in faces:
            self._rasterize(vertices, face)
        self.solid = self._fill_interior()

    def index_of(self, point: list[float] | tuple[float, float, float]) -> tuple[int, int, int]:
        return tuple(  # type: ignore[return-value]
            min(self.dims[axis] - 1, max(0, int((float(point[axis]) - self.low[axis]) / self.step)))
            for axis in range(3)
        )

    def _rasterize(self, vertices: list[list[float]], face: tuple[int, int, int]) -> None:
        try:
            p0, p1, p2 = (vertices[index] for index in face)
        except (IndexError, TypeError) as exc:
            raise ValueError(f"mesh face references an invalid vertex: {face}") from exc
        longest = max(math.dist(p0, p1), math.dist(p1, p2), math.dist(p2, p0))
        steps = max(1, int(math.ceil(longest / (self.step * 0.5))))
        for first in range(steps + 1):
            for second in range(steps + 1 - first):
                a = first / steps
                b = second / steps
                c = 1.0 - a - b
                point = [p0[axis] * c + p1[axis] * a + p2[axis] * b for axis in range(3)]
                self.surface.add(self.index_of(point))

    def _fill_interior(self) -> set[tuple[int, int, int]]:
        outside: set[tuple[int, int, int]] = set()
        start = (0, 0, 0)
        queue = deque([start])
        outside.add(start)
        while queue:
            x, y, z = queue.popleft()
            for dx, dy, dz, _cost in GEODESIC_NEIGHBOURS:
                cell = (x + dx, y + dy, z + dz)
                if not all(0 <= cell[axis] < self.dims[axis] for axis in range(3)):
                    continue
                if cell in outside or cell in self.surface:
                    continue
                outside.add(cell)
                queue.append(cell)
        solid = set(self.surface)
        for x in range(self.dims[0]):
            for y in range(self.dims[1]):
                for z in range(self.dims[2]):
                    cell = (x, y, z)
                    if cell not in outside:
                        solid.add(cell)
        return solid


def _geodesic_segment_voxels(grid: _GeodesicVoxelGrid, start: list[float], end: list[float]) -> set[tuple[int, int, int]]:
    length = math.dist(start, end)
    steps = max(1, int(math.ceil(length / (grid.step * 0.5))))
    return {
        grid.index_of([start[axis] + (end[axis] - start[axis]) * index / steps for axis in range(3)])
        for index in range(steps + 1)
    }


def _geodesic_field(grid: _GeodesicVoxelGrid, sources: set[tuple[int, int, int]]) -> dict[tuple[int, int, int], float]:
    distances: dict[tuple[int, int, int], float] = {}
    heap: list[tuple[float, tuple[int, int, int]]] = []
    for cell in sources:
        if cell in grid.solid:
            distances[cell] = 0.0
            heap.append((0.0, cell))
    heapq.heapify(heap)
    while heap:
        current, cell = heapq.heappop(heap)
        if current > distances.get(cell, float("inf")):
            continue
        x, y, z = cell
        for dx, dy, dz, cost in GEODESIC_NEIGHBOURS:
            neighbour = (x + dx, y + dy, z + dz)
            if neighbour not in grid.solid:
                continue
            candidate = current + cost
            if candidate < distances.get(neighbour, float("inf")):
                distances[neighbour] = candidate
                heapq.heappush(heap, (candidate, neighbour))
    return distances


def _geodesic_euclidean_distance(point: list[float], start: list[float], end: list[float]) -> float:
    direction = [end[axis] - start[axis] for axis in range(3)]
    offset = [point[axis] - start[axis] for axis in range(3)]
    denominator = sum(value * value for value in direction)
    factor = 0.0 if denominator <= 1e-12 else max(0.0, min(1.0, sum(offset[axis] * direction[axis] for axis in range(3)) / denominator))
    closest = [start[axis] + direction[axis] * factor for axis in range(3)]
    return math.dist(point, closest)


def _geodesic_partition(components: Any) -> tuple[dict[str, str], list[str]]:
    if not isinstance(components, list):
        return {}, []
    by_id = {str(item.get("id")): item for item in components if isinstance(item, dict) and item.get("id")}
    warnings: list[str] = []
    rigid: dict[str, str] = {}
    for component in by_id.values():
        component_id = str(component["id"])
        if str(component.get("role") or "").lower() not in GEODESIC_RIGID_ROLES:
            continue
        parent = component.get("parent")
        seen: set[str] = set()
        joint = None
        while isinstance(parent, str) and parent in by_id and parent not in seen:
            seen.add(parent)
            ancestor = by_id[parent]
            if str(ancestor.get("role") or "").lower() not in GEODESIC_RIGID_ROLES:
                joint = parent
                break
            parent = ancestor.get("parent")
        if joint is None:
            warnings.append(f"component {component_id!r} has a rigid role but no skinned ancestor")
        else:
            rigid[component_id] = joint
    return rigid, warnings


def _analyze_geodesic_bind(payload: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    mesh = payload.get("mesh") if isinstance(payload.get("mesh"), dict) else payload
    raw_vertices = mesh.get("vertices") if isinstance(mesh, dict) else None
    raw_indices = mesh.get("indices") if isinstance(mesh, dict) else None
    if not isinstance(raw_vertices, list) or not raw_vertices:
        raise ValueError("mesh.vertices must be a non-empty list")
    vertices: list[list[float]] = []
    for index, raw_vertex in enumerate(raw_vertices):
        if not isinstance(raw_vertex, list) or len(raw_vertex) != 3 or not all(_is_number(value) for value in raw_vertex):
            raise ValueError(f"mesh.vertices[{index}] must be a finite length-3 point")
        vertices.append([float(value) for value in raw_vertex])
    faces = _geodesic_triangles(raw_indices)
    if not faces:
        raise ValueError("mesh.indices must describe at least one triangle")
    bones_raw = payload.get("bones")
    if not isinstance(bones_raw, list) or not bones_raw:
        raise ValueError("bones must be a non-empty list")
    bones: list[dict[str, Any]] = []
    seen_bones: set[str] = set()
    for index, raw_bone in enumerate(bones_raw):
        if not isinstance(raw_bone, dict) or not isinstance(raw_bone.get("id"), str) or not str(raw_bone["id"]).strip():
            raise ValueError(f"bones[{index}].id must be a non-empty string")
        bone_id = str(raw_bone["id"])
        if bone_id in seen_bones:
            raise ValueError(f"duplicate bone id: {bone_id}")
        seen_bones.add(bone_id)
        joint = raw_bone.get("jointPos")
        tip = raw_bone.get("tipPos")
        if not isinstance(joint, list) or len(joint) != 3 or not all(_is_number(value) for value in joint):
            raise ValueError(f"bones[{index}].jointPos must be a finite length-3 point")
        if not isinstance(tip, list) or len(tip) != 3 or not all(_is_number(value) for value in tip):
            raise ValueError(f"bones[{index}].tipPos must be a finite length-3 point")
        bones.append({"id": bone_id, "jointPos": [float(value) for value in joint], "tipPos": [float(value) for value in tip]})
    resolution = _bounded_int(params.get("resolution", payload.get("resolution", 24)), 24, 8, GEODESIC_MAX_RESOLUTION)
    try:
        falloff = float(params.get("falloff_power", payload.get("falloffPower", 3.0)) or 3.0)
    except (TypeError, ValueError):
        falloff = 3.0
    falloff = max(0.5, min(8.0, falloff))
    grid = _GeodesicVoxelGrid(vertices, faces, resolution)
    fields: list[dict[tuple[int, int, int], float]] = []
    unreachable_bones: list[str] = []
    for bone in bones:
        field = _geodesic_field(grid, _geodesic_segment_voxels(grid, bone["jointPos"], bone["tipPos"]))
        if not field:
            unreachable_bones.append(str(bone["id"]))
        fields.append(field)
    skin_indices: list[list[int]] = []
    skin_weights: list[list[float]] = []
    unreachable_vertices = 0
    for vertex in vertices:
        cell = grid.index_of(vertex)
        scored: list[tuple[float, int]] = []
        for bone_index, field in enumerate(fields):
            distance = field.get(cell)
            if distance is not None:
                scored.append((1.0 / max(distance, 0.5) ** falloff, bone_index))
        if not scored:
            unreachable_vertices += 1
            skin_indices.append([0, 0, 0, 0])
            skin_weights.append([1.0, 0.0, 0.0, 0.0])
            continue
        scored.sort(reverse=True)
        kept = scored[:RIG_MAX_INFLUENCES]
        total = sum(weight for weight, _bone_index in kept) or 1.0
        row_indices = [0, 0, 0, 0]
        row_weights = [0.0, 0.0, 0.0, 0.0]
        for slot, (weight, bone_index) in enumerate(kept):
            row_indices[slot] = bone_index
            row_weights[slot] = weight / total
        skin_indices.append(row_indices)
        skin_weights.append(row_weights)

    rigid_targets, partition_warnings = _geodesic_partition(payload.get("components"))
    owners = mesh.get("vertexComponents") if isinstance(mesh, dict) else None
    rigid_pinned = 0
    bone_slots = {bone["id"]: index for index, bone in enumerate(bones)}
    if rigid_targets and not isinstance(owners, list):
        partition_warnings.append("rigid components were detected but mesh.vertexComponents is missing; no vertices were repinned")
    elif rigid_targets and isinstance(owners, list) and len(owners) != len(vertices):
        partition_warnings.append("mesh.vertexComponents must contain one component id per vertex; no vertices were repinned")
    elif rigid_targets and isinstance(owners, list):
        for index, owner in enumerate(owners):
            target = rigid_targets.get(str(owner))
            slot = bone_slots.get(target) if target else None
            if slot is None:
                continue
            skin_indices[index] = [slot, 0, 0, 0]
            skin_weights[index] = [1.0, 0.0, 0.0, 0.0]
            rigid_pinned += 1
    max_weight_error = max((abs(sum(row) - 1.0) for row in skin_weights), default=0.0)
    warnings = list(partition_warnings)
    if unreachable_bones:
        warnings.append("one or more bones did not reach the voxel solid")
    if unreachable_vertices:
        warnings.append("unreachable vertices were pinned to bone 0 for deterministic output; inspect mesh connectivity")
    status = "pass" if not unreachable_bones and not unreachable_vertices else "needs_review"
    return {
        "schemaVersion": 1,
        "kind": "polykit.geodesic-bind",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "passed": status == "pass",
        "boneOrder": [bone["id"] for bone in bones],
        "skinIndices": skin_indices,
        "skinWeights": [[round(weight, 9) for weight in row] for row in skin_weights],
        "summary": {
            "vertexCount": len(vertices),
            "triangleCount": len(faces),
            "jointCount": len(bones),
            "resolution": resolution,
            "falloffPower": round(falloff, 6),
            "solidVoxelCount": len(grid.solid),
            "surfaceVoxelCount": len(grid.surface),
            "unreachableVertexCount": unreachable_vertices,
            "unreachableBones": unreachable_bones,
            "rigidPinnedVertexCount": rigid_pinned,
            "maxWeightError": round(max_weight_error, 12),
        },
        "warnings": warnings,
        "reviewNotes": [
            "Distances are propagated through the voxelized solid, so nearby limbs separated by air do not exchange weights through the gap.",
            "Hair, decals, panels, and other rigid-role components should be supplied with vertexComponents so they can be pinned to an ancestor joint instead of smooth-skinned.",
        ],
    }


def _analyze_ik_solve(payload: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Solve one explicitly ordered joint chain with the FABRIK algorithm."""
    raw_chain = payload.get("chain")
    errors: list[str] = []
    if not isinstance(raw_chain, list) or len(raw_chain) < 2:
        raise ValueError("chain must contain at least two ordered joints")
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_joint in enumerate(raw_chain):
        if not isinstance(raw_joint, dict) or not isinstance(raw_joint.get("id"), str) or not str(raw_joint["id"]).strip():
            raise ValueError(f"chain[{index}].id must be a non-empty string")
        joint_id = str(raw_joint["id"]).strip()
        if joint_id in seen:
            raise ValueError(f"duplicate chain joint: {joint_id}")
        seen.add(joint_id)
        position = raw_joint.get("position")
        if not isinstance(position, list) or len(position) != 3 or not all(_is_number(value) for value in position):
            raise ValueError(f"chain[{index}].position must be a finite length-3 point")
        chain.append({"id": joint_id, "position": [float(value) for value in position]})
    target = payload.get("target")
    if not isinstance(target, list) or len(target) != 3 or not all(_is_number(value) for value in target):
        raise ValueError("target must be a finite length-3 point")
    target_point = [float(value) for value in target]
    try:
        tolerance = max(1e-6, min(1.0, float(params.get("tolerance", 0.001) or 0.001)))
    except (TypeError, ValueError):
        tolerance = 0.001
    iterations = _bounded_int(params.get("iterations", 32), 32, 1, 256)
    points = [list(joint["position"]) for joint in chain]
    lengths = [_distance(points[index], points[index + 1]) for index in range(len(points) - 1)]
    if any(length <= 1e-9 for length in lengths):
        raise ValueError("adjacent chain joints must have non-zero distance")
    root = points[0][:]
    target_distance = _distance(root, target_point)
    reach = sum(lengths)
    unreachable = target_distance > reach + tolerance
    used = 0
    if unreachable:
        direction = [(target_point[axis] - root[axis]) / max(target_distance, 1e-9) for axis in range(3)]
        for index, length in enumerate(lengths):
            points[index + 1] = [points[index][axis] + direction[axis] * length for axis in range(3)]
    else:
        for used in range(1, iterations + 1):
            points[-1] = target_point[:]
            for index in range(len(points) - 2, -1, -1):
                distance = _distance(points[index], points[index + 1])
                factor = lengths[index] / max(distance, 1e-9)
                points[index] = [points[index + 1][axis] + (points[index][axis] - points[index + 1][axis]) * factor for axis in range(3)]
            points[0] = root[:]
            for index, length in enumerate(lengths):
                distance = _distance(points[index], points[index + 1])
                factor = length / max(distance, 1e-9)
                points[index + 1] = [points[index][axis] + (points[index + 1][axis] - points[index][axis]) * factor for axis in range(3)]
            if _distance(points[-1], target_point) <= tolerance:
                break
    end_error = _distance(points[-1], target_point)
    segment_errors = [abs(_distance(points[index], points[index + 1]) - lengths[index]) for index in range(len(lengths))]
    if not unreachable and end_error > tolerance:
        errors.append(f"FABRIK did not converge within {iterations} iterations (error {end_error:.6f})")
    status = "fail" if errors else "needs_review" if unreachable else "pass"
    return {
        "schemaVersion": 1,
        "kind": "polykit.ik-solve",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "root": chain[0]["id"],
        "end": chain[-1]["id"],
        "target": [round(value, 6) for value in target_point],
        "solvedChain": [{"id": chain[index]["id"], "position": [round(value, 6) for value in points[index]]} for index in range(len(chain))],
        "summary": {
            "jointCount": len(chain),
            "iterations": used,
            "tolerance": round(tolerance, 9),
            "targetDistance": round(target_distance, 6),
            "reach": round(reach, 6),
            "unreachable": unreachable,
            "endError": round(end_error, 9),
            "maxSegmentLengthError": round(max(segment_errors, default=0.0), 9),
        },
        "errors": errors,
        "reviewNotes": [
            "FABRIK solves joint positions for one ordered chain and preserves the source segment lengths.",
            "The report does not create a Blender armature, rotations, constraints, or collision-aware pose; inspect the solved pose before applying it to a rig.",
        ],
    }


def main() -> None:
    try:
        payload = json.loads(sys.stdin.readline())
        input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        text_value = input_data.get("text")
        if not isinstance(text_value, str) or not text_value.strip():
            error("rigging-evidence: a JSON text descriptor is required")
            return
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        node_id = str(params.get("_node_id") or "attachment-anchor-audit")
        if node_id not in {"attachment-anchor-audit", "rig-payload-audit", "geodesic-bind", "ik-solve"}:
            error(f"rigging-evidence: unsupported node '{node_id}'")
            return
        descriptor = json.loads(text_value)
        if not isinstance(descriptor, dict):
            raise ValueError("attachment descriptor must be a JSON object")
        progress(5, "Reading rigging evidence…")
        if node_id == "attachment-anchor-audit":
            report = _analyze(descriptor)
        elif node_id == "rig-payload-audit":
            report = _analyze_rig_payload(descriptor)
        elif node_id == "geodesic-bind":
            report = _analyze_geodesic_bind(descriptor, params)
        else:
            report = _analyze_ik_solve(descriptor, params)
        progress(90, "Writing rigging evidence…")
        progress(100, "Rigging evidence ready")
        metadata = {"evidence_kind": node_id, "schema_version": 1, "status": report["status"]}
        if node_id == "attachment-anchor-audit":
            metadata["attachment_count"] = report["attachmentCount"]
        elif node_id == "rig-payload-audit":
            metadata["joint_count"] = report["summary"]["jointCount"]
        elif node_id == "geodesic-bind":
            metadata["joint_count"] = report["summary"]["jointCount"]
            metadata["vertex_count"] = report["summary"]["vertexCount"]
        else:
            metadata["joint_count"] = report["summary"]["jointCount"]
            metadata["end_error"] = report["summary"]["endError"]
        emit({"type": "done", "result": {"text": json.dumps(report, ensure_ascii=False, indent=2), "metadata": metadata}})
    except Exception as exc:
        error(f"rigging-evidence: {exc}")

if __name__ == "__main__":
    main()
