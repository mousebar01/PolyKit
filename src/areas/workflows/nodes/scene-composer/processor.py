"""Merge a mesh fan-in into one named GLB scene."""
from __future__ import annotations

import json
import math
import re
import sys
import uuid
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def done(path: Path, *, metadata: dict[str, Any] | None = None) -> None:
    result: dict[str, Any] = {"filePath": str(path)}
    if metadata:
        result["metadata"] = metadata
    emit({"type": "done", "result": result})


def _slug(value: str) -> str:
    slug = re.sub(
        r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+",
        "_",
        value,
    ).strip("_").lower()
    return slug[:64] or "scene"


def _load_scene(path: Path):
    import trimesh

    if not path.is_file():
        raise ValueError(f"mesh file not found: {path}")
    if path.suffix.lower() not in {".glb", ".gltf", ".ply", ".obj", ".stl"}:
        raise ValueError(f"unsupported mesh format: {path.suffix or 'unknown'}")
    return trimesh.load(path, force="scene")


def _parse_placements(raw: object) -> object:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("placements must be valid JSON") from exc
    if not isinstance(raw, (dict, list)):
        raise ValueError("placements must be a JSON object or array")
    return raw


def _placement_for(placements: object, index: int, path: Path) -> dict[str, Any]:
    if isinstance(placements, dict):
        candidate = placements.get(path.name) or placements.get(path.stem) or placements.get(str(index))
    else:
        candidate = placements[index] if index < len(placements) else None  # type: ignore[arg-type]
    if candidate is None:
        return {}
    if not isinstance(candidate, dict):
        raise ValueError(f"placement for {path.name} must be an object")
    return candidate


def _normalise_coordinate_system(value: object) -> str:
    raw = str(value or "glTF-Y-up").strip().lower().replace("_", "-")
    if raw in {"gltf-y-up", "gltf", "y-up", "yup", "canonical"}:
        return "glTF-Y-up"
    if raw in {"blender-z-up", "blender", "z-up", "zup"}:
        return "Blender-Z-up"
    raise ValueError("coordinate_system must be 'glTF-Y-up' or 'Blender-Z-up'")


def _to_gltf_position(position: tuple[float, float, float], coordinate_system: str) -> tuple[float, float, float]:
    if coordinate_system == "Blender-Z-up":
        # Blender's X/Y/Z frame maps to glTF's X/Z/-Y frame.
        return position[0], position[2], -position[1]
    return position


def _placement_matrix(
    placement: dict[str, Any],
    path: Path,
    *,
    scale_multiplier: float = 1.0,
    coordinate_system: str = "glTF-Y-up",
):
    import numpy as np
    from trimesh.transformations import euler_matrix, translation_matrix

    def vector(key: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
        value = placement.get(key, default)
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError(f"placement {path.name}.{key} must contain three numbers")
        try:
            result = tuple(float(item) for item in value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"placement {path.name}.{key} must contain three numbers") from exc
        if not all(math.isfinite(item) for item in result):
            raise ValueError(f"placement {path.name}.{key} must contain finite numbers")
        return result  # type: ignore[return-value]

    position = _to_gltf_position(vector("position", (0.0, 0.0, 0.0)), coordinate_system)
    rotation = vector("rotation", (0.0, 0.0, 0.0))
    scale = placement.get("scale", 1.0)
    if isinstance(scale, (int, float)):
        scale_vector = (float(scale),) * 3
    elif isinstance(scale, (list, tuple)) and len(scale) == 3:
        try:
            scale_vector = tuple(float(item) for item in scale)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"placement {path.name}.scale must be a number or three numbers") from exc
    else:
        raise ValueError(f"placement {path.name}.scale must be a number or three numbers")
    if not all(math.isfinite(item) and item > 0 for item in scale_vector):
        raise ValueError(f"placement {path.name}.scale must contain positive finite numbers")
    if not math.isfinite(scale_multiplier) or scale_multiplier <= 0:
        raise ValueError(f"placement {path.name} contains an invalid size scale")
    scale_vector = tuple(item * scale_multiplier for item in scale_vector)
    # ScenePlan and Three.js both use radians for Euler rotations. Keeping the
    # same unit here means a composed GLB and the live plan preview agree.
    radians = rotation
    scale_matrix = np.diag([*scale_vector, 1.0])
    rotation_matrix = euler_matrix(*radians, axes="sxyz")
    if coordinate_system == "Blender-Z-up":
        basis = np.asarray(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)))
        frame = np.eye(4)
        frame[:3, :3] = basis
        rotation_matrix = frame @ rotation_matrix @ frame.T
    return translation_matrix(position) @ rotation_matrix @ scale_matrix


def _bounds_after_transform(bounds: Any, transform: Any):
    import numpy as np

    if bounds is None or len(bounds) != 2:
        raise ValueError("mesh scene has no finite bounds")
    minimum = np.asarray(bounds[0], dtype=float)
    maximum = np.asarray(bounds[1], dtype=float)
    if minimum.shape != (3,) or maximum.shape != (3,) or not np.all(np.isfinite([minimum, maximum])):
        raise ValueError("mesh scene has invalid bounds")
    corners = np.array(
        [
            [x, y, z, 1.0]
            for x in (minimum[0], maximum[0])
            for y in (minimum[1], maximum[1])
            for z in (minimum[2], maximum[2])
        ],
        dtype=float,
    )
    transformed = (np.asarray(transform, dtype=float) @ corners.T).T[:, :3]
    return transformed.min(axis=0), transformed.max(axis=0), maximum - minimum


def _fit_scale(scene: Any, placement: dict[str, Any], path: Path) -> float:
    """Return a uniform scale that fits a source mesh to semantic dimensions."""

    import numpy as np

    raw_target = placement.get("size")
    if raw_target is None:
        return 1.0
    if not isinstance(raw_target, (list, tuple)) or len(raw_target) != 3:
        raise ValueError(f"placement {path.name}.size must contain three numbers")
    try:
        target = tuple(float(value) for value in raw_target)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"placement {path.name}.size must contain three numbers") from exc
    if not all(math.isfinite(value) and value > 0 for value in target):
        raise ValueError(f"placement {path.name}.size must contain positive finite numbers")
    _, _, source_size = _bounds_after_transform(scene.bounds, np.eye(4))
    if not all(math.isfinite(value) and value >= 0 for value in source_size) or not any(
        value > 1e-9 for value in source_size
    ):
        raise ValueError(f"mesh scene has an empty or invalid size: {path.name}")
    # A valid planar mesh (for example a wall or decal) has zero extent on one
    # axis.  It can still be composed; that axis simply imposes no uniform
    # scale constraint rather than causing a division-by-zero rejection.
    return min(target[index] / source_size[index] for index in range(3) if source_size[index] > 1e-9)


def _merge(
    paths: list[Path],
    output: Path,
    placements: object | None = None,
    *,
    coordinate_system: str = "glTF-Y-up",
) -> tuple[int, int]:
    import trimesh
    from trimesh.transformations import translation_matrix

    placement_map = _parse_placements(placements)
    result = trimesh.Scene()
    geometry_count = 0
    face_count = 0
    used_names: set[str] = set()
    for source_index, path in enumerate(paths):
        scene = _load_scene(path)
        source_stem = _slug(path.stem)
        placement = _placement_for(placement_map, source_index, path)
        placement_transform = None
        if placement:
            fit_scale = _fit_scale(scene, placement, path)
            authored = dict(placement)
            authored["position"] = [0.0, 0.0, 0.0]
            authored_transform = _placement_matrix(
                authored,
                path,
                scale_multiplier=fit_scale,
                coordinate_system=coordinate_system,
            )
            transformed_min, transformed_max, _ = _bounds_after_transform(scene.bounds, authored_transform)
            target_position = placement.get("position", (0.0, 0.0, 0.0))
            if not isinstance(target_position, (list, tuple)) or len(target_position) != 3:
                raise ValueError(f"placement {path.name}.position must contain three numbers")
            target = tuple(float(value) for value in target_position)
            if not all(math.isfinite(value) for value in target):
                raise ValueError(f"placement {path.name}.position must contain finite numbers")
            target = _to_gltf_position(target, coordinate_system)
            center = ((transformed_min[0] + transformed_max[0]) / 2.0, transformed_min[1], (transformed_min[2] + transformed_max[2]) / 2.0)
            placement_transform = translation_matrix(
                (target[0] - center[0], target[1] - center[1], target[2] - center[2])
            ) @ authored_transform
        for node_index, node_name in enumerate(scene.graph.nodes_geometry):
            transform, geometry_name = scene.graph[node_name]
            geometry = scene.geometry[geometry_name]
            safe_name = _slug(str(node_name) or geometry_name or source_stem)
            candidate = f"{source_stem}/{safe_name}"
            if candidate in used_names:
                candidate = f"{candidate}-{node_index}"
            used_names.add(candidate)
            result.add_geometry(
                geometry.copy(),
                node_name=candidate,
                geom_name=candidate,
                transform=placement_transform @ transform if placement_transform is not None else transform,
                metadata={"polyKit": {"source": path.name, "sourceIndex": source_index, "sourceNode": str(node_name)}},
            )
            geometry_count += 1
            face_count += len(getattr(geometry, "faces", []))
    if geometry_count == 0:
        raise ValueError("no mesh geometry was found in the connected assets")
    result.metadata.setdefault("polyKit", {})
    result.metadata["polyKit"].update({
        "composition": "scene-composer",
        "sourceCount": len(paths),
        # Mesh assets are exchanged through glTF/Three.js coordinates. Keep
        # the placement convention explicit in the artifact so inspectors and
        # downstream agents do not mistake the vector for Blender Z-up data.
        "coordinateSystem": "glTF-Y-up",
        "placementConvention": "position=[x,ground_y,z]; x/z are placement-centre coordinates",
        "inputCoordinateSystem": coordinate_system,
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    result.export(output)
    return geometry_count, face_count


def main() -> None:
    payload = json.loads(sys.stdin.readline())
    input_data = payload.get("input") or {}
    params = payload.get("params") or {}
    workspace_dir = Path(str(payload.get("workspaceDir") or ".")).expanduser().resolve()
    raw_paths = input_data.get("filePaths")
    if not isinstance(raw_paths, list):
        single = input_data.get("filePath")
        raw_paths = [single] if single else []
    paths = [Path(str(value)).expanduser().resolve() for value in raw_paths if str(value or "").strip()]
    if not paths:
        error("scene-composer: connect at least one mesh")
        return
    try:
        progress(10, f"Loading {len(paths)} mesh assets…")
        name = _slug(str(params.get("output_name") or "scene"))
        output = workspace_dir / f"{name}_{uuid.uuid4().hex[:8]}_composed.glb"
        coordinate_system = _normalise_coordinate_system(params.get("coordinate_system"))
        geometry_count, face_count = _merge(
            paths,
            output,
            params.get("placements"),
            coordinate_system=coordinate_system,
        )
        progress(90, f"Writing {geometry_count} scene objects…")
        emit({"type": "log", "message": f"Composed {len(paths)} assets, {geometry_count} objects, {face_count} triangles"})
        progress(100, "Scene composition ready")
        done(
            output,
            metadata={
                "composition": "scene-composer",
                "coordinateSystem": "glTF-Y-up",
                "placementConvention": "position=[x,ground_y,z]; x/z are placement-centre coordinates",
                "sourceCount": len(paths),
                "geometryCount": geometry_count,
                "faceCount": face_count,
            },
        )
    except Exception as exc:
        error(f"scene-composer: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"scene-composer: {exc}")
