"""Deterministic spatial judge for World visual validation.

The judge cross-checks server-owned World facts against the final mesh artifact
published by a WorkflowRun. It reads the delivered GLB with trimesh instead of
parsing Blender files or trusting caller-authored spatial scores.
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


def _scene_snapshot(path: Path) -> tuple[dict[str, Any], dict[str, list[tuple[trimesh.Trimesh, np.ndarray]]]]:
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
        world_corners = trimesh.transform_points(_corners(np.asarray(geometry.bounds, dtype=float)), matrix)
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
            }
        )

    bounds = np.asarray(scene.bounds, dtype=float) if scene.bounds is not None else np.zeros((2, 3), dtype=float)
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
    matches = []
    for node_name in geometry_by_node:
        candidate = _tokens(node_name)
        if wanted.issubset(candidate):
            matches.append(node_name)
    return matches


def _canonical_point(value: Any) -> np.ndarray | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        return None
    point = np.asarray([float(item) for item in value], dtype=float)
    return point if np.isfinite(point).all() else None


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
                _closest, distance, _triangle = trimesh.proximity.closest_point_naive(geometry, local_point)
            except (ValueError, TypeError, np.linalg.LinAlgError):
                continue
            if len(distance):
                best = min(best, float(distance[0]))
    return best


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
            mode = str(attachment.get("mode") or "support")
            source = anchors.get(str(attachment.get("from") or ""))
            target = anchors.get(str(attachment.get("to") or ""))
            if mode not in {"support", "flush"}:
                checks.append(
                    _check(
                        f"spatial.attachment.{building_id}.{attachment_id}",
                        "not_evaluated",
                        f"Attachment mode '{mode}' requires richer volume evidence than a final surface GLB provides.",
                        subjects=[building_id, attachment_id],
                    )
                )
                continue
            if not isinstance(source, Mapping) or not isinstance(target, Mapping):
                checks.append(
                    _check(
                        f"spatial.attachment.{building_id}.{attachment_id}",
                        "not_evaluated",
                        "Attachment anchors are missing from BuildSpec.",
                        subjects=[building_id, attachment_id],
                    )
                )
                continue
            point_a = _canonical_point(source.get("position"))
            point_b = _canonical_point(target.get("position"))
            source_part = str(source.get("partId") or source.get("part_id") or "")
            target_part = str(target.get("partId") or target.get("part_id") or "")
            source_nodes = _matching_nodes(source_part, geometry_by_node)
            target_nodes = _matching_nodes(target_part, geometry_by_node)
            if point_a is None or point_b is None or not source_nodes or not target_nodes:
                checks.append(
                    _check(
                        f"spatial.attachment.{building_id}.{attachment_id}",
                        "not_evaluated",
                        "Final GLB could not be mapped to both BuildSpec attachment parts.",
                        subjects=[building_id, attachment_id],
                        metrics={"source_part": source_part, "target_part": target_part},
                    )
                )
                continue
            tolerance_value = attachment.get("tolerance", 0.05)
            try:
                tolerance = max(0.0, float(tolerance_value))
            except (TypeError, ValueError):
                tolerance = 0.05
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
                    f"spatial.attachment.{building_id}.{attachment_id}",
                    status,
                    message,
                    subjects=[building_id, attachment_id],
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


def _scene_plan_checks(world: Mapping[str, Any]) -> list[dict[str, Any]]:
    runtime = world.get("runtime")
    scene = runtime.get("scene") if isinstance(runtime, Mapping) else None
    if not isinstance(scene, Mapping):
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


def _p0_world_object_checks(world: Mapping[str, Any], target: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(target, Mapping):
        return []
    observations = target.get("observations")
    if not isinstance(observations, list):
        return []
    runtime = world.get("runtime")
    scene = runtime.get("scene") if isinstance(runtime, Mapping) else None
    if not isinstance(scene, Mapping):
        return []
    object_ids = {
        str(item.get("id"))
        for item in scene.get("objects", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    instance_ids = {
        str(item.get("objectId"))
        for item in scene.get("instances", [])
        if isinstance(item, Mapping) and isinstance(item.get("objectId"), str)
    }
    checks: list[dict[str, Any]] = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping) or observation.get("priority") != "P0":
            continue
        subject = observation.get("world_object_id") or observation.get("subject_id")
        if not isinstance(subject, str) or not subject:
            continue
        observation_id = str(observation.get("id") or f"p0-{index}")
        status = "pass" if subject in object_ids and subject in instance_ids else "fail"
        checks.append(
            _check(
                f"spatial.p0.{observation_id}.world-object",
                status,
                "P0 World object has a compiled ScenePlan instance." if status == "pass" else "P0 World object is missing from the compiled ScenePlan.",
                subjects=[subject],
                metrics={"world_object_id": subject},
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
                    "Final WorkflowRun mesh is readable and contains geometry." if geometry_count > 0 else "Final WorkflowRun mesh contains no geometry.",
                    metrics={"geometry_count": geometry_count, "workspace_path": workspace_path},
                )
            )
        except (OSError, ValueError, TypeError, np.linalg.LinAlgError) as exc:
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
