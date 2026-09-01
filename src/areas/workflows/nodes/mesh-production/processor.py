"""Game-ready mesh derivative process nodes.

The processor follows PolyKit's line-delimited process protocol. Collision
meshes and LOD files are generated in the run-private process workspace; the
normal workflow engine publishes the primary mesh and any JSON/GLB sidecars at
the selected sink.
"""
from __future__ import annotations

import json
import math
import re
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


def main() -> None:
    raw = sys.stdin.readline()
    data = json.loads(raw)
    input_data = data.get("input") or {}
    params = data.get("params") or {}
    # Multi-input process nodes receive named paths from the workflow executor
    # (for example ``meshPath`` for the target mesh). Keep ``filePath`` as the
    # compatibility shape used by the single-input collision/LOD nodes.
    input_raw = input_data.get("meshPath") or input_data.get("filePath")
    input_path = Path(str(input_raw)) if input_raw else None
    image_raw = input_data.get("imagePath")
    image_path = Path(str(image_raw)) if image_raw else None
    node_id = str(params.get("_node_id") or "collision-mesh")
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
    workspace_dir = Path(str(data.get("workspaceDir") or ""))
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
