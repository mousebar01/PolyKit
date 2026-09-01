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


def main() -> None:
    try:
        payload = json.loads(sys.stdin.readline())
        input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        node_id = str(params.get("_node_id") or "terrain-mesh")
        if node_id != "terrain-mesh":
            error(f"environment-production: unsupported node '{node_id}'")
            return
        text = input_data.get("text")
        if not isinstance(text, str) or not text.strip():
            error("environment-production: terrain-mesh requires a JSON descriptor on the text input")
            return
        descriptor = json.loads(text)
        if not isinstance(descriptor, dict):
            raise ValueError("terrain descriptor must be a JSON object")
        workspace_raw = payload.get("workspaceDir")
        if not isinstance(workspace_raw, str) or not workspace_raw.strip():
            raise ValueError("workspaceDir is required")
        progress(5, "Reading terrain specification…")
        result = _terrain_mesh(descriptor, Path(workspace_raw), params)
        progress(90, "Writing terrain and water meshes…")
        progress(100, "Terrain mesh ready")
        emit({"type": "done", "result": result})
    except json.JSONDecodeError as exc:
        error(f"environment-production: invalid terrain JSON ({exc.msg})")
    except Exception as exc:
        error(f"environment-production: {exc}")


if __name__ == "__main__":
    main()
