"""Mesh evidence process nodes.

This pack keeps evidence generation in the existing process-node protocol. The
first operation, ``component-audit``, is intentionally read-only: it inspects
the scene graph and returns the original mesh plus a JSON report describing
component footprints and pairwise spatial relationships.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import sys
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
    return result.strip("_").lower()[:48] or "asset"


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _round(value: Any) -> float:
    number = float(value)
    return round(number, 6) if math.isfinite(number) else 0.0


def _vector(values: Any) -> list[float]:
    return [_round(item) for item in values]


def _bounds(points: Any) -> tuple[Any, Any]:
    import numpy as np

    array = np.asarray(points, dtype=float)
    if array.ndim != 2 or array.shape[0] == 0 or array.shape[1] != 3:
        raise ValueError("mesh component has no valid 3D vertices")
    if not np.isfinite(array).all():
        raise ValueError("mesh component contains non-finite vertices")
    return array.min(axis=0), array.max(axis=0)


def _footprints(minimum: Any, maximum: Any, scene_min: Any, scene_max: Any) -> dict[str, Any]:
    extents = maximum - minimum
    scene_extents = scene_max - scene_min
    result: dict[str, Any] = {}
    for name, first, second in (("xy", 0, 1), ("xz", 0, 2), ("yz", 1, 2)):
        denominator = max(float(scene_extents[first] * scene_extents[second]), 1e-12)
        area = max(0.0, float(extents[first] * extents[second]))
        result[name] = {
            "bounds": [
                _round(minimum[first]),
                _round(minimum[second]),
                _round(maximum[first]),
                _round(maximum[second]),
            ],
            "area": _round(area),
            "sceneCoverage": _round(area / denominator),
        }
    return result


def _spatial_relation(first: dict[str, Any], second: dict[str, Any], near_tolerance: float) -> dict[str, Any]:
    import numpy as np

    first_min = np.asarray(first["worldBounds"][0], dtype=float)
    first_max = np.asarray(first["worldBounds"][1], dtype=float)
    second_min = np.asarray(second["worldBounds"][0], dtype=float)
    second_max = np.asarray(second["worldBounds"][1], dtype=float)
    overlap = np.minimum(first_max, second_max) - np.maximum(first_min, second_min)
    gap_axes = np.maximum(0.0, np.maximum(second_min - first_max, first_min - second_max))
    gap = float(np.linalg.norm(gap_axes))
    if bool(np.all(overlap >= -1e-7)):
        relation = "overlap"
    elif gap <= near_tolerance:
        relation = "near"
    else:
        relation = "separate"
    return {
        "a": first["id"],
        "b": second["id"],
        "relation": relation,
        "gap": _round(gap),
        "overlapAxes": [bool(value >= -1e-7) for value in overlap],
    }


def _json_value(value: Any, *, normalize_color: bool = False) -> Any:
    """Convert numpy/PIL values to small JSON-safe values."""
    if value is None:
        return None
    if hasattr(value, "size") and hasattr(value, "mode") and not isinstance(value, (str, bytes, bytearray)):
        return {
            "type": "image",
            "width": int(value.size[0]),
            "height": int(value.size[1]),
            "mode": str(value.mode),
        }
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        result = [_json_value(item, normalize_color=normalize_color) for item in value]
        if normalize_color:
            flattened = [item for item in result if isinstance(item, (int, float))]
            if flattened and max(flattened) > 1.0:
                return [round(float(item) / 255.0, 6) for item in flattened]
        return result
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    return str(value)


def _channel(value: Any, source: str, *, normalize_color: bool = False) -> dict[str, Any]:
    present = value is not None
    return {
        "present": present,
        "source": source if present else "missing",
        "confidence": 1.0 if present else 0.0,
        "gate": "pass" if present else "needs_review",
        "value": _json_value(value, normalize_color=normalize_color) if present else None,
    }


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _material_channels(material: Any) -> dict[str, dict[str, Any]]:
    if material is None:
        return {
            "baseColor": _channel(None, "material-missing"),
            "metallic": _channel(None, "material-missing"),
            "roughness": _channel(None, "material-missing"),
            "normal": _channel(None, "material-missing"),
            "occlusion": _channel(None, "material-missing"),
            "emissive": _channel(None, "material-missing"),
        }
    attributes = {
        "baseColor": (_first_present(getattr(material, "baseColorTexture", None), getattr(material, "baseColorFactor", None)), "material-base-color", True),
        "metallic": (getattr(material, "metallicFactor", None), "material-metallic-factor", False),
        "roughness": (getattr(material, "roughnessFactor", None), "material-roughness-factor", False),
        "normal": (getattr(material, "normalTexture", None), "material-normal-texture", False),
        "occlusion": (getattr(material, "occlusionTexture", None), "material-occlusion-texture", False),
        "emissive": (_first_present(getattr(material, "emissiveTexture", None), getattr(material, "emissiveFactor", None)), "material-emissive", False),
    }
    return {
        name: _channel(value, source, normalize_color=normalize_color)
        for name, (value, source, normalize_color) in attributes.items()
    }


def _material_audit(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    scene = _load_scene(input_path)
    material_by_key: dict[str, dict[str, Any]] = {}
    component_materials: list[dict[str, Any]] = []
    missing_material_components: list[str] = []
    for node_name in scene.graph.nodes_geometry:
        try:
            _transform, geometry_name = scene.graph.get(node_name)
            geometry = scene.geometry[geometry_name]
            visual = getattr(geometry, "visual", None)
            material = getattr(visual, "material", None)
            material_name = str(getattr(material, "name", "") or "") if material is not None else ""
            key = material_name or f"{type(material).__name__ if material is not None else 'none'}:{id(material)}"
            if key not in material_by_key:
                channels = _material_channels(material)
                required_missing = [name for name in ("baseColor", "roughness") if not channels[name]["present"]]
                material_by_key[key] = {
                    "id": f"material-{len(material_by_key) + 1}",
                    "name": material_name or None,
                    "type": type(material).__name__ if material is not None else None,
                    "channels": channels,
                    "missingRequiredChannels": required_missing,
                    "usedBy": [],
                }
            entry = material_by_key[key]
            entry["usedBy"].append(str(node_name))
            component_materials.append({"component": str(node_name), "material": entry["id"]})
            if material is None:
                missing_material_components.append(str(node_name))
        except (KeyError, TypeError, ValueError):
            missing_material_components.append(str(node_name))

    materials = list(material_by_key.values())
    missing_required = [
        material["id"]
        for material in materials
        if material["missingRequiredChannels"]
    ]
    status = "pass" if materials and not missing_required and not missing_material_components else "needs_review"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.material-audit",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown"},
        "materials": materials,
        "componentMaterials": component_materials,
        "checks": {
            "missingMaterialComponents": missing_material_components,
            "missingRequiredChannels": missing_required,
            "requiredChannels": ["baseColor", "roughness"],
        },
        "reviewNotes": [
            "A present channel is evidence that the asset declares a value or texture; it is not a visual match score.",
            "Use the channel source and confidence fields to gate material matching before adding detail noise.",
        ],
    }
    return _write_mesh_result(input_path, workspace_dir, "material-audit", report)


def _bool_param(params: dict[str, Any], name: str, default: bool) -> bool:
    value = params.get(name, default)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _normalize_mesh(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    scene = _load_scene(input_path)
    source_min, source_max = _bounds(scene.bounds)
    source_extents = source_max - source_min
    source_size = float(np.max(source_extents))
    if not math.isfinite(source_size) or source_size <= 1e-9:
        raise ValueError("mesh has no measurable extent")
    try:
        target_size = float(params.get("target_size", 1.0) or 1.0)
    except (TypeError, ValueError):
        target_size = 1.0
    target_size = max(1e-4, min(10000.0, target_size))
    axis_name = str(params.get("up_axis") or "Y").upper()
    if axis_name not in {"X", "Y", "Z"}:
        axis_name = "Y"
    up_axis = {"X": 0, "Y": 1, "Z": 2}[axis_name]
    scale = target_size / source_size
    center_horizontal = _bool_param(params, "center_horizontal", True)
    ground = _bool_param(params, "ground", True)
    source_center = (source_min + source_max) / 2.0
    translation = np.zeros(3, dtype=float)
    if center_horizontal:
        for axis in range(3):
            if axis != up_axis:
                translation[axis] = -float(source_center[axis]) * scale
    if ground:
        translation[up_axis] = -float(source_min[up_axis]) * scale
    transform = np.eye(4, dtype=float)
    transform[:3, :3] *= scale
    transform[:3, 3] = translation
    scene.apply_transform(transform)
    output_min, output_max = _bounds(scene.bounds)

    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}_normalized.glb"
    report_path = workspace_dir / f"{token}_normalize-mesh.json"
    scene.export(output_path)
    report = {
        "schemaVersion": 1,
        "kind": "polykit.mesh-normalization",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown"},
        "settings": {
            "targetSize": target_size,
            "upAxis": axis_name,
            "centerHorizontal": center_horizontal,
            "ground": ground,
        },
        "transform": {
            "scale": _round(scale),
            "translation": _vector(translation),
            "matrix": [[_round(value) for value in row] for row in transform.tolist()],
        },
        "bounds": {
            "before": [_vector(source_min), _vector(source_max)],
            "after": [_vector(output_min), _vector(output_max)],
            "beforeExtents": _vector(source_extents),
            "afterExtents": _vector(output_max - output_min),
        },
        "checks": {
            "maxExtent": _round(float(np.max(output_max - output_min))),
            "groundCoordinate": _round(float(output_min[up_axis])),
            "horizontalCenter": _vector([(output_min[axis] + output_max[axis]) / 2.0 for axis in range(3)]),
        },
        "reviewNotes": [
            "Normalization changes object coordinates but does not alter component topology or material declarations.",
            "Use the reported transform when matching a reference camera or binding a scene asset to a declared scale.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "mesh-normalization",
            "schema_version": 1,
            "status": "pass",
            "scale": _round(scale),
            "report": report_path.name,
        },
    }


def _turntable_evidence(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for turntable evidence: {exc}") from exc

    scene = _load_scene(input_path)
    scene_min, scene_max = _bounds(scene.bounds)
    center = (scene_min + scene_max) / 2.0
    radius = max(float(np.max(scene_max - scene_min)) / 2.0, 1e-6)
    views = _bounded_int(params.get("views", 8), 8, 4, 12)
    elevation = max(-80.0, min(80.0, float(params.get("elevation", 20.0) or 20.0)))
    max_faces = _bounded_int(params.get("max_faces", 20000), 20000, 100, 200000)
    image_size = _bounded_int(params.get("image_size", 512), 512, 128, 2048)
    columns = min(4, views)
    rows = int(math.ceil(views / columns))
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_turntable.png"
    report_path = workspace_dir / f"{token}_turntable.json"

    components: list[tuple[Any, Any, str]] = []
    for node_name in scene.graph.nodes_geometry:
        try:
            transform, geometry_name = scene.graph.get(node_name)
            geometry = scene.geometry[geometry_name]
            if not hasattr(geometry, "vertices") or not hasattr(geometry, "faces"):
                continue
            components.append((geometry, np.asarray(transform, dtype=float), str(node_name)))
        except (KeyError, TypeError, ValueError):
            continue
    if not components:
        raise ValueError("mesh contains no renderable components")

    canvas = Image.new("RGB", (columns * image_size, rows * image_size), "white")
    draw = ImageDraw.Draw(canvas)
    colors = ((79, 70, 229), (8, 145, 178), (217, 119, 6), (190, 18, 60), (21, 128, 61), (124, 58, 237))
    rendered_faces = 0
    view_manifest: list[dict[str, Any]] = []
    elevation_radians = math.radians(elevation)
    for index in range(views):
        azimuth = (360.0 * index) / views
        azimuth_radians = math.radians(azimuth)
        # Camera basis for a stable orthographic projection.  The forward
        # vector points from the object toward the virtual camera.
        forward = np.array([
            math.cos(elevation_radians) * math.cos(azimuth_radians),
            math.cos(elevation_radians) * math.sin(azimuth_radians),
            math.sin(elevation_radians),
        ])
        right = np.array([-math.sin(azimuth_radians), math.cos(azimuth_radians), 0.0])
        up = np.array([
            -math.sin(elevation_radians) * math.cos(azimuth_radians),
            -math.sin(elevation_radians) * math.sin(azimuth_radians),
            math.cos(elevation_radians),
        ])
        cell_x = (index % columns) * image_size
        cell_y = (index // columns) * image_size
        margin = max(8, image_size // 12)
        scale = (image_size - 2 * margin) / max(2.0 * radius, 1e-6)
        polygons: list[tuple[float, list[tuple[float, float]], tuple[int, int, int]]] = []
        for component_index, (geometry, transform, _node_name) in enumerate(components):
            vertices = np.asarray(geometry.vertices, dtype=float)
            vertices = (vertices @ transform[:3, :3].T) + transform[:3, 3]
            faces = np.asarray(geometry.faces, dtype=int)
            if len(faces) > max_faces:
                stride = max(1, int(math.ceil(len(faces) / max_faces)))
                faces = faces[::stride][:max_faces]
            for face in faces:
                points = vertices[face]
                relative = points - center
                projected_x = relative @ right
                projected_y = relative @ up
                projected_depth = float(np.mean(relative @ forward))
                polygon = [
                    (
                        cell_x + image_size / 2.0 + float(x) * scale,
                        cell_y + image_size / 2.0 - float(y) * scale,
                    )
                    for x, y in zip(projected_x, projected_y)
                ]
                polygons.append((projected_depth, polygon, colors[component_index % len(colors)]))
            if index == 0:
                rendered_faces += len(faces)
        # Painter's algorithm: draw distant triangles first, then near ones.
        for _depth, polygon, color in sorted(polygons, key=lambda item: item[0]):
            draw.polygon(polygon, fill=color, outline=(17, 24, 39))
        draw.rectangle((cell_x, cell_y, cell_x + image_size - 1, cell_y + image_size - 1), outline=(203, 213, 225), width=2)
        view_manifest.append({"index": index, "azimuth": _round(azimuth), "elevation": _round(elevation)})
    canvas.save(output_path, format="PNG")

    report = {
        "schemaVersion": 1,
        "kind": "polykit.turntable-evidence",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown"},
        "scene": {
            "componentCount": len(components),
            "worldBounds": [_vector(scene_min), _vector(scene_max)],
            "center": _vector(center),
            "radius": _round(radius),
        },
        "render": {
            "views": view_manifest,
            "imageSize": image_size,
            "columns": columns,
            "rows": rows,
            "maxFacesPerComponent": max_faces,
            "facesRenderedPerView": rendered_faces,
        },
        "reviewNotes": [
            "The sheet is silhouette/assembly evidence, not a photorealistic material match.",
            "Use the view angles to reproduce a camera pose in Blender or a downstream reference comparison.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "turntable-evidence",
            "schema_version": 1,
            "status": "pass",
            "view_count": views,
            "component_count": len(components),
            "report": report_path.name,
        },
    }


def _load_scene(input_path: Path) -> Any:
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError(f"trimesh is required for asset evidence: {exc}") from exc

    loaded = trimesh.load(input_path, force="scene", process=False)
    if isinstance(loaded, trimesh.Scene):
        return loaded
    if isinstance(loaded, trimesh.Trimesh):
        return trimesh.Scene(loaded)
    raise RuntimeError(f"unsupported mesh payload: {type(loaded).__name__}")


def _component_audit(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    scene = _load_scene(input_path)
    scene_min, scene_max = _bounds(scene.bounds)
    near_tolerance = max(0.0, min(100.0, float(params.get("near_tolerance", 0.02) or 0.02)))
    nodes = list(scene.graph.nodes_geometry)
    components: list[dict[str, Any]] = []
    invalid_components: list[str] = []
    for node_name in nodes:
        try:
            transform, geometry_name = scene.graph.get(node_name)
            geometry = scene.geometry[geometry_name]
            if not hasattr(geometry, "vertices") or not hasattr(geometry, "faces"):
                invalid_components.append(str(node_name))
                continue
            world_vertices = np.asarray(geometry.vertices, dtype=float)
            world_vertices = (world_vertices @ np.asarray(transform, dtype=float)[:3, :3].T) + np.asarray(transform, dtype=float)[:3, 3]
            minimum, maximum = _bounds(world_vertices)
            material = getattr(getattr(geometry, "visual", None), "material", None)
            material_name = str(getattr(material, "name", "") or "") or None
            components.append({
                "id": str(node_name),
                "geometry": str(geometry_name),
                "vertices": int(len(geometry.vertices)),
                "faces": int(len(geometry.faces)),
                "localBounds": [_vector(geometry.bounds[0]), _vector(geometry.bounds[1])],
                "worldBounds": [_vector(minimum), _vector(maximum)],
                "extents": _vector(maximum - minimum),
                "center": _vector((minimum + maximum) / 2.0),
                "surfaceArea": _round(getattr(geometry, "area", 0.0)),
                "volume": _round(abs(float(getattr(geometry, "volume", 0.0)))),
                "material": material_name,
                "footprints": _footprints(minimum, maximum, scene_min, scene_max),
            })
        except (KeyError, TypeError, ValueError, np.linalg.LinAlgError):
            invalid_components.append(str(node_name))

    relations: list[dict[str, Any]] = []
    for index, first in enumerate(components):
        for second in components[index + 1:]:
            relations.append(_spatial_relation(first, second, near_tolerance))
    overlap_count = sum(1 for relation in relations if relation["relation"] == "overlap")
    near_count = sum(1 for relation in relations if relation["relation"] == "near")
    zero_extent = [
        component["id"]
        for component in components
        if any(float(value) <= 1e-9 for value in component["extents"])
    ]
    status = "pass" if components and not invalid_components and not zero_extent else "needs_review"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.component-audit",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown"},
        "scene": {
            "componentCount": len(components),
            "worldBounds": [_vector(scene_min), _vector(scene_max)],
            "extents": _vector(scene_max - scene_min),
            "nearTolerance": near_tolerance,
        },
        "components": components,
        "relations": relations,
        "checks": {
            "invalidComponents": invalid_components,
            "zeroExtentComponents": zero_extent,
            "overlapCount": overlap_count,
            "nearCount": near_count,
        },
        "reviewNotes": [
            "Overlap is evidence for review, not an automatic failure; manufactured assemblies may intentionally intersect.",
            "Use component ids and footprints as stable targets for later object-ID renders or localized correction.",
        ],
    }
    return _write_mesh_result(input_path, workspace_dir, "component-audit", report)


def _write_mesh_result(input_path: Path, workspace_dir: Path, tag: str, report: dict[str, Any]) -> dict[str, Any]:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}_{tag}{input_path.suffix.lower() or '.glb'}"
    report_path = workspace_dir / f"{token}_{tag}.json"
    shutil.copy2(input_path, output_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": report["kind"].removeprefix("polykit."),
            "schema_version": report["schemaVersion"],
            "status": report["status"],
            "component_count": report.get("scene", {}).get("componentCount", 0),
            "report": report_path.name,
        },
    }


def main() -> None:
    raw = sys.stdin.readline()
    data = json.loads(raw)
    input_data = data.get("input") or {}
    params = data.get("params") or {}
    input_raw = input_data.get("filePath")
    input_path = Path(str(input_raw)) if input_raw else None
    if input_path is None or not input_path.is_file():
        error(f"asset-evidence: input mesh not found: {input_raw}")
        return
    workspace_dir = Path(str(data.get("workspaceDir") or ""))
    node_id = str(params.get("_node_id") or "component-audit")
    try:
        progress(5, "Loading mesh scene…")
        if node_id == "component-audit":
            progress(25, "Measuring component footprints…")
            result = _component_audit(input_path, workspace_dir, params)
        elif node_id == "material-audit":
            progress(25, "Reading material channels…")
            result = _material_audit(input_path, workspace_dir, params)
        elif node_id == "normalize-mesh":
            progress(25, "Computing normalization transform…")
            result = _normalize_mesh(input_path, workspace_dir, params)
        elif node_id == "turntable-evidence":
            progress(25, "Rendering turntable views…")
            result = _turntable_evidence(input_path, workspace_dir, params)
        else:
            raise RuntimeError(f"unsupported asset evidence node '{node_id}'")
        progress(90, "Writing mesh evidence…")
        progress(100, "Evidence ready")
        emit({"type": "done", "result": result})
    except Exception as exc:
        error(f"asset-evidence: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"asset-evidence: {exc}")
