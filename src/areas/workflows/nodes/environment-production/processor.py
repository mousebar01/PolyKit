"""Deterministic terrain mesh generation for the server-owned world pipeline."""
from __future__ import annotations

import hashlib
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _finite(value: Any, label: str, *, positive: bool = False, non_negative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(parsed) or (positive and parsed <= 0.0) or (non_negative and parsed < 0.0):
        qualifier = "positive" if positive else "non-negative" if non_negative else "finite"
        raise ValueError(f"{label} must be a {qualifier} number")
    return parsed


def _unit_pair(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must contain two normalized coordinates")
    pair = (_finite(value[0], f"{label}[0]"), _finite(value[1], f"{label}[1]"))
    if not all(0.0 <= item <= 1.0 for item in pair):
        raise ValueError(f"{label} coordinates must be within [0, 1]")
    return pair


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 1.0 if value >= edge1 else 0.0
    t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _lerp(first: float, second: float, factor: float) -> float:
    return first + (second - first) * factor


def _hash_noise(seed: int, x: int, z: int) -> float:
    value = (int(seed) & 0xFFFFFFFF) ^ ((x * 0x45D9F3B) & 0xFFFFFFFF) ^ ((z * 0x119DE1F3) & 0xFFFFFFFF)
    value = (value ^ (value >> 16)) * 0x45D9F3B & 0xFFFFFFFF
    value = (value ^ (value >> 16)) * 0x45D9F3B & 0xFFFFFFFF
    value ^= value >> 16
    return (value & 0xFFFFFFFF) / 4294967295.0


def _stable_seed(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:4], "little")


def _value_noise(seed: int, x: float, z: float) -> float:
    x0, z0 = math.floor(x), math.floor(z)
    fx, fz = x - x0, z - z0
    sx, sz = _smoothstep(0.0, 1.0, fx), _smoothstep(0.0, 1.0, fz)
    lower = _lerp(_hash_noise(seed, x0, z0), _hash_noise(seed, x0 + 1, z0), sx)
    upper = _lerp(_hash_noise(seed, x0, z0 + 1), _hash_noise(seed, x0 + 1, z0 + 1), sx)
    return _lerp(lower, upper, sz) * 2.0 - 1.0


def _ridge_noise(seed: int, x: float, z: float) -> float:
    return 1.0 - abs(_value_noise(seed, x, z))


def _segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    dx, dz = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dz * dz
    if length_squared <= 1e-12:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    factor = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dz) / length_squared))
    return math.hypot(point[0] - (start[0] + factor * dx), point[1] - (start[1] + factor * dz))


def _region_height(region: dict[str, Any], u: float, v: float, normalized_distance: float, seed: int) -> float:
    kind = str(region["kind"]).strip().lower()
    amplitude = float(region["amplitude"])
    roughness = max(0.0, min(1.0, float(region["roughness"])))
    frequency = 2.0 + 4.0 / max(float(region["radius"]), 0.04)
    terrain = _value_noise(seed, u * frequency, v * frequency)
    detail = _value_noise(seed ^ 0xBA5E, u * frequency * 4.0 + 11.3, v * frequency * 4.0 - 5.7)
    if kind in {"mountain", "snow"}:
        shape = _ridge_noise(seed ^ 0x5EED, u * frequency * 0.75, v * frequency * 0.75) ** 1.25
        shape *= 0.75 if kind == "snow" else 1.0
    elif kind == "volcanic":
        shape = max(0.0, 1.0 - normalized_distance) ** 1.6 - _smoothstep(0.02, 0.16, normalized_distance) * 0.42
        shape += _ridge_noise(seed ^ 0xC0DE, u * frequency, v * frequency) * 0.18
    elif kind in {"canyon", "mesa"}:
        terrace_count = max(1, int(region.get("terraces") or (5 if kind == "canyon" else 3)))
        base = terrain * 0.5 + 0.5
        quantized = base * terrace_count
        fraction = quantized - math.floor(quantized)
        shape = (math.floor(quantized) + _smoothstep(0.35, 0.65, fraction)) / terrace_count
    elif kind in {"dunes", "desert"}:
        dune = _value_noise(seed ^ 0xD00D, u * frequency * 0.5 + v * frequency * 0.22, v * frequency * 1.4)
        shape = dune * (0.5 if kind == "dunes" else 0.22) + terrain * (0.2 if kind == "desert" else 0.0)
    elif kind in {"hills", "forest"}:
        shape = terrain * 0.5 + 0.5
        if kind == "forest":
            shape *= 0.8
    elif kind == "water":
        shape = -max(0.0, 1.0 - normalized_distance) ** 1.4
    elif kind == "swamp":
        shape = 0.15 - _value_noise(seed ^ 0x51A7, u * frequency * 1.2, v * frequency * 1.2) * 0.5
        shape *= 0.4
    elif kind == "beach":
        shape = terrain * 0.1
    else:
        shape = (terrain * 0.5 + 0.5) * 0.5
    return float(region["base_elevation"]) + (shape + detail * 0.05 * (0.4 + roughness)) * amplitude


def _terrain_mesh(descriptor: dict[str, Any], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import trimesh

    size = _finite(descriptor.get("size", 12.0), "size", positive=True)
    seed_raw = descriptor.get("seed", 0)
    if isinstance(seed_raw, bool):
        raise ValueError("seed must be an integer")
    try:
        seed = int(seed_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("seed must be an integer") from exc
    sea_level = _finite(descriptor.get("seaLevel", descriptor.get("sea_level", 0.0)), "seaLevel")
    resolution_raw = params.get("resolution", descriptor.get("resolution", 64))
    try:
        resolution = max(8, min(256, int(resolution_raw)))
    except (TypeError, ValueError):
        resolution = 64
    regions_raw = descriptor.get("regions", [])
    if not isinstance(regions_raw, list):
        raise ValueError("regions must be a list")
    regions: list[dict[str, Any]] = []
    seen_region_ids: set[str] = set()
    for index, raw_region in enumerate(regions_raw):
        if not isinstance(raw_region, dict):
            raise ValueError(f"regions[{index}] must be an object")
        region_id = str(raw_region.get("id") or f"region-{index + 1}").strip()
        if not region_id or region_id in seen_region_ids:
            raise ValueError(f"regions[{index}] has an empty or duplicate id")
        seen_region_ids.add(region_id)
        center = _unit_pair(raw_region.get("center", [0.5, 0.5]), f"region {region_id}.center")
        radius = _finite(raw_region.get("radius", 0.35), f"region {region_id}.radius", positive=True)
        amplitude = _finite(raw_region.get("amplitude", 1.0), f"region {region_id}.amplitude")
        base_elevation = _finite(raw_region.get("baseElevation", raw_region.get("base_elevation", 0.0)), f"region {region_id}.baseElevation")
        roughness = _finite(raw_region.get("roughness", 0.5), f"region {region_id}.roughness", non_negative=True)
        regions.append({
            "id": region_id,
            "center": center,
            "radius": radius,
            "kind": str(raw_region.get("kind") or "plains"),
            "amplitude": amplitude,
            "base_elevation": base_elevation,
            "roughness": min(1.0, roughness),
            "terraces": raw_region.get("terraces"),
        })

    rivers_raw = descriptor.get("rivers", [])
    if not isinstance(rivers_raw, list):
        raise ValueError("rivers must be a list")
    rivers: list[dict[str, Any]] = []
    for index, raw_river in enumerate(rivers_raw):
        if not isinstance(raw_river, dict):
            raise ValueError(f"rivers[{index}] must be an object")
        raw_path = raw_river.get("path")
        if not isinstance(raw_path, list) or len(raw_path) < 2:
            raise ValueError(f"river {index + 1} path must contain at least two points")
        path = [_unit_pair(point, f"river {index + 1}.path[{point_index}]") for point_index, point in enumerate(raw_path)]
        rivers.append({
            "id": str(raw_river.get("id") or f"river-{index + 1}"),
            "path": path,
            "width": _finite(raw_river.get("width", 0.25), f"river {index + 1}.width", positive=True),
            "depth": _finite(raw_river.get("depth", 0.6), f"river {index + 1}.depth", non_negative=True),
        })

    def height_at(u: float, v: float) -> float:
        weighted_height = 0.0
        total_weight = 0.0
        max_amplitude = 1.0
        for region in regions:
            du, dv = u - region["center"][0], v - region["center"][1]
            normalized_distance = math.hypot(du, dv) / max(region["radius"], 1e-6)
            weight = 1.0 - _smoothstep(0.55, 1.15, normalized_distance)
            if weight <= 0.001:
                continue
            weighted_height += _region_height(region, u, v, min(1.0, normalized_distance), seed ^ _stable_seed(region["id"])) * weight
            total_weight += weight
            max_amplitude = max(max_amplitude, abs(region["amplitude"]))
        background = (_value_noise(seed ^ 0xBA5E, u * 4.0, v * 4.0) * 0.5 + 0.15) * max(1.0, max_amplitude)
        height = (weighted_height + background * max(0.0, 1.0 - total_weight)) / max(1.0, total_weight)
        edge_distance = min(u, v, 1.0 - u, 1.0 - v)
        height = _lerp(sea_level - max(4.0, max_amplitude * 2.0), height, _smoothstep(0.0, 0.08, edge_distance))
        for river in rivers:
            distance = min(_segment_distance((u, v), river["path"][path_index], river["path"][path_index + 1]) for path_index in range(len(river["path"]) - 1)) * size
            influence = river["width"] * 2.5
            if distance > influence:
                continue
            center_weight = 1.0 - _smoothstep(river["width"] * 0.5, influence, distance)
            height = min(height, _lerp(height, sea_level - river["depth"], center_weight))
        return float(height)

    heights = np.empty((resolution, resolution), dtype=np.float32)
    for row in range(resolution):
        v = row / (resolution - 1)
        for column in range(resolution):
            heights[row, column] = height_at(column / (resolution - 1), v)
    vertices = np.empty((resolution * resolution, 3), dtype=np.float32)
    for row in range(resolution):
        for column in range(resolution):
            index = row * resolution + column
            vertices[index] = ((column / (resolution - 1) - 0.5) * size, heights[row, column], (row / (resolution - 1) - 0.5) * size)
    faces: list[list[int]] = []
    for row in range(resolution - 1):
        for column in range(resolution - 1):
            lower_left = row * resolution + column
            lower_right = lower_left + 1
            upper_left = lower_left + resolution
            upper_right = upper_left + 1
            faces.extend(([lower_left, lower_right, upper_right], [lower_left, upper_right, upper_left]))
    terrain = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces, dtype=np.int64), process=False)
    height_min, height_max = float(heights.min()), float(heights.max())
    height_span = max(height_max - height_min, 1e-6)
    colors = np.empty((len(vertices), 4), dtype=np.uint8)
    for index, value in enumerate(vertices[:, 1]):
        t = max(0.0, min(1.0, (float(value) - height_min) / height_span))
        colors[index] = (int(48 + 88 * t), int(74 + 106 * t), int(42 + 64 * t), 255)
    terrain.visual = trimesh.visual.ColorVisuals(vertex_colors=colors)
    scene = trimesh.Scene()
    scene.add_geometry(terrain, geom_name="terrain", node_name="terrain")
    include_water = params.get("include_water", True)
    if isinstance(include_water, str):
        include_water = include_water.strip().lower() not in {"", "0", "false", "no", "off"}
    water_included = bool(include_water)
    if water_included:
        thickness = _finite(params.get("water_thickness", 0.04), "water_thickness", positive=True)
        water = trimesh.creation.box(extents=(size, thickness, size))
        water.apply_translation((0.0, sea_level - thickness * 0.5, 0.0))
        water.visual = trimesh.visual.ColorVisuals(vertex_colors=np.tile(np.asarray([44, 117, 156, 210], dtype=np.uint8), (len(water.vertices), 1)))
        scene.add_geometry(water, geom_name="water", node_name="water")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = f"terrain_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    scene.export(output_path)
    vertex_hash = hashlib.sha256(vertices.tobytes()).hexdigest()
    report = {
        "schemaVersion": 1,
        "kind": "polykit.terrain-mesh",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {"seed": seed, "size": round(size, 6), "resolution": resolution, "regionCount": len(regions), "riverCount": len(rivers)},
        "terrain": {"vertexCount": int(len(vertices)), "faceCount": int(len(faces)), "bounds": [[round(float(value), 6) for value in row] for row in scene.geometry["terrain"].bounds], "minHeight": round(height_min, 6), "maxHeight": round(height_max, 6), "vertexHash": vertex_hash},
        "water": {"included": water_included, "seaLevel": round(sea_level, 6), "geometryName": "water" if water_included else None},
        "regions": [{"id": region["id"], "kind": region["kind"], "center": list(region["center"]), "radius": round(region["radius"], 6), "amplitude": round(region["amplitude"], 6)} for region in regions],
        "rivers": [{"id": river["id"], "pointCount": len(river["path"]), "width": round(river["width"], 6), "depth": round(river["depth"], 6)} for river in rivers],
        "reviewNotes": [
            "The terrain and water are production GLB geometry owned by the server; browser heightfield previews remain a separate planning aid.",
            "Seeded generation is deterministic for the same descriptor and resolution. Validate biome material assignment, erosion, and asset placement in a downstream scene review.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {"evidence_kind": "terrain-mesh", "schema_version": 1, "status": "pass", "vertex_count": len(vertices), "face_count": len(faces), "water_included": water_included, "vertex_hash": vertex_hash, "report": report_path.name},
    }


def _city_blockout(descriptor: dict[str, Any], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Build a flat, deterministic road-and-building massing model."""
    import numpy as np
    import trimesh

    width = _finite(descriptor.get("width", descriptor.get("size", 40.0)), "width", positive=True)
    depth = _finite(descriptor.get("depth", descriptor.get("size", 40.0)), "depth", positive=True)
    seed_raw = descriptor.get("seed", 0)
    if isinstance(seed_raw, bool):
        raise ValueError("seed must be an integer")
    try:
        seed = int(seed_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("seed must be an integer") from exc

    def count(key: str, default: int) -> int:
        raw = params.get(key, descriptor.get(key, default))
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        return max(1, min(24, value))

    rows, columns = count("rows", 4), count("columns", 4)
    road_width = _finite(descriptor.get("roadWidth", descriptor.get("road_width", 3.0)), "roadWidth", positive=True)
    setback = _finite(descriptor.get("setback", 0.6), "setback", non_negative=True)
    height_range = descriptor.get("buildingHeight", descriptor.get("building_height", descriptor.get("heightRange", [4.0, 16.0])))
    if not isinstance(height_range, (list, tuple)) or len(height_range) != 2:
        raise ValueError("buildingHeight must contain minimum and maximum values")
    minimum_height = _finite(height_range[0], "buildingHeight[0]", positive=True)
    maximum_height = _finite(height_range[1], "buildingHeight[1]", positive=True)
    if maximum_height < minimum_height:
        raise ValueError("buildingHeight maximum must be at least its minimum")
    density_raw = params.get("building_density", descriptor.get("buildingDensity", 1.0))
    density = max(0.0, min(1.0, _finite(density_raw, "buildingDensity")))
    include_ground = params.get("include_ground", descriptor.get("includeGround", True))
    if isinstance(include_ground, str):
        include_ground = include_ground.strip().lower() not in {"", "0", "false", "no", "off"}
    include_ground = bool(include_ground)
    block_width = (width - road_width * (columns + 1)) / columns
    block_depth = (depth - road_width * (rows + 1)) / rows
    if block_width <= 2.0 * setback or block_depth <= 2.0 * setback:
        raise ValueError("roadWidth and setback leave no positive building lots")

    scene = trimesh.Scene()
    records: list[dict[str, Any]] = []

    def add_box(name: str, extents: tuple[float, float, float], position: tuple[float, float, float], color: tuple[int, int, int, int]) -> None:
        mesh = trimesh.creation.box(extents=extents)
        mesh.apply_translation(position)
        mesh.visual = trimesh.visual.ColorVisuals(vertex_colors=np.tile(np.asarray(color, dtype=np.uint8), (len(mesh.vertices), 1)))
        scene.add_geometry(mesh, geom_name=name, node_name=name)

    if include_ground:
        add_box("ground", (width, 0.2, depth), (0.0, -0.1, 0.0), (92, 96, 98, 255))
    road_height = 0.035
    for column in range(columns + 1):
        x = -width / 2.0 + road_width / 2.0 + column * (block_width + road_width)
        add_box(f"road-vertical-{column + 1}", (road_width, road_height, depth), (x, 0.018, 0.0), (39, 43, 47, 255))
    for row in range(rows + 1):
        z = -depth / 2.0 + road_width / 2.0 + row * (block_depth + road_width)
        add_box(f"road-horizontal-{row + 1}", (width, road_height, road_width), (0.0, 0.018, z), (39, 43, 47, 255))

    building_count = 0
    for row in range(rows):
        for column in range(columns):
            lot_center_x = -width / 2.0 + road_width + block_width / 2.0 + column * (block_width + road_width)
            lot_center_z = -depth / 2.0 + road_width + block_depth / 2.0 + row * (block_depth + road_width)
            lot_seed = seed ^ _stable_seed(f"lot:{row}:{column}")
            if _hash_noise(lot_seed, row, column) > density:
                continue
            width_jitter = 0.72 + 0.2 * _hash_noise(lot_seed ^ 0xA11, column, row)
            depth_jitter = 0.72 + 0.2 * _hash_noise(lot_seed ^ 0xB22, row, column)
            building_width = max(0.2, (block_width - 2.0 * setback) * width_jitter)
            building_depth = max(0.2, (block_depth - 2.0 * setback) * depth_jitter)
            height = minimum_height + (maximum_height - minimum_height) * (0.2 + 0.8 * _hash_noise(lot_seed ^ 0xC33, column, row))
            building_id = f"building-{row + 1}-{column + 1}"
            palette = (112 + int(_hash_noise(lot_seed ^ 0xD44, row, column) * 52), 118 + int(_hash_noise(lot_seed ^ 0xE55, column, row) * 46), 126 + int(_hash_noise(lot_seed ^ 0xF66, row, column) * 40), 255)
            add_box(building_id, (building_width, height, building_depth), (lot_center_x, height / 2.0 + 0.04, lot_center_z), palette)
            building_count += 1
            records.append({"id": building_id, "row": row, "column": column, "position": [round(lot_center_x, 6), 0.0, round(lot_center_z, 6)], "size": [round(building_width, 6), round(height, 6), round(building_depth, 6)]})

    if not scene.geometry:
        raise ValueError("city descriptor produced no geometry")
    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = f"city_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    scene.export(output_path)
    layout_hash = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    report = {
        "schemaVersion": 1,
        "kind": "polykit.city-blockout",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {"seed": seed, "width": round(width, 6), "depth": round(depth, 6), "rows": rows, "columns": columns, "roadWidth": round(road_width, 6), "setback": round(setback, 6), "buildingDensity": round(density, 6)},
        "summary": {"buildingCount": building_count, "roadCount": rows + columns + 2, "geometryCount": len(scene.geometry), "layoutHash": layout_hash, "bounds": [[round(float(value), 6) for value in row] for row in scene.bounds]},
        "buildings": records,
        "reviewNotes": [
            "This is flat city massing for scene composition and camera blocking; it does not infer parcels, traffic, doors, windows, interiors, or zoning from an image.",
            "Building placement and height are deterministic for the same descriptor. Connect the result to terrain, materials, roads, and semantic scene validation before production use.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {"evidence_kind": "city-blockout", "schema_version": 1, "status": "pass", "building_count": building_count, "geometry_count": len(scene.geometry), "layout_hash": layout_hash, "report": report_path.name},
    }


def _build_room_blockout(descriptor: dict[str, Any], params: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Build room geometry and its report without publishing intermediate artifacts."""
    import numpy as np
    import trimesh

    width = _finite(descriptor.get("width", 6.0), "width", positive=True)
    depth = _finite(descriptor.get("depth", 5.0), "depth", positive=True)
    height = _finite(descriptor.get("height", 3.0), "height", positive=True)
    wall_thickness = _finite(descriptor.get("wallThickness", descriptor.get("wall_thickness", 0.2)), "wallThickness", positive=True)
    floor_thickness = _finite(descriptor.get("floorThickness", descriptor.get("floor_thickness", 0.15)), "floorThickness", positive=True)
    ceiling_thickness = _finite(descriptor.get("ceilingThickness", descriptor.get("ceiling_thickness", 0.15)), "ceilingThickness", positive=True)
    if wall_thickness >= min(width, depth) * 0.5:
        raise ValueError("wallThickness must leave a positive room interior")

    def as_bool(value: Any, label: str, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        if isinstance(value, (bool, int, float)):
            return bool(value)
        raise ValueError(f"{label} must be a boolean")

    include_floor = as_bool(params.get("include_floor", descriptor.get("includeFloor")), "includeFloor", True)
    include_ceiling = as_bool(params.get("include_ceiling", descriptor.get("includeCeiling")), "includeCeiling", True)
    scene = trimesh.Scene()
    records: list[dict[str, Any]] = []
    openings_by_wall: dict[str, list[dict[str, Any]]] = {wall: [] for wall in ("front", "back", "left", "right")}
    colors = {
        "floor": (119, 94, 67, 255),
        "wall": (178, 178, 169, 255),
        "ceiling": (205, 207, 201, 255),
        "trim": (105, 72, 45, 255),
    }

    def add_box(name: str, extents: tuple[float, float, float], position: tuple[float, float, float], color: tuple[int, int, int, int]) -> None:
        mesh = trimesh.creation.box(extents=extents)
        mesh.apply_translation(position)
        mesh.visual = trimesh.visual.ColorVisuals(vertex_colors=np.tile(np.asarray(color, dtype=np.uint8), (len(mesh.vertices), 1)))
        scene.add_geometry(mesh, geom_name=name, node_name=name)

    wall_spans = {"front": width, "back": width, "left": depth, "right": depth}
    for kind, key in (("door", "doors"), ("window", "windows")):
        raw_openings = descriptor.get(key, [])
        if raw_openings is None:
            raw_openings = []
        if not isinstance(raw_openings, list):
            raise ValueError(f"{key} must be a list")
        for index, raw_opening in enumerate(raw_openings):
            if not isinstance(raw_opening, dict):
                raise ValueError(f"{key}[{index}] must be an object")
            wall = str(raw_opening.get("wall", "front")).strip().lower()
            if wall not in wall_spans:
                raise ValueError(f"{key}[{index}].wall must be front, back, left, or right")
            span = wall_spans[wall]
            opening_width = _finite(raw_opening.get("width", 1.0), f"{key}[{index}].width", positive=True)
            if opening_width >= span:
                raise ValueError(f"{key}[{index}].width must fit inside the wall")
            default_offset = (span - opening_width) * 0.5
            offset = _finite(raw_opening.get("offset", default_offset), f"{key}[{index}].offset", non_negative=True)
            if offset + opening_width > span + 1e-6:
                raise ValueError(f"{key}[{index}] extends beyond the wall span")
            opening_height = _finite(raw_opening.get("height", 2.1 if kind == "door" else min(1.2, height * 0.35)), f"{key}[{index}].height", positive=True)
            sill = 0.0 if kind == "door" else _finite(raw_opening.get("sill", height * 0.45), f"{key}[{index}].sill", non_negative=True)
            if sill + opening_height > height + 1e-6:
                raise ValueError(f"{key}[{index}] extends above the room height")
            opening = {
                "id": str(raw_opening.get("id") or f"{kind}-{index + 1}"),
                "kind": kind,
                "wall": wall,
                "offset": round(offset, 6),
                "width": round(opening_width, 6),
                "sill": round(sill, 6),
                "height": round(opening_height, 6),
                "top": round(sill + opening_height, 6),
            }
            openings_by_wall[wall].append(opening)
            records.append(opening)

    for wall, openings in openings_by_wall.items():
        openings.sort(key=lambda opening: (opening["offset"], opening["id"]))
        previous_end = 0.0
        for opening in openings:
            start = float(opening["offset"])
            end = start + float(opening["width"])
            if start < previous_end - 1e-6:
                raise ValueError(f"room openings overlap on {wall} wall")
            previous_end = end

    def add_wall_piece(wall: str, index: int, tangent_start: float, tangent_end: float, y_start: float, y_end: float) -> None:
        if tangent_end - tangent_start <= 1e-6 or y_end - y_start <= 1e-6:
            return
        span = wall_spans[wall]
        tangent_center = -span * 0.5 + (tangent_start + tangent_end) * 0.5
        y_center = (y_start + y_end) * 0.5
        if wall == "front":
            extents, position = (tangent_end - tangent_start, y_end - y_start, wall_thickness), (tangent_center, y_center, -depth * 0.5 + wall_thickness * 0.5)
        elif wall == "back":
            extents, position = (tangent_end - tangent_start, y_end - y_start, wall_thickness), (tangent_center, y_center, depth * 0.5 - wall_thickness * 0.5)
        elif wall == "left":
            extents, position = (wall_thickness, y_end - y_start, tangent_end - tangent_start), (-width * 0.5 + wall_thickness * 0.5, y_center, tangent_center)
        else:
            extents, position = (wall_thickness, y_end - y_start, tangent_end - tangent_start), (width * 0.5 - wall_thickness * 0.5, y_center, tangent_center)
        add_box(f"{wall}-wall-segment-{index}", extents, position, colors["wall"])

    for wall, openings in openings_by_wall.items():
        span = wall_spans[wall]
        cursor = 0.0
        piece_index = 1
        for opening in openings:
            start = float(opening["offset"])
            end = start + float(opening["width"])
            if start > cursor + 1e-6:
                add_wall_piece(wall, piece_index, cursor, start, 0.0, height)
                piece_index += 1
            sill = float(opening["sill"])
            top = float(opening["top"])
            if sill > 1e-6:
                add_wall_piece(wall, piece_index, start, end, 0.0, sill)
                piece_index += 1
            if top < height - 1e-6:
                add_wall_piece(wall, piece_index, start, end, top, height)
                piece_index += 1
            cursor = end
        if cursor < span - 1e-6:
            add_wall_piece(wall, piece_index, cursor, span, 0.0, height)

    if include_floor:
        add_box("floor", (width, floor_thickness, depth), (0.0, -floor_thickness * 0.5, 0.0), colors["floor"])
    if include_ceiling:
        add_box("ceiling", (width, ceiling_thickness, depth), (0.0, height + ceiling_thickness * 0.5, 0.0), colors["ceiling"])

    frame_size = min(0.1, wall_thickness * 0.6)

    def add_frame_box(name: str, wall: str, tangent_center: float, tangent_size: float, y_center: float, y_size: float) -> None:
        span = wall_spans[wall]
        if wall in {"front", "back"}:
            position = (tangent_center, y_center, -depth * 0.5 if wall == "front" else depth * 0.5)
            extents = (tangent_size, y_size, wall_thickness * 1.15)
        else:
            position = (-width * 0.5 if wall == "left" else width * 0.5, y_center, tangent_center)
            extents = (wall_thickness * 1.15, y_size, tangent_size)
        add_box(name, extents, position, colors["trim"])

    for opening in records:
        wall = str(opening["wall"])
        start = float(opening["offset"])
        end = start + float(opening["width"])
        sill = float(opening["sill"])
        top = float(opening["top"])
        span = wall_spans[wall]
        tangent_start = -span * 0.5 + start
        tangent_end = -span * 0.5 + end
        prefix = f"{opening['kind']}-{opening['id']}-{wall}"
        add_frame_box(f"{prefix}-left-jamb", wall, tangent_start, frame_size, (sill + top) * 0.5, top - sill)
        add_frame_box(f"{prefix}-right-jamb", wall, tangent_end, frame_size, (sill + top) * 0.5, top - sill)
        add_frame_box(f"{prefix}-top-lintel", wall, (tangent_start + tangent_end) * 0.5, float(opening["width"]) + frame_size * 2.0, top + frame_size * 0.5, frame_size)
        if opening["kind"] == "window":
            add_frame_box(f"{prefix}-sill", wall, (tangent_start + tangent_end) * 0.5, float(opening["width"]) + frame_size * 2.0, sill - frame_size * 0.5, frame_size)

    if not scene.geometry:
        raise ValueError("room descriptor produced no geometry")
    layout_hash = hashlib.sha256(json.dumps({"dimensions": [width, depth, height, wall_thickness, floor_thickness, ceiling_thickness], "includeFloor": include_floor, "includeCeiling": include_ceiling, "openings": records}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    report = {
        "schemaVersion": 1,
        "kind": "polykit.room-blockout",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {"width": round(width, 6), "depth": round(depth, 6), "height": round(height, 6), "wallThickness": round(wall_thickness, 6), "floorThickness": round(floor_thickness, 6), "ceilingThickness": round(ceiling_thickness, 6), "includeFloor": include_floor, "includeCeiling": include_ceiling},
        "summary": {"openingCount": len(records), "geometryCount": len(scene.geometry), "layoutHash": layout_hash, "bounds": [[round(float(value), 6) for value in row] for row in scene.bounds]},
        "openings": records,
        "reviewNotes": [
            "The room shell, floor, optional ceiling, and explicit door/window voids are server-owned GLB geometry suitable for composition and camera blocking.",
            "Openings are authored from normalized wall coordinates and do not infer architecture, trim, fixtures, materials, or hidden rooms from an image. Validate clearances and interior semantics downstream.",
        ],
    }
    return scene, report


def _room_blockout(descriptor: dict[str, Any], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Build and publish a deterministic room shell with explicit door and window openings."""
    scene, report = _build_room_blockout(descriptor, params)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = f"room_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    scene.export(output_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "room-blockout",
            "schema_version": 1,
            "status": "pass",
            "opening_count": report["summary"]["openingCount"],
            "geometry_count": report["summary"]["geometryCount"],
            "layout_hash": report["summary"]["layoutHash"],
            "report": report_path.name,
        },
    }

def _multi_room_blockout(descriptor: dict[str, Any], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Compose independently authored room shells into one deterministic scene."""
    import trimesh

    raw_rooms = descriptor.get("rooms")
    if not isinstance(raw_rooms, list) or not raw_rooms:
        raise ValueError("rooms must be a non-empty list")
    if len(raw_rooms) > 64:
        raise ValueError("rooms cannot contain more than 64 entries")
    default_keys = ("wallThickness", "floorThickness", "ceilingThickness", "includeFloor", "includeCeiling")
    global_defaults = {key: descriptor[key] for key in default_keys if key in descriptor}
    room_ids: set[str] = set()
    room_records: list[dict[str, Any]] = []
    combined = trimesh.Scene()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    def position_pair(value: Any, label: str) -> tuple[float, float]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"{label} must contain [x, z] world coordinates")
        return (_finite(value[0], f"{label}[0]"), _finite(value[1], f"{label}[1]"))

    for index, raw_room in enumerate(raw_rooms):
        if not isinstance(raw_room, dict):
            raise ValueError(f"rooms[{index}] must be an object")
        room_id = str(raw_room.get("id") or f"room-{index + 1}").strip()
        if not room_id or room_id in room_ids:
            raise ValueError(f"rooms[{index}] has an empty or duplicate id")
        room_ids.add(room_id)
        room_position = position_pair(raw_room.get("position", [0.0, 0.0]), f"room {room_id}.position")
        room_descriptor = dict(global_defaults)
        room_descriptor.update(raw_room)
        room_descriptor.pop("position", None)
        room_params = {
            "include_floor": params.get("include_floor", room_descriptor.get("includeFloor")),
            "include_ceiling": params.get("include_ceiling", room_descriptor.get("includeCeiling")),
        }
        child_scene, child_report = _build_room_blockout(room_descriptor, room_params)
        for name, geometry in child_scene.geometry.items():
            copied = geometry.copy()
            copied.apply_translation((room_position[0], 0.0, room_position[1]))
            combined.add_geometry(copied, geom_name=f"{room_id}-{name}", node_name=f"{room_id}-{name}")
        room_records.append({
            "id": room_id,
            "position": [round(room_position[0], 6), round(room_position[1], 6)],
            "dimensions": child_report["source"],
            "openingCount": child_report["summary"]["openingCount"],
            "layoutHash": child_report["summary"]["layoutHash"],
        })

    if not combined.geometry:
        raise ValueError("multi-room descriptor produced no geometry")
    token = f"multi_room_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    combined.export(output_path)
    layout_hash = hashlib.sha256(json.dumps(room_records, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    report = {
        "schemaVersion": 1,
        "kind": "polykit.multi-room-blockout",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {"roomCount": len(room_records), "roomIds": sorted(room_ids)},
        "summary": {"roomCount": len(room_records), "geometryCount": len(combined.geometry), "layoutHash": layout_hash, "bounds": [[round(float(value), 6) for value in row] for row in combined.bounds]},
        "rooms": room_records,
        "reviewNotes": [
            "Each room is generated by the validated room-blockout shell and translated to its explicit world position; source room and opening names are prefixed for composition.",
            "Adjacent shells are intentionally kept as separate meshes. This node does not infer floor plans, shared-wall booleans, corridors, navigation, or hidden rooms from an image.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {"evidence_kind": "multi-room-blockout", "schema_version": 1, "status": "pass", "room_count": len(room_records), "geometry_count": len(combined.geometry), "layout_hash": layout_hash, "report": report_path.name},
    }


def _vegetation_scatter(descriptor: dict[str, Any], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Build deterministic low-poly vegetation/rock instances for composition."""
    import numpy as np
    import trimesh

    size = _finite(descriptor.get("size", 24.0), "size", positive=True)
    seed_raw = descriptor.get("seed", 0)
    if isinstance(seed_raw, bool):
        raise ValueError("seed must be an integer")
    try:
        seed = int(seed_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("seed must be an integer") from exc
    try:
        requested_count = int(params.get("count", descriptor.get("count", 32)))
    except (TypeError, ValueError) as exc:
        raise ValueError("count must be an integer") from exc
    requested_count = max(1, min(500, requested_count))
    try:
        min_distance = _finite(params.get("min_distance", descriptor.get("minDistance", 0.8)), "minDistance", non_negative=True)
    except ValueError:
        raise
    raw_types = descriptor.get("types", ["tree"])
    if isinstance(raw_types, str):
        raw_types = [raw_types]
    if not isinstance(raw_types, list) or not raw_types:
        raise ValueError("types must be a non-empty list")
    types = [str(value).strip().lower() for value in raw_types]
    allowed = {"tree", "pine", "rock", "boulder", "grass", "cactus"}
    unknown = sorted(set(types).difference(allowed))
    if unknown:
        raise ValueError(f"unsupported vegetation type(s): {', '.join(unknown)}")
    base_elevation = _finite(descriptor.get("baseElevation", descriptor.get("base_elevation", 0.0)), "baseElevation")
    relief = _finite(descriptor.get("relief", 0.0), "relief")
    density_center = descriptor.get("avoidCenter")
    avoid_center: tuple[float, float] | None = None
    avoid_radius = 0.0
    if density_center is not None:
        avoid_center = _unit_pair(density_center, "avoidCenter")
        avoid_radius = _finite(descriptor.get("avoidRadius", 0.0), "avoidRadius", non_negative=True)
        if avoid_radius == 0.0:
            avoid_radius = 0.12

    scene = trimesh.Scene()
    records: list[dict[str, Any]] = []
    occupied: list[tuple[float, float]] = []
    colors = {
        "trunk": (93, 65, 43, 255),
        "foliage": (59, 119, 63, 255),
        "accent": (111, 153, 67, 255),
        "rock": (121, 119, 111, 255),
        "grass": (95, 148, 62, 255),
    }

    def add_mesh(name: str, mesh: Any, color: tuple[int, int, int, int], position: tuple[float, float, float], yaw: float, scale: float) -> None:
        mesh.apply_transform(trimesh.transformations.rotation_matrix(yaw, (0.0, 1.0, 0.0)))
        mesh.apply_scale(scale)
        mesh.apply_translation(position)
        mesh.visual = trimesh.visual.ColorVisuals(vertex_colors=np.tile(np.asarray(color, dtype=np.uint8), (len(mesh.vertices), 1)))
        scene.add_geometry(mesh, geom_name=name, node_name=name)

    def vertical(mesh: Any) -> Any:
        """Rotate trimesh's Z-axis primitives into the project's Y-up frame."""
        mesh.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2.0, (1.0, 0.0, 0.0)))
        return mesh

    def surface_y(x: float, z: float) -> float:
        if abs(relief) <= 1e-12:
            return base_elevation
        u, v = x / size + 0.5, z / size + 0.5
        return base_elevation + relief * (_value_noise(seed ^ 0xBEEF, u * 3.0, v * 3.0) * 0.5 + 0.5)

    def tree_parts(kind: str, random_seed: int) -> list[tuple[str, Any, tuple[int, int, int, int], tuple[float, float, float]]]:
        random = lambda salt: _hash_noise(random_seed ^ _stable_seed(str(salt)), salt, random_seed)
        if kind == "rock" or kind == "boulder":
            radius = 0.25 + random(1) * (0.25 if kind == "boulder" else 0.14)
            mesh = trimesh.creation.icosphere(subdivisions=1 if kind == "boulder" else 0, radius=radius)
            mesh.apply_scale((1.0, 0.55 + random(2) * 0.25, 0.85 + random(3) * 0.25))
            return [("rock", mesh, colors["rock"], (0.0, radius * 0.42, 0.0))]
        if kind == "grass":
            parts: list[tuple[str, Any, tuple[int, int, int, int], tuple[float, float, float]]] = []
            for blade in range(5 + int(random(4) * 4)):
                height = 0.16 + random(10 + blade) * 0.16
                mesh = vertical(trimesh.creation.cone(0.018, height, sections=3))
                angle = random(30 + blade) * math.tau
                distance = random(40 + blade) * 0.08
                parts.append((f"blade-{blade + 1}", mesh, colors["grass"], (math.cos(angle) * distance, height * 0.5, math.sin(angle) * distance)))
            return parts
        if kind == "cactus":
            body_height = 0.55 + random(1) * 0.32
            body = vertical(trimesh.creation.capsule(radius=0.09, height=body_height, count=[2, 8]))
            parts = [("body", body, colors["foliage"], (0.0, body_height * 0.5 + 0.09, 0.0))]
            for arm in range(int(random(2) * 3)):
                side = 1.0 if random(3) > 0.5 else -1.0
                arm_height = 0.2 + random(4 + arm) * 0.16
                mesh = vertical(trimesh.creation.capsule(radius=0.055, height=arm_height, count=[2, 6]))
                mesh.apply_transform(trimesh.transformations.rotation_matrix(side * -0.2, (0.0, 0.0, 1.0)))
                parts.append((f"arm-{arm + 1}", mesh, colors["accent"], (side * 0.14, body_height * (0.35 + random(5 + arm) * 0.25), 0.0)))
            return parts
        if kind == "pine":
            trunk_height = 0.25 + random(1) * 0.14
            parts = [("trunk", vertical(trimesh.creation.cylinder(radius=0.035, height=trunk_height, sections=6)), colors["trunk"], (0.0, trunk_height * 0.5, 0.0))]
            y, radius = trunk_height * 0.85, 0.28 + random(2) * 0.1
            for tier in range(3 + int(random(3) * 2)):
                height = 0.22 + random(10 + tier) * 0.12
                parts.append((f"tier-{tier + 1}", vertical(trimesh.creation.cone(radius, height, sections=6)), colors["foliage" if tier % 2 else "accent"], (0.0, y + height * 0.5, 0.0)))
                y += height * 0.58
                radius *= 0.72
            return parts
        trunk_height = 0.34 + random(1) * 0.12
        parts = [("trunk", vertical(trimesh.creation.cylinder(radius=0.045, height=trunk_height, sections=6)), colors["trunk"], (0.0, trunk_height * 0.5, 0.0))]
        for blob in range(2 + int(random(2) * 2)):
            radius = (0.24 + random(10 + blob) * 0.1) * (1.0 - blob * 0.16)
            parts.append((f"crown-{blob + 1}", trimesh.creation.icosphere(subdivisions=0, radius=radius), colors["foliage" if blob % 2 else "accent"], ((random(20 + blob) * 2.0 - 1.0) * 0.1, trunk_height + radius * 0.6 + blob * radius * 0.75, (random(30 + blob) * 2.0 - 1.0) * 0.1)))
        return parts

    attempts = 0
    while len(records) < requested_count and attempts < requested_count * 32:
        candidate_seed = seed ^ _stable_seed(f"vegetation:{attempts}")
        x = (_hash_noise(candidate_seed, attempts, 1) - 0.5) * size * 0.94
        z = (_hash_noise(candidate_seed ^ 0x1234, 1, attempts) - 0.5) * size * 0.94
        attempts += 1
        if avoid_center is not None and math.hypot(x / size + 0.5 - avoid_center[0], z / size + 0.5 - avoid_center[1]) < avoid_radius:
            continue
        if any(math.hypot(x - other_x, z - other_z) < min_distance for other_x, other_z in occupied):
            continue
        occupied.append((x, z))
        kind = types[len(records) % len(types)]
        scale = 0.72 + _hash_noise(candidate_seed ^ 0x5678, attempts, 2) * 0.56
        y = surface_y(x, z)
        parts = tree_parts(kind, candidate_seed)
        part_names: list[str] = []
        for part_name, mesh, color, local_position in parts:
            name = f"{kind}-{len(records) + 1}-{part_name}"
            add_mesh(name, mesh, color, (x + local_position[0] * scale, y + local_position[1] * scale, z + local_position[2] * scale), _hash_noise(candidate_seed ^ 0x9999, 3, attempts) * math.tau, scale)
            part_names.append(name)
        records.append({"id": f"{kind}-{len(records) + 1}", "kind": kind, "position": [round(x, 6), round(y, 6), round(z, 6)], "scale": round(scale, 6), "parts": part_names})

    if not records:
        raise ValueError("vegetation descriptor produced no instances; reduce avoidRadius or minDistance")
    include_ground = params.get("include_ground", descriptor.get("includeGround", False))
    if isinstance(include_ground, str):
        include_ground = include_ground.strip().lower() not in {"", "0", "false", "no", "off"}
    if bool(include_ground):
        add_box = trimesh.creation.box(extents=(size, 0.04, size))
        add_box.apply_translation((0.0, base_elevation - 0.02, 0.0))
        add_box.visual = trimesh.visual.ColorVisuals(vertex_colors=np.tile(np.asarray((78, 104, 61, 255), dtype=np.uint8), (len(add_box.vertices), 1)))
        scene.add_geometry(add_box, geom_name="ground", node_name="ground")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = f"vegetation_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    scene.export(output_path)
    layout_hash = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    report = {
        "schemaVersion": 1,
        "kind": "polykit.vegetation-scatter",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {"seed": seed, "size": round(size, 6), "requestedCount": requested_count, "placedCount": len(records), "minDistance": round(min_distance, 6), "relief": round(relief, 6)},
        "summary": {"instanceCount": len(records), "geometryCount": len(scene.geometry), "layoutHash": layout_hash, "bounds": [[round(float(value), 6) for value in row] for row in scene.bounds]},
        "instances": records,
        "reviewNotes": [
            "Instances are low-poly production geometry with stable names and placement records; they are suitable for composition over terrain-mesh.",
            "This node does not infer botanical species, collision, seasonal variation, or image-grounded vegetation semantics. Validate density and material identity in a scene review.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {"evidence_kind": "vegetation-scatter", "schema_version": 1, "status": "pass", "instance_count": len(records), "geometry_count": len(scene.geometry), "layout_hash": layout_hash, "report": report_path.name},
    }


def main() -> None:
    try:
        payload = json.loads(sys.stdin.readline())
        input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        node_id = str(params.get("_node_id") or "terrain-mesh")
        if node_id not in {"terrain-mesh", "city-blockout", "vegetation-scatter", "room-blockout", "multi-room-blockout"}:
            error(f"environment-production: unsupported node '{node_id}'")
            return
        text = input_data.get("text")
        if not isinstance(text, str) or not text.strip():
            error("environment-production: the selected node requires a JSON descriptor on the text input")
            return
        descriptor = json.loads(text)
        if not isinstance(descriptor, dict):
            raise ValueError("terrain descriptor must be a JSON object")
        workspace_raw = payload.get("workspaceDir")
        if not isinstance(workspace_raw, str) or not workspace_raw.strip():
            raise ValueError("workspaceDir is required")
        progress(5, "Reading terrain specification…")
        if node_id == "terrain-mesh":
            result = _terrain_mesh(descriptor, Path(workspace_raw), params)
            progress(90, "Writing terrain and water meshes…")
            progress(100, "Terrain mesh ready")
        elif node_id == "city-blockout":
            result = _city_blockout(descriptor, Path(workspace_raw), params)
            progress(90, "Writing roads and building masses…")
            progress(100, "City blockout ready")
        elif node_id == "room-blockout":
            result = _room_blockout(descriptor, Path(workspace_raw), params)
            progress(90, "Writing room shell and openings…")
            progress(100, "Room blockout ready")
        elif node_id == "multi-room-blockout":
            result = _multi_room_blockout(descriptor, Path(workspace_raw), params)
            progress(90, "Composing room shells…")
            progress(100, "Multi-room blockout ready")
        else:
            result = _vegetation_scatter(descriptor, Path(workspace_raw), params)
            progress(90, "Writing vegetation instances…")
            progress(100, "Vegetation scatter ready")
        emit({"type": "done", "result": result})
    except json.JSONDecodeError as exc:
        error(f"environment-production: invalid terrain JSON ({exc.msg})")
    except Exception as exc:
        error(f"environment-production: {exc}")


if __name__ == "__main__":
    main()
