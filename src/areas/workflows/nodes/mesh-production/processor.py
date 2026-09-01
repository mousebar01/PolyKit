"""Game-ready mesh derivative process nodes.

The processor follows PolyKit's line-delimited process protocol. Collision
meshes and LOD files are generated in the run-private process workspace; the
normal workflow engine publishes the primary mesh and any JSON/GLB sidecars at
the selected sink.
"""
from __future__ import annotations

import base64
import json
import math
import re
import struct
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": percent, "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _slug(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+", "_", value)
    return result.strip("_").lower()[:48] or "mesh"


def _round(value: Any) -> float:
    number = float(value)
    return round(number, 6) if math.isfinite(number) else 0.0


def _vector(values: Any) -> list[float]:
    return [_round(value) for value in values]


def _load_scene(input_path: Path) -> Any:
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError(f"trimesh is required for mesh production: {exc}") from exc

    loaded = trimesh.load(input_path, force="scene", process=False)
    if isinstance(loaded, trimesh.Scene):
        return loaded
    if isinstance(loaded, trimesh.Trimesh):
        return trimesh.Scene(loaded)
    raise RuntimeError(f"unsupported mesh payload: {type(loaded).__name__}")


def _components(scene: Any) -> list[tuple[Any, Any, str]]:
    components: list[tuple[Any, Any, str]] = []
    for node_name in scene.graph.nodes_geometry:
        try:
            transform, geometry_name = scene.graph.get(node_name)
            geometry = scene.geometry[geometry_name]
            if not hasattr(geometry, "vertices") or not hasattr(geometry, "faces"):
                continue
            components.append((geometry, transform, str(node_name)))
        except (KeyError, TypeError, ValueError):
            continue
    if not components:
        raise ValueError("mesh contains no renderable components")
    return components


def _world_mesh(scene: Any) -> Any:
    import numpy as np
    import trimesh

    meshes = []
    for geometry, transform, _name in _components(scene):
        vertices = np.asarray(geometry.vertices, dtype=float)
        matrix = np.asarray(transform, dtype=float)
        world_vertices = (vertices @ matrix[:3, :3].T) + matrix[:3, 3]
        meshes.append(trimesh.Trimesh(vertices=world_vertices, faces=np.asarray(geometry.faces, dtype=int), process=False))
    return trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]


def _token(input_path: Path, suffix: str) -> str:
    return f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}_{suffix}"


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _collision_mesh(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    scene = _load_scene(input_path)
    source = _world_mesh(scene)
    requested = str(params.get("method") or "convex_hull").strip().lower()
    if requested not in {"convex_hull", "box"}:
        requested = "convex_hull"
    method = requested
    warnings: list[str] = []
    if requested == "box":
        collider = source.bounding_box
    else:
        try:
            collider = source.convex_hull
        except Exception as exc:
            collider = source.bounding_box
            method = "box-fallback"
            warnings.append(f"convex hull failed ({type(exc).__name__}); used the bounding box fallback")
    if len(getattr(collider, "faces", [])) == 0:
        raise ValueError("collision proxy contains no faces")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = _token(input_path, "collision")
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    collider.export(output_path)
    source_bounds = source.bounds
    report = {
        "schemaVersion": 1,
        "kind": "polykit.collision-mesh",
        "status": "pass" if not warnings else "needs_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown"},
        "method": {"requested": requested, "used": method},
        "source": {
            "componentCount": len(_components(scene)),
            "vertices": int(len(source.vertices)),
            "faces": int(len(source.faces)),
            "bounds": [_vector(source_bounds[0]), _vector(source_bounds[1])],
        },
        "collision": {
            "vertices": int(len(collider.vertices)),
            "faces": int(len(collider.faces)),
            "bounds": [_vector(collider.bounds[0]), _vector(collider.bounds[1])],
            "volume": _round(abs(float(getattr(collider, "volume", 0.0)))),
        },
        "warnings": warnings,
        "reviewNotes": [
            "Collision geometry is an interaction proxy, not a render mesh; inspect clearance around thin or concave parts.",
            "The proxy uses world-space bounds and therefore preserves the source asset coordinate frame.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "collision-mesh",
            "schema_version": 1,
            "status": report["status"],
            "method": method,
            "source_faces": int(len(source.faces)),
            "collision_faces": int(len(collider.faces)),
            "report": report_path.name,
        },
    }


def _ratio(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _decimate(mesh: Any, target_faces: int, work_dir: Path) -> Any:
    import pymeshlab
    import trimesh

    if len(mesh.faces) <= target_faces:
        return mesh.copy()
    source_path = work_dir / f"source-{uuid.uuid4().hex}.ply"
    output_path = work_dir / f"output-{uuid.uuid4().hex}.ply"
    mesh.export(source_path)
    mesh_set = pymeshlab.MeshSet()
    mesh_set.load_new_mesh(str(source_path))
    mesh_set.meshing_decimation_quadric_edge_collapse(
        targetfacenum=int(max(4, target_faces)),
        preservenormal=True,
        preserveboundary=True,
    )
    mesh_set.save_current_mesh(str(output_path))
    result = trimesh.load(output_path, force="mesh", process=False)
    if not isinstance(result, trimesh.Trimesh):
        raise RuntimeError("pymeshlab did not return a triangle mesh")
    return result


def _lod_generate(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    import trimesh

    scene = _load_scene(input_path)
    components = _components(scene)
    ratios = {
        "LOD1": _ratio(params.get("lod1_ratio"), 0.5, 0.05, 1.0),
        "LOD2": _ratio(params.get("lod2_ratio"), 0.2, 0.02, 1.0),
    }
    try:
        min_faces = int(params.get("min_faces", 32) or 32)
    except (TypeError, ValueError):
        min_faces = 32
    min_faces = max(4, min(100000, min_faces))
    source_faces = sum(int(len(geometry.faces)) for geometry, _transform, _name in components)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = _token(input_path, "lod")
    output_path = workspace_dir / f"{token}_LOD0.glb"
    report_path = workspace_dir / f"{token}.json"
    lod_files: list[dict[str, Any]] = []
    temp_dir = Path(tempfile.mkdtemp(prefix="polykit-lod-", dir=str(workspace_dir)))
    try:
        base_scene = _load_scene(input_path)
        base_scene.export(output_path)
        lod_files.append({"level": "LOD0", "path": output_path, "faces": source_faces, "ratio": 1.0})
        for level, ratio in ratios.items():
            lod_scene = trimesh.Scene()
            level_faces = 0
            for geometry, transform, name in components:
                target = max(min_faces, int(round(len(geometry.faces) * ratio)))
                reduced = _decimate(geometry, target, temp_dir)
                lod_scene.add_geometry(reduced, geom_name=name, node_name=name, transform=transform)
                level_faces += int(len(reduced.faces))
            lod_path = workspace_dir / f"{token}_{level}.glb"
            lod_scene.export(lod_path)
            lod_files.append({"level": level, "path": lod_path, "faces": level_faces, "ratio": ratio})
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    report = {
        "schemaVersion": 1,
        "kind": "polykit.lod-generation",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown"},
        "levels": [
            {"level": item["level"], "file": Path(item["path"]).name, "faces": item["faces"], "ratio": item["ratio"]}
            for item in lod_files
        ],
        "settings": {"lod1Ratio": ratios["LOD1"], "lod2Ratio": ratios["LOD2"], "minFacesPerComponent": min_faces},
        "reviewNotes": [
            "LOD generation is triangle reduction for runtime distance tiers; validate silhouette, UVs, and material seams at each distance.",
            "LOD0 is the primary mesh output; LOD1 and LOD2 are published as GLB sidecars beside it.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)] + [str(item["path"]) for item in lod_files[1:]],
        "metadata": {
            "evidence_kind": "lod-generation",
            "schema_version": 1,
            "status": "pass",
            "level_count": len(lod_files),
            "source_faces": source_faces,
            "report": report_path.name,
        },
    }


def _projection_bake(image_path: Path, mesh_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Project an image into per-vertex UVs and embed it in an exported GLB."""
    import numpy as np
    import trimesh

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for projection bake: {exc}") from exc

    modes = {"perspective-camera-projection", "orthographic-front-projection", "triplanar-fallback"}
    strategies = {"mirror-symmetry", "palette-continue", "request-additional-view", "leave-unprojected"}
    mode = str(params.get("projection_mode") or "perspective-camera-projection")
    if mode not in modes:
        mode = "perspective-camera-projection"
    strategy = str(params.get("unseen_strategy") or "leave-unprojected")
    if strategy not in strategies:
        strategy = "leave-unprojected"
    fov = _ratio(params.get("fov_degrees"), 35.0, 10.0, 120.0)
    distance = _ratio(params.get("distance"), 2.5, 0.01, 10000.0)
    yaw = _ratio(params.get("yaw"), 0.0, -180.0, 180.0)
    pitch = _ratio(params.get("pitch"), 0.0, -89.0, 89.0)
    roll = _ratio(params.get("roll"), 0.0, -180.0, 180.0)
    try:
        requested_texture_size = int(params.get("texture_size", 2048) or 2048)
    except (TypeError, ValueError):
        requested_texture_size = 2048
    texture_size = max(64, min(8192, requested_texture_size))

    scene = _load_scene(mesh_path)
    components = _components(scene)
    scene_min, scene_max = np.asarray(scene.bounds[0], dtype=float), np.asarray(scene.bounds[1], dtype=float)
    center = (scene_min + scene_max) / 2.0
    world_vertices: list[np.ndarray] = []
    faces: list[np.ndarray] = []
    offset = 0
    for geometry, transform, _name in components:
        vertices = np.asarray(geometry.vertices, dtype=float)
        matrix = np.asarray(transform, dtype=float)
        world = (vertices @ matrix[:3, :3].T) + matrix[:3, 3]
        world_vertices.append(world)
        faces.append(np.asarray(geometry.faces, dtype=int) + offset)
        offset += len(world)
    vertices_array = np.vstack(world_vertices)
    faces_array = np.vstack(faces)

    yaw_radians = math.radians(yaw)
    pitch_radians = math.radians(pitch)
    roll_radians = math.radians(roll)
    forward = np.array([
        math.cos(pitch_radians) * math.cos(yaw_radians),
        math.cos(pitch_radians) * math.sin(yaw_radians),
        math.sin(pitch_radians),
    ])
    right = np.array([-math.sin(yaw_radians), math.cos(yaw_radians), 0.0])
    up = np.array([
        -math.sin(pitch_radians) * math.cos(yaw_radians),
        -math.sin(pitch_radians) * math.sin(yaw_radians),
        math.cos(pitch_radians),
    ])
    cos_roll, sin_roll = math.cos(roll_radians), math.sin(roll_radians)
    rolled_right = right * cos_roll + up * sin_roll
    rolled_up = -right * sin_roll + up * cos_roll
    relative = vertices_array - center
    projected_x = relative @ rolled_right
    projected_y = relative @ rolled_up
    aspect = 1.0
    with Image.open(image_path) as source:
        source_image = source.convert("RGBA")
        aspect = source_image.width / max(1, source_image.height)
        if max(source_image.size) > texture_size:
            source_image.thumbnail((texture_size, texture_size), Image.Resampling.LANCZOS)
        if mode == "perspective-camera-projection":
            depth = distance - (relative @ forward)
            visible = depth > 1e-6
            safe_depth = np.maximum(depth, 1e-6)
            tangent = math.tan(math.radians(fov) / 2.0)
            u = 0.5 + projected_x / (2.0 * safe_depth * tangent * max(aspect, 1e-6))
            v = 0.5 - projected_y / (2.0 * safe_depth * tangent)
        else:
            visible = np.ones(len(vertices_array), dtype=bool)
            x_range = max(float(projected_x.max() - projected_x.min()), 1e-9)
            y_range = max(float(projected_y.max() - projected_y.min()), 1e-9)
            u = (projected_x - projected_x.min()) / x_range
            v = 1.0 - (projected_y - projected_y.min()) / y_range
        raw_uv = np.column_stack((u, v))
        out_of_view = (~visible) | (raw_uv[:, 0] < 0.0) | (raw_uv[:, 0] > 1.0) | (raw_uv[:, 1] < 0.0) | (raw_uv[:, 1] > 1.0)
        uv = np.clip(raw_uv, 0.0, 1.0).astype(np.float32)
        textured = trimesh.Trimesh(vertices=vertices_array, faces=faces_array, process=False)
        textured.visual = trimesh.visual.texture.TextureVisuals(uv=uv, image=source_image.copy())

        workspace_dir.mkdir(parents=True, exist_ok=True)
        token = _token(mesh_path, "projection")
        output_path = workspace_dir / f"{token}.glb"
        report_path = workspace_dir / f"{token}.json"
        textured.export(output_path)

    unseen_count = int(np.count_nonzero(out_of_view))
    report = {
        "schemaVersion": 1,
        "kind": "polykit.projection-bake",
        "status": "needs_visual_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceImage": {"name": image_path.name, "width": source_image.width, "height": source_image.height},
        "sourceMesh": {"name": mesh_path.name, "componentCount": len(components), "vertices": int(len(vertices_array)), "faces": int(len(faces_array))},
        "camera": {
            "projectionMode": mode,
            "fovDegrees": _round(fov),
            "distance": _round(distance),
            "yawDegrees": _round(yaw),
            "pitchDegrees": _round(pitch),
            "rollDegrees": _round(roll),
            "target": _vector(center),
        },
        "texture": {"embedded": True, "requestedMaxSize": texture_size, "actualSize": [source_image.width, source_image.height], "uvMode": "per-vertex-camera-projection"},
        "coverage": {"unseenVertexCount": unseen_count, "unseenVertexRatio": _round(unseen_count / max(1, len(vertices_array))), "clampedUvVertexCount": int(np.count_nonzero(np.any(np.abs(raw_uv - uv) > 1e-6, axis=1)))},
        "unseenRegionStrategy": {"mode": strategy, "applied": False, "note": "The selected strategy is recorded for downstream inference; this single-image bake flags and clamps unseen UVs instead of inventing pixels."},
        "reviewNotes": [
            "The GLB contains the source image as an embedded texture with camera-projected UVs.",
            "Review a render from the reference camera; back, underside, and occluded regions remain uncertain.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "projection-bake",
            "schema_version": 1,
            "status": report["status"],
            "unseen_vertex_ratio": report["coverage"]["unseenVertexRatio"],
            "report": report_path.name,
        },
    }


def _uv_unwrap(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Generate wedge UVs with a deterministic, dependency-local parametrizer.

    Pymeshlab owns the chart projection; the exported mesh duplicates vertices
    at UV seams so GLB consumers receive ordinary per-vertex UV coordinates.
    This is intentionally a reviewable unwrap derivative, not a promise that
    one camera-independent chart layout is ideal for every asset.
    """
    import numpy as np
    import pymeshlab
    import trimesh

    scene = _load_scene(input_path)
    source = _world_mesh(scene)
    method = str(params.get("method") or "flat-plane").strip().lower()
    if method not in {"flat-plane", "triangle-trivial"}:
        method = "flat-plane"

    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = _token(input_path, "uv")
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    temp_dir = Path(tempfile.mkdtemp(prefix="polykit-uv-", dir=str(workspace_dir)))
    try:
        source_path = temp_dir / "source.ply"
        source.export(source_path)
        mesh_set = pymeshlab.MeshSet()
        mesh_set.load_new_mesh(str(source_path))
        if method == "triangle-trivial":
            mesh_set.compute_texcoord_parametrization_triangle_trivial_per_wedge()
        else:
            mesh_set.compute_texcoord_parametrization_flat_plane_per_wedge()
        uv_mesh = mesh_set.current_mesh()
        if not uv_mesh.has_wedge_tex_coord():
            raise RuntimeError("UV parametrization produced no wedge coordinates")
        vertices = np.asarray(uv_mesh.vertex_matrix(), dtype=float)
        faces = np.asarray(uv_mesh.face_matrix(), dtype=np.int64)
        wedge_uv = np.asarray(uv_mesh.wedge_tex_coord_matrix(), dtype=np.float32)
        if wedge_uv.shape != (len(faces) * 3, 2):
            raise RuntimeError("UV parametrization returned an invalid wedge coordinate array")
        corner_indices = faces.reshape(-1)
        unwrapped = trimesh.Trimesh(
            vertices=vertices[corner_indices],
            faces=np.arange(len(corner_indices), dtype=np.int64).reshape(-1, 3),
            process=False,
        )
        unwrapped.visual = trimesh.visual.texture.TextureVisuals(uv=wedge_uv)
        unwrapped.export(output_path)
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    uv_min = wedge_uv.min(axis=0)
    uv_max = wedge_uv.max(axis=0)
    report = {
        "schemaVersion": 1,
        "kind": "polykit.uv-unwrap",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {
            "name": input_path.name,
            "componentCount": len(_components(scene)),
            "vertices": int(len(source.vertices)),
            "faces": int(len(source.faces)),
        },
        "uv": {
            "method": method,
            "hasWedgeCoordinates": True,
            "sourceVertexCount": int(len(source.vertices)),
            "unwrappedVertexCount": int(len(unwrapped.vertices)),
            "faceCount": int(len(unwrapped.faces)),
            "uvBounds": [_vector(uv_min), _vector(uv_max)],
            "seamVertexExpansion": int(len(unwrapped.vertices) - len(source.vertices)),
        },
        "reviewNotes": [
            "Vertices are duplicated at UV wedges so the GLB carries explicit seam-safe coordinates.",
            "Inspect island scale, padding, orientation, and distortion before using the unwrap for baking.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "uv-unwrap",
            "schema_version": 1,
            "status": report["status"],
            "method": method,
            "uv_vertex_count": int(len(unwrapped.vertices)),
            "report": report_path.name,
        },
    }


def _rasterize_uv_values(
    uv: Any,
    faces: Any,
    values: Any,
    resolution: int,
    channels: int,
    fill: float,
) -> tuple[Any, Any]:
    """Rasterize per-vertex values into a small UV image with barycentric interpolation."""
    import numpy as np

    uv_array = np.asarray(uv, dtype=np.float32)
    face_array = np.asarray(faces, dtype=np.int64)
    value_array = np.asarray(values, dtype=np.float32)
    if value_array.ndim == 1:
        value_array = value_array[:, None]
    image = np.full((resolution, resolution, channels), fill, dtype=np.float32)
    covered = np.zeros((resolution, resolution), dtype=bool)
    for face in face_array:
        tri_uv = uv_array[face]
        px = np.clip(tri_uv[:, 0] * (resolution - 1), 0, resolution - 1)
        py = np.clip((1.0 - tri_uv[:, 1]) * (resolution - 1), 0, resolution - 1)
        min_x, max_x = int(np.floor(px.min())), int(np.ceil(px.max()))
        min_y, max_y = int(np.floor(py.min())), int(np.ceil(py.max()))
        if max_x < min_x or max_y < min_y:
            continue
        grid_x, grid_y = np.meshgrid(
            np.arange(min_x, max_x + 1, dtype=np.float32),
            np.arange(min_y, max_y + 1, dtype=np.float32),
        )
        ax, ay = px[0], py[0]
        bx, by = px[1], py[1]
        cx, cy = px[2], py[2]
        denominator = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(float(denominator)) <= 1e-8:
            continue
        weight_a = ((by - cy) * (grid_x - cx) + (cx - bx) * (grid_y - cy)) / denominator
        weight_b = ((cy - ay) * (grid_x - cx) + (ax - cx) * (grid_y - cy)) / denominator
        weight_c = 1.0 - weight_a - weight_b
        inside = (weight_a >= -1e-5) & (weight_b >= -1e-5) & (weight_c >= -1e-5)
        if not np.any(inside):
            continue
        sampled = (
            weight_a[..., None] * value_array[face[0]]
            + weight_b[..., None] * value_array[face[1]]
            + weight_c[..., None] * value_array[face[2]]
        )
        target = image[min_y : max_y + 1, min_x : max_x + 1]
        target[inside] = sampled[inside, :channels]
        covered[min_y : max_y + 1, min_x : max_x + 1][inside] = True
    return np.clip(image, 0.0, 1.0), covered


def _surface_map_bake(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Bake world-space normal and vertex-derived AO maps beside a UV mesh."""
    import numpy as np
    import pymeshlab
    import trimesh

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for surface map bake: {exc}") from exc

    method = str(params.get("uv_method") or "flat-plane").strip().lower()
    if method not in {"flat-plane", "triangle-trivial"}:
        method = "flat-plane"
    try:
        requested_resolution = int(params.get("resolution", 512) or 512)
    except (TypeError, ValueError):
        requested_resolution = 512
    resolution = max(64, min(2048, requested_resolution))

    scene = _load_scene(input_path)
    source = _world_mesh(scene)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = _token(input_path, "maps")
    output_path = workspace_dir / f"{token}.glb"
    normal_path = workspace_dir / f"{token}_normal.png"
    ao_path = workspace_dir / f"{token}_ao.png"
    report_path = workspace_dir / f"{token}.json"
    temp_dir = Path(tempfile.mkdtemp(prefix="polykit-maps-", dir=str(workspace_dir)))
    try:
        source_path = temp_dir / "source.ply"
        source.export(source_path)
        mesh_set = pymeshlab.MeshSet()
        mesh_set.load_new_mesh(str(source_path))
        if method == "triangle-trivial":
            mesh_set.compute_texcoord_parametrization_triangle_trivial_per_wedge()
        else:
            mesh_set.compute_texcoord_parametrization_flat_plane_per_wedge()
        uv_mesh = mesh_set.current_mesh()
        if not uv_mesh.has_wedge_tex_coord():
            raise RuntimeError("surface map bake produced no UV coordinates")
        vertices = np.asarray(uv_mesh.vertex_matrix(), dtype=float)
        source_faces = np.asarray(uv_mesh.face_matrix(), dtype=np.int64)
        wedge_uv = np.asarray(uv_mesh.wedge_tex_coord_matrix(), dtype=np.float32)
        if wedge_uv.shape != (len(source_faces) * 3, 2):
            raise RuntimeError("surface map bake returned invalid wedge UV coordinates")
        corner_indices = source_faces.reshape(-1)
        faces = np.arange(len(corner_indices), dtype=np.int64).reshape(-1, 3)
        unwrapped = trimesh.Trimesh(vertices=vertices[corner_indices], faces=faces, process=False)
        unwrapped.visual = trimesh.visual.texture.TextureVisuals(uv=wedge_uv)
        unwrapped.export(output_path)

        normals = np.asarray(unwrapped.vertex_normals, dtype=np.float32)
        normal_values = np.clip((normals + 1.0) * 0.5, 0.0, 1.0)
        normal_image, normal_coverage = _rasterize_uv_values(
            wedge_uv, faces, normal_values, resolution, 3, 0.5
        )
        Image.fromarray(np.uint8(np.round(normal_image * 255.0)), mode="RGB").save(normal_path)

        ao_method = "pymeshlab-vertex-ambient-occlusion"
        try:
            mesh_set.compute_scalar_ambient_occlusion()
            ao_values = np.asarray(mesh_set.current_mesh().vertex_scalar_array(), dtype=float)
            if len(ao_values) != len(vertices) or not np.isfinite(ao_values).all():
                raise ValueError("invalid ambient occlusion scalar field")
            low, high = float(ao_values.min()), float(ao_values.max())
            if high - low <= 1e-9:
                ao_values = np.ones(len(ao_values), dtype=np.float32)
            else:
                ao_values = ((ao_values - low) / (high - low)).astype(np.float32)
            ao_values = ao_values[corner_indices]
        except Exception:
            ao_method = "normal-upward-fallback"
            ao_values = np.clip((normals[:, 2] + 1.0) * 0.5, 0.0, 1.0)
        ao_image, ao_coverage = _rasterize_uv_values(
            wedge_uv, faces, ao_values, resolution, 1, 1.0
        )
        Image.fromarray(np.uint8(np.round(ao_image[:, :, 0] * 255.0)), mode="L").save(ao_path)
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    report = {
        "schemaVersion": 1,
        "kind": "polykit.surface-map-bake",
        "status": "needs_visual_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {
            "name": input_path.name,
            "componentCount": len(_components(scene)),
            "vertices": int(len(source.vertices)),
            "faces": int(len(source.faces)),
        },
        "maps": {
            "resolution": [resolution, resolution],
            "normal": {"file": normal_path.name, "space": "world", "coverage": _round(float(normal_coverage.mean()))},
            "ambientOcclusion": {"file": ao_path.name, "method": ao_method, "coverage": _round(float(ao_coverage.mean()))},
            "uvMethod": method,
        },
        "reviewNotes": [
            "Normal output is world-space RGB, not a tangent-space high-to-low bake.",
            "AO is derived from the source mesh vertex field; inspect seams, padding, and map fidelity before production use.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(output_path),
        "sidecars": [str(normal_path), str(ao_path), str(report_path)],
        "metadata": {
            "evidence_kind": "surface-map-bake",
            "schema_version": 1,
            "status": report["status"],
            "normal_space": "world",
            "ao_method": report["maps"]["ambientOcclusion"]["method"],
            "report": report_path.name,
        },
    }


def _geometry_integrity(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Audit topology and finite geometry without changing the source mesh."""
    import numpy as np

    scene = _load_scene(input_path)
    mesh = _world_mesh(scene)
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    try:
        area_epsilon = float(params.get("area_epsilon", 1e-12) or 1e-12)
    except (TypeError, ValueError):
        area_epsilon = 1e-12
    area_epsilon = max(0.0, min(1.0, area_epsilon))
    try:
        require_watertight = params.get("require_watertight", False)
        if isinstance(require_watertight, str):
            require_watertight = require_watertight.strip().lower() not in {"", "0", "false", "no", "off"}
        require_watertight = bool(require_watertight)
    except Exception:
        require_watertight = False

    finite_vertices = bool(vertices.ndim == 2 and vertices.shape[1] == 3 and np.isfinite(vertices).all())
    if faces.size:
        triangles = vertices[faces]
        cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        areas = 0.5 * np.linalg.norm(cross, axis=1)
        degenerate_faces = int(np.count_nonzero(~np.isfinite(areas) | (areas <= area_epsilon)))
        canonical_faces = np.sort(faces, axis=1)
        duplicate_faces = int(len(canonical_faces) - len(np.unique(canonical_faces, axis=0)))
        edges = np.sort(
            np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])),
            axis=1,
        )
        _unique_edges, edge_counts = np.unique(edges, axis=0, return_counts=True)
        boundary_edges = int(np.count_nonzero(edge_counts == 1))
        nonmanifold_edges = int(np.count_nonzero(edge_counts > 2))
    else:
        degenerate_faces = duplicate_faces = boundary_edges = nonmanifold_edges = 0

    checks = {
        "finiteVertices": finite_vertices,
        "hasFaces": bool(len(faces) > 0),
        "degenerateFaces": degenerate_faces == 0,
        "duplicateFaces": duplicate_faces == 0,
        "nonManifoldEdges": nonmanifold_edges == 0,
        "watertight": bool(mesh.is_watertight),
        "windingConsistent": bool(mesh.is_winding_consistent),
        "volume": bool(mesh.is_volume),
    }
    hard_fail = not all(
        (checks[name] for name in ("finiteVertices", "hasFaces", "degenerateFaces", "duplicateFaces", "nonManifoldEdges"))
    )
    status = "fail" if hard_fail else "pass"
    if require_watertight and not checks["watertight"]:
        status = "needs_review" if status == "pass" else status
    report = {
        "schemaVersion": 1,
        "kind": "polykit.geometry-integrity",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {
            "name": input_path.name,
            "componentCount": len(_components(scene)),
            "vertices": int(len(vertices)),
            "faces": int(len(faces)),
            "bounds": [_vector(mesh.bounds[0]), _vector(mesh.bounds[1])],
        },
        "checks": checks,
        "counts": {
            "degenerateFaces": degenerate_faces,
            "duplicateFaces": duplicate_faces,
            "boundaryEdges": boundary_edges,
            "nonManifoldEdges": nonmanifold_edges,
        },
        "settings": {"areaEpsilon": _round(area_epsilon), "requireWatertight": require_watertight},
        "reviewNotes": [
            "This node audits the source and returns it unchanged; use mesh-repair or a modeling pass to mutate defects.",
            "Open boundaries are reported explicitly and only block the result when require_watertight is enabled.",
        ],
    }
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace_dir / f"{_token(input_path, 'integrity')}.json"
    _write_report(report_path, report)
    return {
        "filePath": str(input_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "geometry-integrity",
            "schema_version": 1,
            "status": status,
            "watertight": checks["watertight"],
            "report": report_path.name,
        },
    }


def _self_intersection_audit(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Detect triangle self-intersections in the world-space scene mesh."""
    import numpy as np

    scene = _load_scene(input_path)
    mesh = _world_mesh(scene)
    face_count = int(len(mesh.faces))
    if face_count == 0:
        raise ValueError("mesh contains no triangles for self-intersection audit")
    try:
        max_reported_faces = int(params.get("max_reported_faces", 64) or 64)
    except (TypeError, ValueError):
        max_reported_faces = 64
    max_reported_faces = max(1, min(10000, max_reported_faces))

    selection = np.zeros(face_count, dtype=bool)
    method_available = True
    method_error: str | None = None
    temp_dir = Path(tempfile.mkdtemp(prefix="polykit-self-intersection-"))
    try:
        # PyMeshLab's intersection selector is more reliable on a normalized
        # single mesh than on a scene container. Exporting the world-space
        # concatenation also makes component transforms part of the check.
        source_path = temp_dir / "world-mesh.ply"
        mesh.export(source_path)
        import pymeshlab

        mesh_set = pymeshlab.MeshSet()
        mesh_set.load_new_mesh(str(source_path))
        mesh_set.compute_selection_by_self_intersections_per_face()
        values = np.asarray(mesh_set.current_mesh().face_selection_array(), dtype=bool)
        if values.shape == selection.shape:
            selection = values
        else:
            method_available = False
            method_error = f"selector returned {values.size} faces; expected {face_count}"
    except Exception as exc:
        method_available = False
        method_error = f"{type(exc).__name__}: {exc}"
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    intersecting_faces = int(np.count_nonzero(selection))
    reported_indices = np.flatnonzero(selection)[:max_reported_faces].astype(int).tolist()
    status = "pass" if method_available and intersecting_faces == 0 else "fail" if method_available else "needs_review"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.self-intersection-audit",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {
            "name": input_path.name,
            "componentCount": len(_components(scene)),
            "vertices": int(len(mesh.vertices)),
            "faces": face_count,
            "bounds": [_vector(mesh.bounds[0]), _vector(mesh.bounds[1])],
        },
        "check": {
            "method": "pymeshlab.compute_selection_by_self_intersections_per_face",
            "available": method_available,
            "intersectingFaceCount": intersecting_faces,
            "intersectingFaceFraction": _round(intersecting_faces / face_count),
            "reportedFaceIndices": reported_indices,
            "reportedFaceLimit": max_reported_faces,
            "truncated": intersecting_faces > len(reported_indices),
        },
        "error": method_error,
        "reviewNotes": [
            "The selector reports faces participating in triangle-triangle self-intersections; both sides of a crossing are normally selected.",
            "Coplanar overlaps and near-contact within numerical tolerance still deserve a visual or distance-based review.",
            "This node returns the source mesh unchanged; repair or component separation belongs in a subsequent modeling step.",
        ],
    }
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace_dir / f"{_token(input_path, 'self-intersection')}.json"
    _write_report(report_path, report)
    return {
        "filePath": str(input_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "self-intersection-audit",
            "schema_version": 1,
            "status": status,
            "intersecting_faces": intersecting_faces,
            "report": report_path.name,
        },
    }


def _visual_hull(text: str, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Carve an orthographic visual hull from a compact JSON silhouette descriptor."""
    import numpy as np
    import trimesh

    try:
        descriptor = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"visual hull descriptor is not valid JSON: {exc.msg}") from exc
    if not isinstance(descriptor, dict):
        raise ValueError("visual hull descriptor must be a JSON object")

    bounds = descriptor.get("bounds")
    if not isinstance(bounds, dict):
        raise ValueError("visual hull descriptor.bounds must be an object")
    low = bounds.get("min")
    high = bounds.get("max")
    if not isinstance(low, list) or not isinstance(high, list) or len(low) != 3 or len(high) != 3:
        raise ValueError("visual hull bounds must provide min/max vectors of length three")
    try:
        low = [float(value) for value in low]
        high = [float(value) for value in high]
    except (TypeError, ValueError) as exc:
        raise ValueError("visual hull bounds must be numeric") from exc
    if not all(math.isfinite(value) for value in (*low, *high)) or any(a >= b for a, b in zip(low, high)):
        raise ValueError("visual hull bounds must be finite with min below max on every axis")
    try:
        resolution = int(descriptor.get("resolution", 16))
    except (TypeError, ValueError):
        resolution = 16
    if resolution < 4 or resolution > 32:
        raise ValueError("visual hull resolution must be between 4 and 32")
    try:
        triangle_budget = int(descriptor.get("triangleBudget", resolution**3 * 12))
    except (TypeError, ValueError):
        triangle_budget = resolution**3 * 12
    if triangle_budget < 1 or triangle_budget > 400000:
        raise ValueError("visual hull triangleBudget must be between 1 and 400000")

    views = descriptor.get("views")
    if not isinstance(views, list) or not 2 <= len(views) <= 3:
        raise ValueError("visual hull views must contain two or three silhouette views")
    view_axes = {"front": (0, False, 1, True, 2), "side": (2, False, 1, True, 0), "top": (0, False, 2, False, 1)}
    seen_axes: set[str] = set()
    normalized_views: list[dict[str, Any]] = []
    for index, view in enumerate(views):
        if not isinstance(view, dict):
            raise ValueError(f"visual hull view {index} must be an object")
        axis = str(view.get("axis") or "")
        if axis not in view_axes or axis in seen_axes:
            raise ValueError("visual hull views must use distinct front, side, or top axes")
        mask = view.get("mask")
        if not isinstance(mask, list) or not mask or not all(isinstance(row, str) and row for row in mask):
            raise ValueError(f"visual hull {axis} mask must be a non-empty array of binary strings")
        width = len(mask[0])
        if width < 4 or width > 256 or len(mask) < 4 or len(mask) > 256:
            raise ValueError(f"visual hull {axis} mask dimensions must be between 4 and 256")
        if any(len(row) != width or any(bit not in "01" for bit in row) for row in mask):
            raise ValueError(f"visual hull {axis} mask rows must have equal binary contents")
        if not any("1" in row for row in mask):
            raise ValueError(f"visual hull {axis} mask must contain foreground")
        seen_axes.add(axis)
        normalized_views.append({"axis": axis, "mask": mask, "confidence": float(view.get("confidence", 1.0))})

    spans = [high[axis] - low[axis] for axis in range(3)]

    def sample_mask(mask: list[str], u: float, v: float) -> bool:
        if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
            return False
        x = min(len(mask[0]) - 1, max(0, int(u * len(mask[0]))))
        y = min(len(mask) - 1, max(0, int(v * len(mask))))
        return mask[y][x] == "1"

    occupied: set[tuple[int, int, int]] = set()
    for ix in range(resolution):
        point_x = low[0] + (ix + 0.5) * spans[0] / resolution
        for iy in range(resolution):
            point_y = low[1] + (iy + 0.5) * spans[1] / resolution
            for iz in range(resolution):
                point = (point_x, point_y, low[2] + (iz + 0.5) * spans[2] / resolution)
                inside = True
                for view in normalized_views:
                    column_axis, column_flip, row_axis, row_flip, _free_axis = view_axes[view["axis"]]
                    u = (point[column_axis] - low[column_axis]) / spans[column_axis]
                    v = (point[row_axis] - low[row_axis]) / spans[row_axis]
                    if column_flip:
                        u = 1.0 - u
                    if row_flip:
                        v = 1.0 - v
                    if not sample_mask(view["mask"], u, v):
                        inside = False
                        break
                if inside:
                    occupied.add((ix, iy, iz))
    if not occupied:
        raise ValueError("visual hull masks have no overlapping occupied volume")

    # Emit only occupied/empty cell boundaries, welding shared grid corners so
    # the result is a closed surface rather than six independent shells.
    corner_ids: dict[tuple[int, int, int], int] = {}
    vertices: list[list[float]] = []

    def corner(gx: int, gy: int, gz: int) -> int:
        key = (gx, gy, gz)
        if key not in corner_ids:
            corner_ids[key] = len(vertices)
            vertices.append([
                low[0] + gx * spans[0] / resolution,
                low[1] + gy * spans[1] / resolution,
                low[2] + gz * spans[2] / resolution,
            ])
        return corner_ids[key]

    face_offsets = {
        (0, 1): ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)),
        (0, -1): ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)),
        (1, 1): ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)),
        (1, -1): ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),
        (2, 1): ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
        (2, -1): ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)),
    }
    faces: list[list[int]] = []
    for cell in sorted(occupied):
        for (axis, direction), offsets in face_offsets.items():
            neighbour = list(cell)
            neighbour[axis] += direction
            if tuple(neighbour) in occupied:
                continue
            points = [tuple(cell[axis] + offset[axis] for axis in range(3)) for offset in offsets]
            quad = [corner(*point) for point in points]
            faces.extend([[quad[0], quad[1], quad[2]], [quad[0], quad[2], quad[3]]])

    triangle_count = len(faces)
    status = "pass" if triangle_count <= triangle_budget else "needs_review"
    unconstrained = [name for axis, name in enumerate(("x", "y", "z")) if axis not in {view_axes[item["axis"]][4] for item in normalized_views}]
    limitations = [
        "A visual hull is an intersection of silhouette cones and therefore an upper bound; concavities hidden in every supplied silhouette are not reconstructed.",
    ]
    if len(normalized_views) < 3:
        limitations.append(f"Only {len(normalized_views)} views supplied; the hull remains loose along {', '.join(unconstrained) or 'no'}.")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    source_name = str(params.get("name") or descriptor.get("name") or "visual-hull")
    token = _slug(source_name)
    output_path = workspace_dir / f"{token}_{uuid.uuid4().hex[:8]}_visual-hull.glb"
    report_path = workspace_dir / f"{token}_{uuid.uuid4().hex[:8]}_visual-hull.json"
    trimesh.Trimesh(vertices=np.asarray(vertices, dtype=float), faces=np.asarray(faces, dtype=np.int64), process=False).export(output_path)
    report = {
        "schemaVersion": 1,
        "kind": "polykit.visual-hull",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "projection": "orthographic",
        "boundsSpace": "component-local",
        "bounds": {"min": _vector(low), "max": _vector(high)},
        "resolution": resolution,
        "triangleBudget": triangle_budget,
        "views": [{"axis": item["axis"], "width": len(item["mask"][0]), "height": len(item["mask"]), "confidence": _round(item["confidence"])} for item in normalized_views],
        "occupiedVoxelCount": len(occupied),
        "totalVoxelCount": resolution**3,
        "occupiedFraction": _round(len(occupied) / max(1, resolution**3)),
        "unconstrainedAxes": unconstrained,
        "mesh": {"vertices": len(vertices), "triangles": triangle_count, "watertight": True},
        "limitations": limitations,
        "reviewNotes": [
            "Silhouette masks are treated as hard evidence; a bad mask can carve away the entire hull.",
            "Inspect the generated surface and run geometry-integrity before using it as a production mesh.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "visual-hull",
            "schema_version": 1,
            "status": status,
            "triangle_count": triangle_count,
            "occupied_voxels": len(occupied),
            "report": report_path.name,
        },
    }


def _bvh_build(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic triangle AABB hierarchy as a JSON sidecar."""
    import numpy as np

    scene = _load_scene(input_path)
    mesh = _world_mesh(scene)
    vertices = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    try:
        leaf_triangles = int(params.get("leaf_triangles", 8) or 8)
    except (TypeError, ValueError):
        leaf_triangles = 8
    leaf_triangles = max(1, min(64, leaf_triangles))
    try:
        max_depth = int(params.get("max_depth", 24) or 24)
    except (TypeError, ValueError):
        max_depth = 24
    max_depth = max(1, min(64, max_depth))
    try:
        max_triangles = int(params.get("max_triangles", 200000) or 200000)
    except (TypeError, ValueError):
        max_triangles = 200000
    max_triangles = max(1, min(2000000, max_triangles))
    if len(faces) == 0:
        raise ValueError("mesh contains no triangles for BVH construction")

    triangle_count = len(faces)
    complete = triangle_count <= max_triangles
    triangle_indices = np.arange(min(triangle_count, max_triangles), dtype=np.int64)
    triangles = vertices[faces[triangle_indices]]
    triangle_min = triangles.min(axis=1)
    triangle_max = triangles.max(axis=1)
    triangle_center = (triangle_min + triangle_max) * 0.5
    nodes: list[dict[str, Any]] = []
    ordered: list[int] = []

    def visit(indices: Any, depth: int) -> int:
        node_index = len(nodes)
        bounds_min = triangle_min[indices].min(axis=0)
        bounds_max = triangle_max[indices].max(axis=0)
        node: dict[str, Any] = {
            "bounds": [_vector(bounds_min), _vector(bounds_max)],
            "depth": depth,
        }
        nodes.append(node)
        if len(indices) <= leaf_triangles or depth >= max_depth:
            start = len(ordered)
            ordered.extend(int(value) for value in indices.tolist())
            node.update({"leaf": True, "start": start, "count": len(indices)})
            return node_index
        extent = np.ptp(triangle_center[indices], axis=0)
        axis = int(np.argmax(extent))
        sorted_indices = indices[np.argsort(triangle_center[indices, axis], kind="mergesort")]
        middle = max(1, min(len(sorted_indices) - 1, len(sorted_indices) // 2))
        left = visit(sorted_indices[:middle], depth + 1)
        right = visit(sorted_indices[middle:], depth + 1)
        node.update({"leaf": False, "children": [left, right], "axis": axis})
        return node_index

    root = visit(triangle_indices, 0)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace_dir / f"{_token(input_path, 'bvh')}.json"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.bvh",
        "status": "pass" if complete else "needs_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {
            "name": input_path.name,
            "componentCount": len(_components(scene)),
            "vertices": int(len(vertices)),
            "faces": int(triangle_count),
        },
        "tree": {
            "root": root,
            "nodeCount": len(nodes),
            "leafTriangles": leaf_triangles,
            "maxDepth": max_depth,
            "triangleCount": int(triangle_count),
            "indexedTriangleCount": int(len(ordered)),
            "complete": complete,
            "nodes": nodes,
            "triangleOrder": ordered,
        },
        "reviewNotes": [
            "The sidecar stores triangle AABBs and a stable leaf order for server-side broad-phase queries.",
            "This artifact does not replace a renderer-native BVH; rebuild it after any geometry mutation.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(input_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "bvh",
            "schema_version": 1,
            "status": report["status"],
            "node_count": len(nodes),
            "triangle_count": int(triangle_count),
            "report": report_path.name,
        },
    }


def _read_gltf_payload(input_path: Path) -> tuple[dict[str, Any], list[bytes]]:
    """Read a glTF document and its local buffer payloads.

    The animation gate is intentionally able to inspect real binary attributes,
    not just the JSON declarations.  External .bin files and data URIs are
    accepted for .gltf; GLB's BIN chunks are associated with buffers that omit a
    URI, as required by the container format.
    """
    if input_path.suffix.lower() == ".gltf":
        document = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("glTF document must be an object")
        buffers: list[bytes] = []
        for index, descriptor in enumerate(document.get("buffers", [])):
            if not isinstance(descriptor, dict):
                raise ValueError(f"glTF buffer {index} must be an object")
            uri = descriptor.get("uri")
            if not isinstance(uri, str) or not uri:
                raise ValueError(f"glTF buffer {index} has no local URI")
            if uri.startswith("data:"):
                try:
                    encoded = uri.split(",", 1)[1]
                    buffers.append(base64.b64decode(encoded, validate=True))
                except (IndexError, ValueError) as exc:
                    raise ValueError(f"glTF buffer {index} data URI is invalid") from exc
            else:
                buffers.append((input_path.parent / uri).read_bytes())
        return document, buffers

    with input_path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise ValueError("GLB header is truncated")
        magic, version, total_length = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2:
            raise ValueError("unsupported GLB header")
        json_chunk: bytes | None = None
        binary_chunks: list[bytes] = []
        consumed = 12
        while consumed + 8 <= total_length:
            chunk_header = stream.read(8)
            if len(chunk_header) != 8:
                break
            chunk_length, chunk_type = struct.unpack("<II", chunk_header)
            consumed += 8
            if chunk_length > total_length - consumed:
                raise ValueError("GLB chunk exceeds declared file length")
            chunk = stream.read(chunk_length)
            if len(chunk) != chunk_length:
                raise ValueError("GLB chunk is truncated")
            consumed += chunk_length
            if chunk_type == 0x4E4F534A:
                json_chunk = chunk
            elif chunk_type == 0x004E4942:
                binary_chunks.append(chunk)
        if json_chunk is None:
            raise ValueError("GLB has no JSON chunk")
        document = json.loads(json_chunk.rstrip(b" \t\r\n").decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError("GLB JSON chunk must be an object")
        buffers: list[bytes] = []
        binary_index = 0
        for index, descriptor in enumerate(document.get("buffers", [])):
            if not isinstance(descriptor, dict):
                raise ValueError(f"GLB buffer {index} must be an object")
            uri = descriptor.get("uri")
            if isinstance(uri, str) and uri:
                if uri.startswith("data:"):
                    try:
                        buffers.append(base64.b64decode(uri.split(",", 1)[1], validate=True))
                    except (IndexError, ValueError) as exc:
                        raise ValueError(f"GLB buffer {index} data URI is invalid") from exc
                else:
                    buffers.append((input_path.parent / uri).read_bytes())
            else:
                if binary_index >= len(binary_chunks):
                    raise ValueError(f"GLB buffer {index} has no BIN chunk")
                buffers.append(binary_chunks[binary_index])
                binary_index += 1
        return document, buffers


def _read_gltf_document(input_path: Path) -> dict[str, Any]:
    return _read_gltf_payload(input_path)[0]


def _read_gltf_accessor(
    document: dict[str, Any], buffers: list[bytes], accessor_index: Any
) -> tuple[list[tuple[float | int, ...]], int, str]:
    """Decode one glTF accessor without pulling in a second runtime dependency."""
    accessors = document.get("accessors") if isinstance(document.get("accessors"), list) else []
    views = document.get("bufferViews") if isinstance(document.get("bufferViews"), list) else []
    if not isinstance(accessor_index, int) or not 0 <= accessor_index < len(accessors):
        raise ValueError("accessor index is out of range")
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict):
        raise ValueError("accessor must be an object")
    view_index = accessor.get("bufferView")
    if not isinstance(view_index, int) or not 0 <= view_index < len(views):
        raise ValueError("accessor has no valid bufferView")
    view = views[view_index]
    if not isinstance(view, dict):
        raise ValueError("bufferView must be an object")
    buffer_index = view.get("buffer", 0)
    if not isinstance(buffer_index, int) or not 0 <= buffer_index < len(buffers):
        raise ValueError("bufferView references an unavailable buffer")
    component = {
        5120: ("b", 1, True),
        5121: ("B", 1, False),
        5122: ("h", 2, True),
        5123: ("H", 2, False),
        5125: ("I", 4, False),
        5126: ("f", 4, True),
    }.get(accessor.get("componentType"))
    if component is None:
        raise ValueError("accessor componentType is unsupported")
    accessor_type = accessor.get("type")
    arity = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}.get(accessor_type)
    if arity is None:
        raise ValueError("accessor type is unsupported for skin data")
    count = accessor.get("count")
    if not isinstance(count, int) or count < 0 or count > 2_000_000:
        raise ValueError("accessor count is invalid or exceeds the audit budget")
    format_code, component_size, signed = component
    element_size = component_size * arity
    stride = view.get("byteStride", element_size)
    if not isinstance(stride, int) or stride < element_size:
        raise ValueError("bufferView byteStride is smaller than the accessor element")
    offset = int(view.get("byteOffset", 0) or 0) + int(accessor.get("byteOffset", 0) or 0)
    payload = buffers[buffer_index]
    if offset < 0 or offset + max(0, count - 1) * stride + element_size > len(payload):
        raise ValueError("accessor exceeds its buffer payload")
    normalized = bool(accessor.get("normalized", False))
    values: list[tuple[float | int, ...]] = []
    unpack_format = "<" + (format_code * arity)
    for row in range(count):
        raw = struct.unpack_from(unpack_format, payload, offset + row * stride)
        if normalized and format_code != "f":
            if signed:
                limit = float((1 << (component_size * 8 - 1)) - 1)
                converted = tuple(max(-1.0, float(value) / limit) for value in raw)
            else:
                limit = float((1 << (component_size * 8)) - 1)
                converted = tuple(float(value) / limit for value in raw)
            values.append(converted)
        else:
            values.append(tuple(raw))
    return values, count, str(accessor_type)


def _audit_skin_buffers(document: dict[str, Any], buffers: list[bytes]) -> dict[str, Any]:
    """Validate numeric skin attributes when the source carries readable buffers."""
    nodes = document.get("nodes") if isinstance(document.get("nodes"), list) else []
    skins = document.get("skins") if isinstance(document.get("skins"), list) else []
    meshes = document.get("meshes") if isinstance(document.get("meshes"), list) else []
    skin_limits: dict[int, int] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("mesh"), int) or not isinstance(node.get("skin"), int):
            continue
        skin_index = node["skin"]
        if 0 <= skin_index < len(skins) and isinstance(skins[skin_index], dict):
            joints = skins[skin_index].get("joints")
            if isinstance(joints, list) and joints:
                skin_limits[node["mesh"]] = max(skin_limits.get(node["mesh"], 0), len(joints))

    checked = 0
    valid = 0
    invalid = 0
    unverified = 0
    max_sum_error = 0.0
    errors: list[str] = []
    for mesh_index, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            continue
        primitives = mesh.get("primitives") if isinstance(mesh.get("primitives"), list) else []
        for primitive_index, primitive in enumerate(primitives):
            if not isinstance(primitive, dict):
                continue
            attributes = primitive.get("attributes") if isinstance(primitive.get("attributes"), dict) else {}
            if "JOINTS_0" not in attributes or "WEIGHTS_0" not in attributes:
                continue
            checked += 1
            try:
                accessors = document.get("accessors") if isinstance(document.get("accessors"), list) else []
                joint_accessor = accessors[attributes["JOINTS_0"]] if isinstance(attributes["JOINTS_0"], int) and 0 <= attributes["JOINTS_0"] < len(accessors) else None
                weight_accessor = accessors[attributes["WEIGHTS_0"]] if isinstance(attributes["WEIGHTS_0"], int) and 0 <= attributes["WEIGHTS_0"] < len(accessors) else None
                if not isinstance(joint_accessor, dict) or joint_accessor.get("componentType") not in {5121, 5123, 5125}:
                    raise ValueError("JOINTS_0 must use an unsigned integer component type")
                if not isinstance(weight_accessor, dict) or weight_accessor.get("componentType") not in {5121, 5123, 5126}:
                    raise ValueError("WEIGHTS_0 must use float or normalized unsigned integer components")
                joints, joint_count, joint_type = _read_gltf_accessor(document, buffers, attributes["JOINTS_0"])
                weights, weight_count, weight_type = _read_gltf_accessor(document, buffers, attributes["WEIGHTS_0"])
                position_count = None
                if "POSITION" in attributes:
                    _position_values, position_count, _position_type = _read_gltf_accessor(document, buffers, attributes["POSITION"])
                if joint_count != weight_count or (position_count is not None and position_count != joint_count):
                    raise ValueError("POSITION, JOINTS_0, and WEIGHTS_0 counts differ")
                if joint_type != "VEC4" or weight_type != "VEC4":
                    raise ValueError("JOINTS_0 and WEIGHTS_0 must be VEC4 attributes")
                joint_limit = skin_limits.get(mesh_index)
                if not joint_limit:
                    raise ValueError("mesh is not attached to a skin node with joints")
                primitive_error: str | None = None
                primitive_max_error = 0.0
                for row_index, (joint_row, weight_row) in enumerate(zip(joints, weights)):
                    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in (*joint_row, *weight_row)):
                        primitive_error = f"vertex {row_index} contains a non-finite joint or weight"
                        break
                    if any(float(value) < 0.0 or float(value) >= joint_limit for value in joint_row):
                        primitive_error = f"vertex {row_index} references a joint outside 0..{joint_limit - 1}"
                        break
                    weight_sum = sum(float(value) for value in weight_row)
                    sum_error = abs(weight_sum - 1.0)
                    primitive_max_error = max(primitive_max_error, sum_error)
                    if any(float(value) < -1e-6 for value in weight_row):
                        primitive_error = f"vertex {row_index} contains a negative skin weight"
                        break
                    if sum_error > 0.02:
                        primitive_error = f"vertex {row_index} skin weights sum to {weight_sum:.6f}, expected 1"
                        break
                max_sum_error = max(max_sum_error, primitive_max_error)
                if primitive_error:
                    invalid += 1
                    if len(errors) < 12:
                        errors.append(f"mesh {mesh_index} primitive {primitive_index}: {primitive_error}")
                else:
                    valid += 1
            except (TypeError, ValueError, OSError, struct.error) as exc:
                unverified += 1
                if len(errors) < 12:
                    errors.append(f"mesh {mesh_index} primitive {primitive_index}: {exc}")
    return {
        "checkedPrimitiveCount": checked,
        "validPrimitiveCount": valid,
        "invalidPrimitiveCount": invalid,
        "unverifiedPrimitiveCount": unverified,
        "maxWeightSumError": round(max_sum_error, 6),
        "errors": errors,
        "dataAvailable": bool(buffers),
    }


def _animation_audit(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Inspect glTF skin, weight, animation, and morph-target declarations."""
    document, buffers = _read_gltf_payload(input_path)
    nodes = document.get("nodes") if isinstance(document.get("nodes"), list) else []
    skins = document.get("skins") if isinstance(document.get("skins"), list) else []
    animations = document.get("animations") if isinstance(document.get("animations"), list) else []
    meshes = document.get("meshes") if isinstance(document.get("meshes"), list) else []
    valid_skins = [
        skin for skin in skins
        if isinstance(skin, dict)
        and isinstance(skin.get("joints"), list)
        and skin["joints"]
        and all(isinstance(index, int) and 0 <= index < len(nodes) for index in skin["joints"])
    ]
    skinned_primitives = 0
    morph_targets = 0
    animation_channels = 0
    for mesh in meshes:
        if not isinstance(mesh, dict):
            continue
        primitives = mesh.get("primitives") if isinstance(mesh.get("primitives"), list) else []
        for primitive in primitives:
            if not isinstance(primitive, dict):
                continue
            attributes = primitive.get("attributes") if isinstance(primitive.get("attributes"), dict) else {}
            if "JOINTS_0" in attributes and "WEIGHTS_0" in attributes:
                skinned_primitives += 1
            targets = primitive.get("targets") if isinstance(primitive.get("targets"), list) else []
            morph_targets += len(targets)
    for animation in animations:
        if isinstance(animation, dict) and isinstance(animation.get("channels"), list):
            animation_channels += len(animation["channels"])
    require_animation = params.get("require_animation", False)
    if isinstance(require_animation, str):
        require_animation = require_animation.strip().lower() not in {"", "0", "false", "no", "off"}
    require_animation = bool(require_animation)
    has_skin = bool(valid_skins and skinned_primitives > 0)
    has_animation = bool(animations and animation_channels > 0)
    weight_audit = _audit_skin_buffers(document, buffers)
    status = "pass" if has_skin and (has_animation or not require_animation) else "needs_review"
    if weight_audit["invalidPrimitiveCount"]:
        status = "fail"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace_dir / f"{_token(input_path, 'animation')}.json"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.animation-audit",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown"},
        "rig": {
            "skinCount": len(skins),
            "validSkinCount": len(valid_skins),
            "jointCount": max((len(skin.get("joints", [])) for skin in valid_skins), default=0),
            "skinnedPrimitiveCount": skinned_primitives,
            "bound": has_skin,
            "weights": weight_audit,
        },
        "animation": {
            "clipCount": len(animations),
            "channelCount": animation_channels,
            "hasAnimation": has_animation,
            "required": require_animation,
        },
        "morphTargets": {"targetCount": morph_targets, "present": morph_targets > 0},
        "reviewNotes": [
            "Numeric JOINTS_0/WEIGHTS_0 buffers are checked when local glTF/GLB payloads are available; deformation quality still requires a runtime smoke test.",
            "A mesh can be rigged without clips. Enable require_animation when the next stage expects an animation-bearing asset.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(input_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "animation-audit",
            "schema_version": 1,
            "status": status,
            "bound": has_skin,
            "invalid_weight_primitives": weight_audit["invalidPrimitiveCount"],
            "unverified_weight_primitives": weight_audit["unverifiedPrimitiveCount"],
            "clip_count": len(animations),
            "report": report_path.name,
        },
    }


def _morph_target_bake(input_path: Path, target_text: str, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Build relative morph deltas from world-space target vertices."""
    import numpy as np

    try:
        document = json.loads(target_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"morph target descriptor is not valid JSON: {exc.msg}") from exc
    if isinstance(document, dict) and isinstance(document.get("targets"), list):
        raw_targets = document["targets"]
        epsilon_value = document.get("epsilon", params.get("epsilon", 1e-6))
    elif isinstance(document, dict) and isinstance(document.get("vertices"), list):
        raw_targets = [document]
        epsilon_value = params.get("epsilon", 1e-6)
    elif isinstance(document, list):
        raw_targets = document
        epsilon_value = params.get("epsilon", 1e-6)
    else:
        raise ValueError("morph target descriptor must contain vertices or a targets array")
    if not raw_targets:
        raise ValueError("morph target descriptor contains no targets")
    try:
        epsilon = float(epsilon_value)
    except (TypeError, ValueError):
        epsilon = 1e-6
    epsilon = max(0.0, min(1.0, epsilon))

    mesh = _world_mesh(_load_scene(input_path))
    base = np.asarray(mesh.vertices, dtype=float)
    targets: list[dict[str, Any]] = []
    for index, raw_target in enumerate(raw_targets):
        if not isinstance(raw_target, dict) or not isinstance(raw_target.get("vertices"), list):
            raise ValueError(f"morph target {index} must contain a vertices array")
        vertices = np.asarray(raw_target["vertices"], dtype=float)
        if vertices.shape != base.shape:
            raise ValueError(
                f"morph target {raw_target.get('name', index)!r} has shape {list(vertices.shape)}; "
                f"base mesh has shape {list(base.shape)}"
            )
        if not np.isfinite(vertices).all():
            raise ValueError(f"morph target {raw_target.get('name', index)!r} contains non-finite vertices")
        delta = vertices - base
        magnitudes = np.linalg.norm(delta, axis=1)
        moved = magnitudes > epsilon
        rounded = np.where(moved[:, None], np.round(delta, 6), 0.0)
        targets.append({
            "name": str(raw_target.get("name") or f"morph-{index + 1}"),
            "deltas": rounded.tolist(),
            "movedVertexCount": int(np.count_nonzero(moved)),
            "vertexCount": int(len(base)),
            "maxDisplacement": _round(float(magnitudes.max(initial=0.0))),
            "isNoOp": not bool(np.any(moved)),
        })

    no_op = [target["name"] for target in targets if target["isNoOp"]]
    status = "needs_review" if no_op else "pass"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace_dir / f"{_token(input_path, 'morph')}.json"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.morph-target-bake",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {
            "name": input_path.name,
            "componentCount": len(_components(_load_scene(input_path))),
            "vertices": int(len(base)),
            "faces": int(len(mesh.faces)),
            "vertexOrder": "world-space concatenation in source scene traversal order",
        },
        "morphTargetsRelative": True,
        "epsilon": _round(epsilon),
        "targets": targets,
        "noOpTargets": no_op,
        "totalMovedVertices": sum(target["movedVertexCount"] for target in targets),
        "reviewNotes": [
            "Deltas are relative to the unchanged source mesh; consumers must enable morphTargetsRelative when applying them.",
            "Target vertices must preserve the source mesh's world-space vertex order. This node emits JSON evidence and does not mutate the GLB buffers.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(input_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "morph-target-bake",
            "schema_version": 1,
            "status": status,
            "target_count": len(targets),
            "total_moved_vertices": report["totalMovedVertices"],
            "report": report_path.name,
        },
    }


def _joint_loop_audit(input_path: Path, bone_text: str, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Measure independent vertex bands around declared joints."""
    import numpy as np

    try:
        document = json.loads(bone_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"joint descriptor is not valid JSON: {exc.msg}") from exc
    if isinstance(document, dict):
        bones = document.get("bones")
    else:
        bones = document
    if not isinstance(bones, list) or not bones:
        raise ValueError("joint descriptor must contain a non-empty bones array")
    try:
        min_loops = int(params.get("min_loops", 3) or 3)
    except (TypeError, ValueError):
        min_loops = 3
    min_loops = max(1, min(64, min_loops))
    try:
        radius_scale = float(params.get("radius_scale", 0.35) or 0.35)
    except (TypeError, ValueError):
        radius_scale = 0.35
    radius_scale = max(0.01, min(4.0, radius_scale))
    try:
        band_tolerance = float(params.get("band_tolerance", 0.5) or 0.5)
    except (TypeError, ValueError):
        band_tolerance = 0.5
    band_tolerance = max(0.01, min(4.0, band_tolerance))

    mesh = _world_mesh(_load_scene(input_path))
    vertices = np.asarray(mesh.vertices, dtype=float)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError("mesh vertices are not finite 3D positions")
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    for index, bone in enumerate(bones):
        if not isinstance(bone, dict):
            continue
        joint = bone.get("jointPos")
        tip = bone.get("tipPos")
        if not isinstance(joint, list) or not isinstance(tip, list) or len(joint) != 3 or len(tip) != 3:
            continue
        try:
            joint_vec = np.asarray([float(value) for value in joint], dtype=float)
            tip_vec = np.asarray([float(value) for value in tip], dtype=float)
        except (TypeError, ValueError):
            continue
        direction = tip_vec - joint_vec
        length = float(np.linalg.norm(direction))
        bone_id = str(bone.get("id") or bone.get("name") or f"bone-{index + 1}")
        if not math.isfinite(length) or length <= 1e-12:
            record = {"bone": bone_id, "loops": 0, "vertexCount": 0, "radius": 0.0, "minLoops": min_loops, "passes": False, "reason": "bone has zero length"}
            records.append(record)
            failures.append(f"{bone_id}: bone has zero length")
            continue
        axis = direction / length
        radius = length * radius_scale
        offsets = vertices - joint_vec
        along = offsets @ axis
        radial_squared = np.maximum(0.0, np.sum(offsets * offsets, axis=1) - along * along)
        selected = np.abs(along) <= radius
        selected &= np.sqrt(radial_squared) <= radius * 2.0
        projections = np.sort(along[selected])
        if len(projections) == 0:
            loops = 0
            axial_span = 0.0
        elif len(projections) == 1:
            loops = 1
            axial_span = 0.0
        else:
            axial_span = float(projections[-1] - projections[0])
            if axial_span <= 1e-12:
                loops = 1
            else:
                mean_gap = axial_span / max(1, len(projections) - 1)
                threshold = mean_gap * band_tolerance
                loops = 1 + int(np.count_nonzero(np.diff(projections) > threshold))
        passes = loops >= min_loops
        record = {
            "bone": bone_id,
            "loops": int(loops),
            "vertexCount": int(len(projections)),
            "radius": _round(radius),
            "axialSpan": _round(axial_span),
            "minLoops": min_loops,
            "passes": passes,
        }
        records.append(record)
        if not passes:
            failures.append(f"{bone_id}: {loops} loop(s) near joint, needs {min_loops}")
    if not records:
        raise ValueError("joint descriptor contains no usable bones with jointPos and tipPos")

    status = "pass" if not failures else "fail"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace_dir / f"{_token(input_path, 'joint-loops')}.json"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.joint-loop-audit",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown", "vertices": int(len(vertices))},
        "settings": {"minLoops": min_loops, "radiusScale": _round(radius_scale), "bandTolerance": _round(band_tolerance)},
        "joints": records,
        "checks": {"jointCount": len(records), "failingJointCount": len(failures), "failures": failures},
        "reviewNotes": [
            "Loops are measured as distinct vertex bands along each bone axis, not guessed quad topology.",
            "This is a topology readiness gate, not a deformation preview; run a pose smoke test after weights are bound.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(input_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "joint-loop-audit",
            "schema_version": 1,
            "status": status,
            "joint_count": len(records),
            "failing_joint_count": len(failures),
            "report": report_path.name,
        },
    }


def _clothing_blockout(descriptor_text: str, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Build closed, low-resolution garment solids from explicit dimensions."""
    import numpy as np
    import trimesh

    try:
        descriptor = json.loads(descriptor_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"clothing descriptor is not valid JSON: {exc.msg}") from exc
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("garments"), list) or not descriptor["garments"]:
        raise ValueError("clothing descriptor must contain a non-empty garments array")
    try:
        sections = max(8, min(96, int(params.get("sections", 24))))
    except (TypeError, ValueError):
        sections = 24
    try:
        default_clearance = float(params.get("default_clearance", 0.02) or 0.02)
    except (TypeError, ValueError):
        default_clearance = 0.02
    default_clearance = max(0.0, min(1.0, default_clearance))

    def number(item: dict[str, Any], key: str, *, default: float | None = None) -> float:
        value = item.get(key, default)
        if value is None or isinstance(value, bool):
            raise ValueError(f"garment {item.get('id', '?')!r} needs numeric {key}")
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"garment {item.get('id', '?')!r} has invalid {key}") from exc
        if not math.isfinite(parsed) or parsed <= 0.0:
            raise ValueError(f"garment {item.get('id', '?')!r} {key} must be positive")
        return parsed

    def position(item: dict[str, Any], key: str, default: float = 0.0) -> float:
        value = item.get(key, default)
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"garment {item.get('id', '?')!r} has invalid {key}") from exc
        if not math.isfinite(parsed):
            raise ValueError(f"garment {item.get('id', '?')!r} {key} must be finite")
        return parsed

    def clearance(item: dict[str, Any]) -> float:
        value = item.get("clearance", default_clearance)
        if isinstance(value, bool):
            raise ValueError(f"garment {item.get('id', '?')!r} clearance must be a non-negative number")
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"garment {item.get('id', '?')!r} clearance must be a non-negative number") from exc
        if not math.isfinite(parsed) or parsed < 0.0:
            raise ValueError(f"garment {item.get('id', '?')!r} clearance must be a non-negative number")
        return min(1.0, parsed)

    def ring_solid(bottom: tuple[float, float], top: tuple[float, float], height: float, radial_sections: int) -> Any:
        """Create a closed cylindrical frustum around the Y axis."""
        vertices: list[list[float]] = []
        for y, (rx, rz) in ((0.0, bottom), (height, top)):
            for index in range(radial_sections):
                angle = index / radial_sections * math.tau
                vertices.append([rx * math.cos(angle), y, rz * math.sin(angle)])
        faces: list[list[int]] = []
        for index in range(radial_sections):
            next_index = (index + 1) % radial_sections
            faces.append([index, next_index, radial_sections + next_index])
            faces.append([index, radial_sections + next_index, radial_sections + index])
        # Keep every face the same arity so the mesh can be exported to GLB.
        for index in range(1, radial_sections - 1):
            faces.append([0, index + 1, index])
            faces.append([radial_sections, radial_sections + index, radial_sections + index + 1])
        return trimesh.Trimesh(vertices=np.asarray(vertices, dtype=float), faces=np.asarray(faces, dtype=int), process=False)

    def rectangular_solid(bottom: tuple[float, float], top: tuple[float, float], height: float) -> Any:
        bx, bz = bottom[0] / 2.0, bottom[1] / 2.0
        tx, tz = top[0] / 2.0, top[1] / 2.0
        vertices = np.asarray([
            [-bx, 0.0, -bz], [bx, 0.0, -bz], [bx, 0.0, bz], [-bx, 0.0, bz],
            [-tx, height, -tz], [tx, height, -tz], [tx, height, tz], [-tx, height, tz],
        ], dtype=float)
        faces = np.asarray([
            [0, 1, 2, 3], [4, 7, 6, 5], [0, 4, 5, 1], [1, 5, 6, 2],
            [2, 6, 7, 3], [4, 0, 3, 7],
        ], dtype=int)
        return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

    scene = trimesh.Scene()
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    allowed = {"top", "skirt", "pants", "cape"}
    for index, item in enumerate(descriptor["garments"]):
        if not isinstance(item, dict):
            raise ValueError(f"garments[{index}] must be an object")
        garment_id = str(item.get("id") or f"garment-{index + 1}").strip()
        if not garment_id:
            raise ValueError(f"garments[{index}].id must be non-empty")
        if garment_id in seen_ids:
            raise ValueError(f"duplicate garment id: {garment_id}")
        seen_ids.add(garment_id)
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in allowed:
            raise ValueError(f"garment {garment_id!r} kind must be one of: {', '.join(sorted(allowed))}")
        width = number(item, "width")
        height = number(item, "height")
        depth = number(item, "depth")
        garment_clearance = clearance(item)
        base_y = position(item, "y")
        offset_x, offset_z = position(item, "x"), position(item, "z")
        if kind == "top":
            shoulder_width = number(item, "top_width", default=width)
            shoulder_depth = number(item, "top_depth", default=depth)
            mesh = rectangular_solid((width, depth), (shoulder_width, shoulder_depth), height)
            parts = [garment_id]
        elif kind == "skirt":
            top_width = number(item, "top_width", default=width * 0.78)
            mesh = ring_solid((width / 2.0, depth / 2.0), (top_width / 2.0, depth * 0.42), height, sections)
            parts = [garment_id]
        elif kind == "cape":
            mesh = rectangular_solid((width, max(depth, 0.01)), (width * 0.72, max(depth * 0.65, 0.01)), height)
            parts = [garment_id]
        else:
            gap = number(item, "leg_gap", default=width * 0.08)
            leg_width = max(width * 0.42, (width - gap) / 2.0)
            leg_depth = depth
            parts = [f"{garment_id}-L", f"{garment_id}-R"]
            left = trimesh.creation.box(extents=[leg_width, height, leg_depth])
            right = trimesh.creation.box(extents=[leg_width, height, leg_depth])
            left.apply_translation([-(leg_width + gap) / 2.0, height / 2.0, 0.0])
            right.apply_translation([(leg_width + gap) / 2.0, height / 2.0, 0.0])
            left.apply_translation([offset_x, base_y, offset_z])
            right.apply_translation([offset_x, base_y, offset_z])
            scene.add_geometry(left, geom_name=parts[0])
            scene.add_geometry(right, geom_name=parts[1])
            records.append({"id": garment_id, "kind": kind, "parts": parts, "width": round(width, 6), "height": round(height, 6), "depth": round(depth, 6), "clearance": round(garment_clearance, 6)})
            continue
        mesh.apply_translation([offset_x, base_y, offset_z])
        scene.add_geometry(mesh, geom_name=parts[0])
        records.append({"id": garment_id, "kind": kind, "parts": parts, "width": round(width, 6), "height": round(height, 6), "depth": round(depth, 6), "clearance": round(garment_clearance, 6)})

    if not scene.geometry:
        raise ValueError("clothing descriptor produced no geometry")
    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = f"clothing_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    scene.export(output_path)
    bounds = scene.bounds
    report = {
        "schemaVersion": 1,
        "kind": "polykit.clothing-blockout",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "garments": records,
        "summary": {"garmentCount": len(records), "geometryCount": len(scene.geometry), "bounds": [[round(float(value), 6) for value in row] for row in bounds]},
        "settings": {"sections": sections, "defaultClearance": round(default_clearance, 6), "solidBlockout": True},
        "reviewNotes": [
            "This node creates closed low-resolution garment solids from authored dimensions; it does not fit a body, sew 2D patterns, or run cloth simulation.",
            "Use Blender cloth/simulation setup and inspect clearance against the body before treating the blockout as production clothing.",
        ],
    }
    _write_report(report_path, report)
    return {"filePath": str(output_path), "sidecars": [str(report_path)], "metadata": {"evidence_kind": "clothing-blockout", "schema_version": 1, "status": "pass", "garment_count": len(records), "geometry_count": len(scene.geometry), "report": report_path.name}}


def main() -> None:
    raw = sys.stdin.readline()
    data = json.loads(raw)
    input_data = data.get("input") or {}
    params = data.get("params") or {}
    node_id = str(params.get("_node_id") or "collision-mesh")
    workspace_dir = Path(str(data.get("workspaceDir") or ""))
    if node_id == "clothing-blockout":
        descriptor_text = input_data.get("text")
        if not isinstance(descriptor_text, str) or not descriptor_text.strip():
            error("mesh-production: clothing blockout requires a JSON descriptor on the text input")
            return
        try:
            progress(5, "Reading clothing dimensions…")
            result = _clothing_blockout(descriptor_text, workspace_dir, params)
            progress(90, "Writing clothing blockout…")
            progress(100, "Clothing blockout ready")
            emit({"type": "done", "result": result})
        except Exception as exc:
            error(f"mesh-production: {exc}")
        return
    if node_id == "visual-hull":
        descriptor_text = input_data.get("text")
        if not isinstance(descriptor_text, str) or not descriptor_text.strip():
            error("mesh-production: visual hull requires a JSON descriptor on the text input")
            return
        try:
            progress(5, "Reading silhouette descriptor…")
            result = _visual_hull(descriptor_text, workspace_dir, params)
            progress(90, "Writing visual hull mesh…")
            progress(100, "Visual hull ready")
            emit({"type": "done", "result": result})
        except Exception as exc:
            error(f"mesh-production: {exc}")
        return
    # Multi-input process nodes receive named paths from the workflow executor
    # (for example ``meshPath`` for the target mesh). Keep ``filePath`` as the
    # compatibility shape used by the single-input collision/LOD nodes.
    input_raw = input_data.get("meshPath") or input_data.get("filePath")
    input_path = Path(str(input_raw)) if input_raw else None
    image_raw = input_data.get("imagePath")
    image_path = Path(str(image_raw)) if image_raw else None
    if input_path is None or not input_path.is_file():
        error(f"mesh-production: input mesh not found: {input_raw}")
        return
    if node_id == "projection-bake":
        if image_path is None or not image_path.is_file():
            error(f"mesh-production: projection image not found: {image_raw}")
            return
        if input_path is None or not input_path.is_file():
            error(f"mesh-production: projection mesh not found: {input_raw}")
            return
    target_text = input_data.get("text")
    if node_id == "morph-target-bake" and (not isinstance(target_text, str) or not target_text.strip()):
        error("mesh-production: morph target requires a JSON descriptor on the text input")
        return
    if node_id == "joint-loop-audit" and (not isinstance(target_text, str) or not target_text.strip()):
        error("mesh-production: joint loop audit requires a JSON bone descriptor on the text input")
        return
    try:
        progress(5, "Loading mesh…")
        if node_id == "collision-mesh":
            progress(25, "Building collision proxy…")
            result = _collision_mesh(input_path, workspace_dir, params)
        elif node_id == "lod-generate":
            progress(25, "Generating LOD levels…")
            result = _lod_generate(input_path, workspace_dir, params)
        elif node_id == "projection-bake":
            progress(25, "Projecting reference texture…")
            result = _projection_bake(image_path, input_path, workspace_dir, params)
        elif node_id == "uv-unwrap":
            progress(25, "Generating UV coordinates…")
            result = _uv_unwrap(input_path, workspace_dir, params)
        elif node_id == "surface-map-bake":
            progress(25, "Baking normal and AO maps…")
            result = _surface_map_bake(input_path, workspace_dir, params)
        elif node_id == "geometry-integrity":
            progress(25, "Auditing mesh integrity…")
            result = _geometry_integrity(input_path, workspace_dir, params)
        elif node_id == "self-intersection-audit":
            progress(25, "Checking triangle intersections…")
            result = _self_intersection_audit(input_path, workspace_dir, params)
        elif node_id == "bvh-build":
            progress(25, "Building triangle BVH…")
            result = _bvh_build(input_path, workspace_dir, params)
        elif node_id == "animation-audit":
            progress(25, "Auditing rig and animation metadata…")
            result = _animation_audit(input_path, workspace_dir, params)
        elif node_id == "morph-target-bake":
            progress(25, "Building relative morph deltas…")
            result = _morph_target_bake(input_path, target_text, workspace_dir, params)
        elif node_id == "joint-loop-audit":
            progress(25, "Auditing joint loop density…")
            result = _joint_loop_audit(input_path, target_text, workspace_dir, params)
        else:
            raise RuntimeError(f"unsupported mesh production node '{node_id}'")
        progress(90, "Writing mesh derivatives…")
        progress(100, "Mesh derivative ready")
        emit({"type": "done", "result": result})
    except Exception as exc:
        error(f"mesh-production: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"mesh-production: {exc}")
