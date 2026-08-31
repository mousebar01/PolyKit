"""Deterministic spatial judge for World visual validation.

The judge cross-checks server-owned World facts against the final mesh artifact
published by a WorkflowRun. It reads the delivered GLB with trimesh instead of
parsing Blender files or trusting caller-authored spatial scores.

Camera facts, when supplied by a visual target, are treated as immutable target
constraints rather than workflow state. Volume checks only PASS when the final
mesh provides enough bounded, watertight evidence; otherwise they remain
needs_review/not_evaluated instead of inventing certainty.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from services.runtime_paths import runtime_paths
from services.workspace_paths import resolve_workspace_path


SPATIAL_SNAPSHOT_KIND = "polykit.spatial-snapshot"
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_MESH_SUFFIXES = {".glb", ".gltf", ".obj", ".ply", ".stl"}
_RAY_TRIANGLE_LIMIT = 250_000
_VOLUME_POINT_LIMIT = 512
_EPS = 1e-8


def _tokens(value: Any) -> set[str]:
    return {item.lower() for item in _TOKEN_RE.findall(str(value or "")) if item}


def _status(checks: Sequence[Mapping[str, Any]]) -> str:
    required = [item for item in checks if item.get("required") is True]
    if any(item.get("status") == "fail" for item in required):
        return "fail"
    if any(item.get("status") in {"needs_review", "not_evaluated"} for item in required):
        return "needs_review"
    return "pass" if required else "needs_review"


def _check(
    check_id: str,
    status: str,
    message: str,
    *,
    subjects: Sequence[str] | None = None,
    metrics: Mapping[str, Any] | None = None,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "category": "spatial",
        "judge": "spatial",
        "required": bool(required),
        "status": status,
        "subjects": list(subjects or []),
        "metrics": dict(metrics or {}),
        "evidence_refs": ["evidence:spatial-mesh"],
        "message": message,
    }


def _nonnegative_number(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) and result >= 0 else default


def _positive_number(value: Any, default: float) -> float:
    result = _nonnegative_number(value, default)
    return result if result > 0 else default


def _run_mesh_workspace_path(run: Mapping[str, Any] | None) -> str | None:
    if not isinstance(run, Mapping):
        return None
    meta = run.get("meta")
    if not isinstance(meta, Mapping):
        return None
    obs = meta.get("observability")
    if not isinstance(obs, Mapping):
        return None
    artifacts = obs.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for item in reversed(artifacts):
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "")
        workspace_path = item.get("workspace_path")
        if not isinstance(workspace_path, str) or not workspace_path.strip():
            continue
        suffix = Path(workspace_path).suffix.lower()
        if kind in {"mesh", "scene"} or suffix in _MESH_SUFFIXES:
            return workspace_path.strip()
    return None


def _corners(bounds: np.ndarray) -> np.ndarray:
    minimum, maximum = bounds
    return np.asarray(
        [
            [x, y, z]
            for x in (minimum[0], maximum[0])
            for y in (minimum[1], maximum[1])
            for z in (minimum[2], maximum[2])
        ],
        dtype=float,
    )


def _world_vertices(geometry: trimesh.Trimesh, transform: np.ndarray) -> np.ndarray:
    return trimesh.transform_points(np.asarray(geometry.vertices, dtype=float), transform)


def _world_triangles(geometry: trimesh.Trimesh, transform: np.ndarray) -> np.ndarray:
    vertices = _world_vertices(geometry, transform)
    return vertices[np.asarray(geometry.faces, dtype=int)]


def _scene_snapshot(
    path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
]:
    loaded = trimesh.load(path, force="scene", process=False)
    scene = loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)
    objects: list[dict[str, Any]] = []
    geometry_by_node: dict[str, list[tuple[trimesh.Trimesh, np.ndarray]]] = {}

    for node_name in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph.get(node_name)
        geometry = scene.geometry.get(geometry_name)
        if not isinstance(geometry, trimesh.Trimesh):
            continue
        matrix = np.asarray(transform, dtype=float)
        world_corners = trimesh.transform_points(
            _corners(np.asarray(geometry.bounds, dtype=float)),
            matrix,
        )
        minimum = world_corners.min(axis=0)
        maximum = world_corners.max(axis=0)
        geometry_by_node.setdefault(str(node_name), []).append((geometry, matrix))
        objects.append(
            {
                "id": str(node_name),
                "geometry": str(geometry_name),
                "bounds": {
                    "min": [float(value) for value in minimum],
                    "max": [float(value) for value in maximum],
                },
                "vertex_count": int(len(geometry.vertices)),
                "face_count": int(len(geometry.faces)),
                "watertight": bool(geometry.is_watertight),
                "convex": bool(geometry.is_convex),
            }
        )

    bounds = (
        np.asarray(scene.bounds, dtype=float)
        if scene.bounds is not None
        else np.zeros((2, 3), dtype=float)
    )
    snapshot = {
        "schema_version": 1,
        "kind": SPATIAL_SNAPSHOT_KIND,
        "coordinate_system": "gltf-y-up-meters",
        "objects": objects,
        "bounds": {
            "min": [float(value) for value in bounds[0]],
            "max": [float(value) for value in bounds[1]],
        },
        "geometry_count": len(objects),
    }
    return snapshot, geometry_by_node


def _matching_nodes(part_id: str, geometry_by_node: Mapping[str, Any]) -> list[str]:
    wanted = _tokens(part_id)
    if not wanted:
        return []
    matches: list[str] = []
    for node_name in geometry_by_node:
        candidate = _tokens(node_name)
        if wanted.issubset(candidate):
            matches.append(node_name)
    return matches


def _matching_world_object_nodes(
    object_value: Mapping[str, Any],
    geometry_by_node: Mapping[str, Any],
) -> list[str]:
    terms: list[str] = []
    for key in ("id", "name"):
        value = object_value.get(key)
        if isinstance(value, str) and value.strip():
            terms.append(value)
    aliases = object_value.get("aliases")
    if isinstance(aliases, list):
        terms.extend(str(value) for value in aliases if isinstance(value, str) and value.strip())

    best: list[str] = []
    for term in terms:
        matches = _matching_nodes(term, geometry_by_node)
        if matches:
            if not best or len(matches) < len(best):
                best = matches
    return best


def _canonical_point(value: Any) -> np.ndarray | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        return None
    point = np.asarray([float(item) for item in value], dtype=float)
    return point if np.isfinite(point).all() else None


def _normal(value: Any) -> np.ndarray | None:
    vector = _canonical_point(value)
    if vector is None:
        return None
    length = float(np.linalg.norm(vector))
    if length <= _EPS:
        return None
    return vector / length


def _point_surface_distance(
    point: np.ndarray,
    node_names: Sequence[str],
    geometry_by_node: Mapping[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
) -> float:
    best = math.inf
    for node_name in node_names:
        for geometry, transform in geometry_by_node.get(node_name, []):
            try:
                inverse = np.linalg.inv(transform)
                local_point = trimesh.transform_points(point.reshape(1, 3), inverse)
                _closest, distance, _triangle = trimesh.proximity.closest_point_naive(
                    geometry,
                    local_point,
                )
            except (ValueError, TypeError, np.linalg.LinAlgError):
                continue
            if len(distance):
                best = min(best, float(distance[0]))
    return best


def _single_world_mesh(
    node_names: Sequence[str],
    geometry_by_node: Mapping[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
) -> trimesh.Trimesh | None:
    entries: list[tuple[trimesh.Trimesh, np.ndarray]] = []
    for node_name in node_names:
        entries.extend(geometry_by_node.get(node_name, []))
    if len(entries) != 1:
        return None
    geometry, transform = entries[0]
    mesh = geometry.copy()
    mesh.apply_transform(transform)
    return mesh


def _sample_mesh_points(
    mesh: trimesh.Trimesh,
    *,
    include_centers: bool,
    limit: int = _VOLUME_POINT_LIMIT,
) -> tuple[np.ndarray, bool]:
    vertices = np.asarray(mesh.vertices, dtype=float)
    groups: list[np.ndarray] = [vertices]
    if include_centers and len(mesh.faces):
        triangles = np.asarray(mesh.triangles, dtype=float)
        groups.append(triangles.mean(axis=1))
    if len(vertices):
        groups.append(vertices.mean(axis=0, keepdims=True))
    points = np.vstack(groups) if groups else np.empty((0, 3), dtype=float)
    complete = len(points) <= limit
    if len(points) > limit:
        indices = np.linspace(0, len(points) - 1, limit, dtype=int)
        points = points[indices]
    return points, complete


def _ray_hits(
    origin: np.ndarray,
    direction: np.ndarray,
    triangles: np.ndarray,
    *,
    max_distance: float | None = None,
) -> np.ndarray:
    if not len(triangles):
        return np.empty(0, dtype=float)
    direction = np.asarray(direction, dtype=float)
    norm = float(np.linalg.norm(direction))
    if norm <= _EPS:
        return np.empty(0, dtype=float)
    direction = direction / norm

    v0 = triangles[:, 0]
    edge1 = triangles[:, 1] - v0
    edge2 = triangles[:, 2] - v0
    h = np.cross(np.broadcast_to(direction, edge2.shape), edge2)
    a = np.einsum("ij,ij->i", edge1, h)
    valid = np.abs(a) > _EPS
    if not np.any(valid):
        return np.empty(0, dtype=float)

    f = np.zeros_like(a)
    f[valid] = 1.0 / a[valid]
    s = np.broadcast_to(np.asarray(origin, dtype=float), v0.shape) - v0
    u = f * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, edge1)
    v = f * np.einsum("ij,j->i", q, direction)
    t = f * np.einsum("ij,ij->i", edge2, q)
    mask = (
        valid
        & (u >= -_EPS)
        & (u <= 1.0 + _EPS)
        & (v >= -_EPS)
        & ((u + v) <= 1.0 + _EPS)
        & (t > _EPS)
    )
    if max_distance is not None:
        mask &= t <= max_distance + 1e-6
    return np.asarray(t[mask], dtype=float)


def _unique_hit_count(distances: np.ndarray, *, epsilon: float = 1e-6) -> int:
    if not len(distances):
        return 0
    ordered = np.sort(np.asarray(distances, dtype=float))
    count = 1
    previous = float(ordered[0])
    for value in ordered[1:]:
        current = float(value)
        if abs(current - previous) > epsilon:
            count += 1
            previous = current
    return count


def _point_inside_or_near_volume(
    point: np.ndarray,
    mesh: trimesh.Trimesh,
    *,
    tolerance: float,
) -> bool:
    triangles = np.asarray(mesh.triangles, dtype=float)
    direction = np.asarray([1.0, 0.3713906763541037, 0.217128469], dtype=float)
    intersections = _ray_hits(point, direction, triangles)
    if _unique_hit_count(intersections) % 2 == 1:
        return True
    if tolerance <= 0:
        return False
    try:
        _closest, distance, _triangle = trimesh.proximity.closest_point_naive(
            mesh,
            point.reshape(1, 3),
        )
    except (ValueError, TypeError):
        return False
    return bool(len(distance) and float(distance[0]) <= tolerance + 1e-9)


def _inside_attachment_check(
    *,
    check_id: str,
    subjects: list[str],
    source_nodes: Sequence[str],
    target_nodes: Sequence[str],
    geometry_by_node: Mapping[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
    tolerance: float,
) -> dict[str, Any]:
    source_mesh = _single_world_mesh(source_nodes, geometry_by_node)
    target_mesh = _single_world_mesh(target_nodes, geometry_by_node)
    if source_mesh is None or target_mesh is None:
        return _check(
            check_id,
            "not_evaluated",
            "Inside validation requires one resolvable source mesh and one container mesh.",
            subjects=subjects,
        )
    if not target_mesh.is_watertight or not target_mesh.is_convex:
        return _check(
            check_id,
            "not_evaluated",
            "Inside validation only PASSes against a watertight convex final container mesh.",
            subjects=subjects,
            metrics={
                "target_watertight": bool(target_mesh.is_watertight),
                "target_convex": bool(target_mesh.is_convex),
            },
        )
    points, complete = _sample_mesh_points(source_mesh, include_centers=False)
    if not len(points):
        return _check(
            check_id,
            "not_evaluated",
            "Inside validation found no source vertices.",
            subjects=subjects,
        )
    inside = [
        _point_inside_or_near_volume(point, target_mesh, tolerance=tolerance)
        for point in points
    ]
    outside_count = sum(1 for value in inside if not value)
    if outside_count:
        status = "fail"
        message = "Final source geometry leaves the declared container volume."
    elif complete:
        status = "pass"
        message = "All final source vertices are inside/on the watertight convex container."
    else:
        status = "needs_review"
        message = "Sampled source vertices are inside, but the vertex set exceeded the bounded evidence budget."
    return _check(
        check_id,
        status,
        message,
        subjects=subjects,
        metrics={
            "sample_count": len(points),
            "complete_vertex_evidence": complete,
            "outside_count": outside_count,
            "tolerance": tolerance,
        },
    )


def _passes_through_attachment_check(
    *,
    check_id: str,
    subjects: list[str],
    source_nodes: Sequence[str],
    target_nodes: Sequence[str],
    geometry_by_node: Mapping[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
    anchor: np.ndarray | None,
    normal: np.ndarray | None,
    tolerance: float,
) -> dict[str, Any]:
    source_mesh = _single_world_mesh(source_nodes, geometry_by_node)
    target_mesh = _single_world_mesh(target_nodes, geometry_by_node)
    if source_mesh is None or target_mesh is None:
        return _check(
            check_id,
            "not_evaluated",
            "Pass-through validation requires one source mesh and one target volume mesh.",
            subjects=subjects,
        )
    if anchor is None or normal is None:
        return _check(
            check_id,
            "not_evaluated",
            "Pass-through validation requires an anchor position and directional normal.",
            subjects=subjects,
        )
    if not source_mesh.is_watertight:
        return _check(
            check_id,
            "not_evaluated",
            "Pass-through validation requires a watertight connected source mesh.",
            subjects=subjects,
            metrics={"source_watertight": bool(source_mesh.is_watertight)},
        )
    if not target_mesh.is_watertight or not target_mesh.is_convex:
        return _check(
            check_id,
            "not_evaluated",
            "Pass-through validation only resolves against a watertight convex target volume.",
            subjects=subjects,
            metrics={
                "target_watertight": bool(target_mesh.is_watertight),
                "target_convex": bool(target_mesh.is_convex),
            },
        )
    points, complete = _sample_mesh_points(source_mesh, include_centers=True)
    if not len(points):
        return _check(
            check_id,
            "not_evaluated",
            "Pass-through validation found no source samples.",
            subjects=subjects,
        )
    inside_flags = [
        _point_inside_or_near_volume(point, target_mesh, tolerance=tolerance)
        for point in points
    ]
    signed = np.dot(points - anchor.reshape(1, 3), normal)
    inside_count = sum(1 for value in inside_flags if value)
    positive_outside = any(
        (not inside_flags[index]) and signed[index] > tolerance
        for index in range(len(points))
    )
    negative_outside = any(
        (not inside_flags[index]) and signed[index] < -tolerance
        for index in range(len(points))
    )
    proven = inside_count > 0 and positive_outside and negative_outside
    if proven and complete:
        status = "pass"
        message = "Final source volume crosses the target volume with evidence on both sides."
    elif complete:
        status = "fail"
        message = "Final source volume does not prove a complete pass-through relation."
    else:
        status = "needs_review"
        message = "Pass-through samples are incomplete; the relation cannot be promoted to PASS."
    return _check(
        check_id,
        status,
        message,
        subjects=subjects,
        metrics={
            "sample_count": len(points),
            "complete_sample_evidence": complete,
            "inside_count": inside_count,
            "positive_outside": positive_outside,
            "negative_outside": negative_outside,
            "tolerance": tolerance,
        },
    )


def _attachment_checks(
    world: Mapping[str, Any],
    geometry_by_node: Mapping[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
) -> list[dict[str, Any]]:
    runtime = world.get("runtime")
    build = runtime.get("build") if isinstance(runtime, Mapping) else None
    buildings = build.get("buildings") if isinstance(build, Mapping) else None
    if not isinstance(buildings, list) or not buildings:
        return []

    checks: list[dict[str, Any]] = []
    for building in buildings:
        if not isinstance(building, Mapping):
            continue
        building_id = str(building.get("id") or "building")
        raw_anchors = building.get("anchors")
        anchors = (
            {
                str(item.get("id")): item
                for item in raw_anchors
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)
            }
            if isinstance(raw_anchors, list)
            else {}
        )
        attachments = building.get("attachments")
        if not isinstance(attachments, list):
            continue
        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, Mapping):
                continue
            attachment_id = str(attachment.get("id") or f"attachment-{index}")
            check_id = f"spatial.attachment.{building_id}.{attachment_id}"
            subjects = [building_id, attachment_id]
            mode = str(attachment.get("mode") or "support")
            source = anchors.get(str(attachment.get("from") or ""))
            target = anchors.get(str(attachment.get("to") or ""))
            if not isinstance(source, Mapping) or not isinstance(target, Mapping):
                checks.append(
                    _check(
                        check_id,
                        "not_evaluated",
                        "Attachment anchors are missing from BuildSpec.",
                        subjects=subjects,
                    )
                )
                continue
            point_a = _canonical_point(source.get("position"))
            point_b = _canonical_point(target.get("position"))
            source_part = str(source.get("partId") or source.get("part_id") or "")
            target_part = str(target.get("partId") or target.get("part_id") or "")
            source_nodes = _matching_nodes(source_part, geometry_by_node)
            target_nodes = _matching_nodes(target_part, geometry_by_node)
            tolerance = _nonnegative_number(attachment.get("tolerance"), 0.05)

            if point_a is None or point_b is None or not source_nodes or not target_nodes:
                checks.append(
                    _check(
                        check_id,
                        "not_evaluated",
                        "Final GLB could not be mapped to both BuildSpec attachment parts.",
                        subjects=subjects,
                        metrics={
                            "source_part": source_part,
                            "target_part": target_part,
                            "source_nodes": source_nodes,
                            "target_nodes": target_nodes,
                        },
                    )
                )
                continue

            if mode == "inside":
                checks.append(
                    _inside_attachment_check(
                        check_id=check_id,
                        subjects=subjects,
                        source_nodes=source_nodes,
                        target_nodes=target_nodes,
                        geometry_by_node=geometry_by_node,
                        tolerance=tolerance,
                    )
                )
                continue

            if mode == "passes-through":
                checks.append(
                    _passes_through_attachment_check(
                        check_id=check_id,
                        subjects=subjects,
                        source_nodes=source_nodes,
                        target_nodes=target_nodes,
                        geometry_by_node=geometry_by_node,
                        anchor=point_b,
                        normal=_normal(target.get("normal") or source.get("normal")),
                        tolerance=tolerance,
                    )
                )
                continue

            if mode not in {"support", "flush"}:
                checks.append(
                    _check(
                        check_id,
                        "not_evaluated",
                        f"Attachment mode '{mode}' is not supported by the spatial judge.",
                        subjects=subjects,
                    )
                )
                continue

            source_distance = _point_surface_distance(point_a, source_nodes, geometry_by_node)
            target_distance = _point_surface_distance(point_b, target_nodes, geometry_by_node)
            anchor_gap = float(np.linalg.norm(point_a - point_b))
            measured = max(source_distance, target_distance, anchor_gap)
            if not math.isfinite(measured):
                status = "not_evaluated"
                message = "Could not measure attachment distance on the final GLB surface."
            elif measured > tolerance:
                status = "fail"
                message = "Final GLB violates the BuildSpec attachment tolerance."
            else:
                status = "pass"
                message = "Final GLB surface satisfies the BuildSpec attachment tolerance."
            checks.append(
                _check(
                    check_id,
                    status,
                    message,
                    subjects=subjects,
                    metrics={
                        "source_surface_distance": source_distance,
                        "target_surface_distance": target_distance,
                        "anchor_gap": anchor_gap,
                        "measured": measured,
                        "tolerance": tolerance,
                        "source_nodes": source_nodes,
                        "target_nodes": target_nodes,
                    },
                )
            )
    return checks


def _scene_plan(world: Mapping[str, Any]) -> Mapping[str, Any] | None:
    runtime = world.get("runtime")
    scene = runtime.get("scene") if isinstance(runtime, Mapping) else None
    return scene if isinstance(scene, Mapping) else None


def _scene_plan_checks(world: Mapping[str, Any]) -> list[dict[str, Any]]:
    scene = _scene_plan(world)
    if scene is None:
        return []
    metadata = scene.get("metadata")
    quality = metadata.get("layoutQuality") if isinstance(metadata, Mapping) else None
    quality_status = quality.get("status") if isinstance(quality, Mapping) else None
    if quality_status in {"pass", "valid"}:
        status = "pass"
        message = "Compiled ScenePlan has passing deterministic layout-quality evidence."
    elif quality_status == "invalid":
        status = "fail"
        message = "Compiled ScenePlan layout is invalid."
    else:
        status = "needs_review"
        message = "Compiled ScenePlan does not have passing layout-quality evidence."
    objects = scene.get("objects")
    instances = scene.get("instances")
    relations = scene.get("relations")
    return [
        _check(
            "spatial.scene-plan-layout",
            status,
            message,
            metrics={
                "layout_status": quality_status,
                "object_count": len(objects) if isinstance(objects, list) else 0,
                "instance_count": len(instances) if isinstance(instances, list) else 0,
                "relation_count": len(relations) if isinstance(relations, list) else 0,
            },
        )
    ]


def _scene_index(
    world: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    scene = _scene_plan(world)
    if scene is None:
        return {}, {}
    objects = {
        str(item.get("id")): item
        for item in scene.get("objects", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    instances = {
        str(item.get("objectId")): item
        for item in scene.get("instances", [])
        if isinstance(item, Mapping) and isinstance(item.get("objectId"), str)
    }
    return objects, instances


def _p0_world_object_checks(
    world: Mapping[str, Any],
    target: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(target, Mapping):
        return []
    observations = target.get("observations")
    if not isinstance(observations, list):
        return []
    objects, instances = _scene_index(world)
    if not objects and not instances:
        return []
    checks: list[dict[str, Any]] = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping) or observation.get("priority") != "P0":
            continue
        subject = observation.get("world_object_id") or observation.get("subject_id")
        if not isinstance(subject, str) or not subject:
            continue
        observation_id = str(observation.get("id") or f"p0-{index}")
        status = "pass" if subject in objects and subject in instances else "fail"
        checks.append(
            _check(
                f"spatial.p0.{observation_id}.world-object",
                status,
                "P0 World object has a compiled ScenePlan instance."
                if status == "pass"
                else "P0 World object is missing from the compiled ScenePlan.",
                subjects=[subject],
                metrics={"world_object_id": subject},
            )
        )
    return checks


def _camera_contract(target: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(target, Mapping):
        return None, None
    raw = target.get("camera")
    if raw is None:
        return None, None
    if not isinstance(raw, Mapping):
        return None, "camera contract must be an object"

    position = _canonical_point(raw.get("position"))
    look_at = _canonical_point(raw.get("target") or raw.get("look_at") or raw.get("lookAt"))
    up = _normal(raw.get("up") or [0.0, 1.0, 0.0])
    if position is None or look_at is None or up is None:
        return None, "camera requires finite position, target/look_at, and up vectors"
    forward = look_at - position
    forward_length = float(np.linalg.norm(forward))
    if forward_length <= _EPS:
        return None, "camera position and target must differ"
    forward /= forward_length
    right = np.cross(forward, up)
    right_length = float(np.linalg.norm(right))
    if right_length <= _EPS:
        return None, "camera up vector cannot be parallel to the view direction"
    right /= right_length
    corrected_up = np.cross(right, forward)
    corrected_up /= max(float(np.linalg.norm(corrected_up)), _EPS)

    vfov = _positive_number(
        raw.get("vertical_fov_deg")
        or raw.get("verticalFovDeg")
        or raw.get("fov_deg")
        or raw.get("fov"),
        50.0,
    )
    aspect = _positive_number(raw.get("aspect_ratio") or raw.get("aspectRatio"), 16.0 / 9.0)
    near = _positive_number(raw.get("near"), 0.05)
    far = _positive_number(raw.get("far"), 1000.0)
    if not (1.0 <= vfov < 179.0):
        return None, "camera vertical FOV must be in [1, 179) degrees"
    if far <= near:
        return None, "camera far plane must be greater than near plane"

    camera_id = raw.get("id")
    target_camera_id = target.get("camera_id")
    if isinstance(camera_id, str) and isinstance(target_camera_id, str) and camera_id != target_camera_id:
        return None, "camera contract id does not match target camera_id"
    revision = raw.get("revision")
    target_revision = target.get("camera_revision")
    if revision is not None and target_revision is not None and revision != target_revision:
        return None, "camera contract revision does not match target camera_revision"

    return {
        "position": position,
        "target": look_at,
        "forward": forward,
        "right": right,
        "up": corrected_up,
        "vertical_fov_deg": vfov,
        "aspect_ratio": aspect,
        "near": near,
        "far": far,
        "id": camera_id if isinstance(camera_id, str) else target_camera_id,
        "revision": revision if revision is not None else target_revision,
    }, None


def _rotation_matrix(rotation: Any) -> np.ndarray:
    vector = _canonical_point(rotation)
    if vector is None:
        return np.eye(3)
    x, y, z = vector
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    rx = np.asarray([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=float)
    ry = np.asarray([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=float)
    rz = np.asarray([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=float)
    return rz @ ry @ rx


def _scene_object_corners(
    object_value: Mapping[str, Any],
    instance: Mapping[str, Any],
) -> np.ndarray | None:
    size = _canonical_point(object_value.get("size"))
    position = _canonical_point(instance.get("position"))
    if size is None or position is None or np.any(size <= 0):
        return None
    scale = _positive_number(instance.get("scale"), 1.0)
    dimensions = size * scale
    local = np.asarray(
        [
            [x, y, z]
            for x in (-dimensions[0] / 2.0, dimensions[0] / 2.0)
            for y in (0.0, dimensions[1])
            for z in (-dimensions[2] / 2.0, dimensions[2] / 2.0)
        ],
        dtype=float,
    )
    rotation = _rotation_matrix(instance.get("rotation"))
    return local @ rotation.T + position.reshape(1, 3)


def _frustum_contains_any(points: np.ndarray, camera: Mapping[str, Any]) -> tuple[bool, dict[str, float]]:
    relative = points - np.asarray(camera["position"], dtype=float).reshape(1, 3)
    forward = np.asarray(camera["forward"], dtype=float)
    right = np.asarray(camera["right"], dtype=float)
    up = np.asarray(camera["up"], dtype=float)
    depth = relative @ forward
    horizontal = relative @ right
    vertical = relative @ up
    tan_v = math.tan(math.radians(float(camera["vertical_fov_deg"])) / 2.0)
    tan_h = tan_v * float(camera["aspect_ratio"])
    near = float(camera["near"])
    far = float(camera["far"])
    mask = (
        (depth >= near)
        & (depth <= far)
        & (np.abs(horizontal) <= depth * tan_h)
        & (np.abs(vertical) <= depth * tan_v)
    )
    center = points.mean(axis=0)
    center_relative = center - np.asarray(camera["position"], dtype=float)
    center_depth = float(np.dot(center_relative, forward))
    return bool(np.any(mask)), {
        "corner_count_in_frustum": int(np.count_nonzero(mask)),
        "center_depth": center_depth,
        "near": near,
        "far": far,
        "vertical_fov_deg": float(camera["vertical_fov_deg"]),
        "aspect_ratio": float(camera["aspect_ratio"]),
    }


def _all_world_triangles_with_owner(
    geometry_by_node: Mapping[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
) -> tuple[np.ndarray | None, list[str] | None]:
    chunks: list[np.ndarray] = []
    owners: list[str] = []
    total = 0
    for node_name, entries in geometry_by_node.items():
        for geometry, transform in entries:
            triangles = _world_triangles(geometry, transform)
            total += len(triangles)
            if total > _RAY_TRIANGLE_LIMIT:
                return None, None
            chunks.append(triangles)
            owners.extend([node_name] * len(triangles))
    if not chunks:
        return np.empty((0, 3, 3), dtype=float), []
    return np.concatenate(chunks, axis=0), owners


def _target_visibility_samples(
    node_names: Sequence[str],
    geometry_by_node: Mapping[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
) -> np.ndarray:
    samples: list[np.ndarray] = []
    for node_name in node_names:
        for geometry, transform in geometry_by_node.get(node_name, []):
            world = _world_vertices(geometry, transform)
            if not len(world):
                continue
            bounds = np.asarray([world.min(axis=0), world.max(axis=0)], dtype=float)
            samples.extend(_corners(bounds))
            samples.append(world.mean(axis=0))
    if not samples:
        return np.empty((0, 3), dtype=float)
    values = np.asarray(samples, dtype=float)
    if len(values) > 12:
        values = values[:12]
    return values


def _line_of_sight(
    camera_position: np.ndarray,
    target_nodes: Sequence[str],
    geometry_by_node: Mapping[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
) -> tuple[str, dict[str, Any], str]:
    triangles, owners = _all_world_triangles_with_owner(geometry_by_node)
    if triangles is None or owners is None:
        return (
            "needs_review",
            {"triangle_limit": _RAY_TRIANGLE_LIMIT},
            "Line-of-sight evidence exceeded the bounded triangle budget.",
        )
    samples = _target_visibility_samples(target_nodes, geometry_by_node)
    if not len(samples):
        return "not_evaluated", {}, "No target geometry samples were available for line-of-sight."
    owner_array = np.asarray(owners, dtype=object)
    target_set = set(target_nodes)
    visible_rays = 0
    blocked_rays = 0
    for sample in samples:
        ray = sample - camera_position
        distance = float(np.linalg.norm(ray))
        if distance <= _EPS:
            visible_rays += 1
            continue
        direction = ray / distance
        hit_distances = _ray_hits(camera_position, direction, triangles, max_distance=distance + 1e-5)
        if not len(hit_distances):
            continue
        v0 = triangles[:, 0]
        edge1 = triangles[:, 1] - v0
        edge2 = triangles[:, 2] - v0
        h = np.cross(np.broadcast_to(direction, edge2.shape), edge2)
        a = np.einsum("ij,ij->i", edge1, h)
        valid = np.abs(a) > _EPS
        f = np.zeros_like(a)
        f[valid] = 1.0 / a[valid]
        s = np.broadcast_to(camera_position, v0.shape) - v0
        u = f * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, edge1)
        v = f * np.einsum("ij,j->i", q, direction)
        t = f * np.einsum("ij,ij->i", edge2, q)
        mask = (
            valid
            & (u >= -_EPS)
            & (u <= 1.0 + _EPS)
            & (v >= -_EPS)
            & ((u + v) <= 1.0 + _EPS)
            & (t > _EPS)
            & (t <= distance + 1e-5)
        )
        hit_indices = np.flatnonzero(mask)
        if not len(hit_indices):
            continue
        nearest = hit_indices[int(np.argmin(t[hit_indices]))]
        if str(owner_array[nearest]) in target_set:
            visible_rays += 1
        else:
            blocked_rays += 1
    metrics = {
        "sample_count": len(samples),
        "visible_rays": visible_rays,
        "blocked_rays": blocked_rays,
        "triangle_count": int(len(triangles)),
        "target_nodes": list(target_nodes),
    }
    if visible_rays:
        return "pass", metrics, "At least one deterministic ray reaches the P0 target geometry first."
    if blocked_rays:
        return (
            "needs_review",
            metrics,
            "Sampled P0 sight lines are occluded; broader visibility evidence is required before PASS.",
        )
    return "not_evaluated", metrics, "Line-of-sight rays did not yield decisive target intersections."


def _camera_visibility_checks(
    world: Mapping[str, Any],
    target: Mapping[str, Any] | None,
    geometry_by_node: Mapping[str, list[tuple[trimesh.Trimesh, np.ndarray]]],
) -> list[dict[str, Any]]:
    if not isinstance(target, Mapping):
        return []
    camera, camera_error = _camera_contract(target)
    require_visibility = bool(target.get("require_visibility") or target.get("requireVisibility"))
    raw_camera = target.get("camera")
    if raw_camera is None and not require_visibility:
        return []
    if camera is None:
        return [
            _check(
                "spatial.camera-contract",
                "fail" if raw_camera is not None else "not_evaluated",
                camera_error or "Required camera contract is missing.",
                metrics={"require_visibility": require_visibility},
            )
        ]

    checks = [
        _check(
            "spatial.camera-contract",
            "pass",
            "Visual target provides a valid deterministic camera contract.",
            metrics={
                "camera_id": camera.get("id"),
                "camera_revision": camera.get("revision"),
                "vertical_fov_deg": camera["vertical_fov_deg"],
                "aspect_ratio": camera["aspect_ratio"],
                "near": camera["near"],
                "far": camera["far"],
            },
        )
    ]
    observations = target.get("observations")
    if not isinstance(observations, list):
        return checks
    objects, instances = _scene_index(world)
    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping) or observation.get("priority") != "P0":
            continue
        subject = observation.get("world_object_id") or observation.get("subject_id")
        if not isinstance(subject, str) or subject not in objects or subject not in instances:
            continue
        observation_id = str(observation.get("id") or f"p0-{index}")
        corners = _scene_object_corners(objects[subject], instances[subject])
        if corners is None:
            checks.append(
                _check(
                    f"spatial.p0.{observation_id}.frustum",
                    "not_evaluated",
                    "P0 ScenePlan object has no usable size/transform for frustum testing.",
                    subjects=[subject],
                )
            )
            continue
        in_frustum, metrics = _frustum_contains_any(corners, camera)
        checks.append(
            _check(
                f"spatial.p0.{observation_id}.frustum",
                "pass" if in_frustum else "fail",
                "P0 ScenePlan bounds intersect the target camera frustum."
                if in_frustum
                else "P0 ScenePlan bounds are outside the target camera frustum.",
                subjects=[subject],
                metrics=metrics,
            )
        )
        if not in_frustum:
            continue
        target_nodes = _matching_world_object_nodes(objects[subject], geometry_by_node)
        if not target_nodes:
            checks.append(
                _check(
                    f"spatial.p0.{observation_id}.line-of-sight",
                    "not_evaluated",
                    "Final GLB could not be mapped to the P0 World object for line-of-sight.",
                    subjects=[subject],
                )
            )
            continue
        los_status, los_metrics, los_message = _line_of_sight(
            np.asarray(camera["position"], dtype=float),
            target_nodes,
            geometry_by_node,
        )
        checks.append(
            _check(
                f"spatial.p0.{observation_id}.line-of-sight",
                los_status,
                los_message,
                subjects=[subject],
                metrics=los_metrics,
            )
        )
    return checks


def build_world_spatial_bundle(
    world_id: str,
    world: Mapping[str, Any],
    run: Mapping[str, Any] | None,
    *,
    target: Mapping[str, Any] | None = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Build authoritative spatial checks from World facts and the final run GLB."""

    root = (workspace_root or runtime_paths.workspace).expanduser().resolve()
    checks: list[dict[str, Any]] = []
    workspace_path = _run_mesh_workspace_path(run)
    evidence: list[dict[str, Any]] = []
    snapshot: dict[str, Any] | None = None
    geometry_by_node: dict[str, list[tuple[trimesh.Trimesh, np.ndarray]]] = {}

    if not workspace_path:
        checks.append(
            _check(
                "spatial.final-mesh",
                "not_evaluated",
                "A completed WorkflowRun mesh artifact is required for authoritative spatial validation.",
            )
        )
    else:
        try:
            mesh_path = resolve_workspace_path(root, workspace_path)
            if not mesh_path.is_file():
                raise FileNotFoundError(mesh_path)
            snapshot, geometry_by_node = _scene_snapshot(mesh_path)
            evidence.append(
                {
                    "id": "evidence:spatial-mesh",
                    "kind": "geometry_snapshot_source",
                    "workspace_path": workspace_path,
                }
            )
            geometry_count = int(snapshot.get("geometry_count") or 0)
            checks.append(
                _check(
                    "spatial.final-mesh",
                    "pass" if geometry_count > 0 else "fail",
                    "Final WorkflowRun mesh is readable and contains geometry."
                    if geometry_count > 0
                    else "Final WorkflowRun mesh contains no geometry.",
                    metrics={
                        "geometry_count": geometry_count,
                        "workspace_path": workspace_path,
                    },
                )
            )
        except Exception as exc:
            checks.append(
                _check(
                    "spatial.final-mesh",
                    "fail",
                    f"Final WorkflowRun mesh could not be inspected: {exc}",
                    metrics={"workspace_path": workspace_path},
                )
            )

    checks.extend(_scene_plan_checks(world))
    checks.extend(_p0_world_object_checks(world, target))
    if geometry_by_node:
        checks.extend(_attachment_checks(world, geometry_by_node))
        checks.extend(_camera_visibility_checks(world, target, geometry_by_node))
    elif isinstance(target, Mapping) and (
        target.get("camera") is not None
        or bool(target.get("require_visibility") or target.get("requireVisibility"))
    ):
        camera, camera_error = _camera_contract(target)
        checks.append(
            _check(
                "spatial.camera-contract",
                "pass" if camera is not None else "fail",
                "Visual target provides a valid deterministic camera contract."
                if camera is not None
                else camera_error or "Required camera contract is missing.",
            )
        )
        checks.append(
            _check(
                "spatial.camera-visibility",
                "not_evaluated",
                "Camera visibility requires readable final GLB geometry.",
            )
        )

    if not evidence:
        evidence.append(
            {
                "id": "evidence:spatial-mesh",
                "kind": "geometry_snapshot_source",
                "workspace_path": workspace_path or "",
            }
        )

    return {
        "schema_version": 1,
        "kind": "polykit.spatial-validation-bundle",
        "world_id": world_id,
        "run_id": run.get("run_id") if isinstance(run, Mapping) else None,
        "status": _status(checks),
        "checks": checks,
        "evidence": evidence,
        "snapshot": snapshot,
    }


__all__ = ["SPATIAL_SNAPSHOT_KIND", "build_world_spatial_bundle"]
