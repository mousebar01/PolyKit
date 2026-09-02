"""Deterministic Terrain Compiler v2 shared by production terrain export.

The math in this module intentionally mirrors src/areas/worlds/runtime/terrain.ts,
noise.ts, and rng.ts so browser planning and server-owned GLB production sample
the same world for terrainVersion=2 descriptors.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

_MASK32 = 0xFFFFFFFF
_DEFAULT_SURFACE_COLORS: dict[str, tuple[int, int, int]] = {
    "mountain": (122, 124, 126),
    "hills": (111, 130, 83),
    "plains": (126, 146, 91),
    "desert": (194, 166, 111),
    "dunes": (207, 181, 126),
    "water": (61, 100, 132),
    "canyon": (151, 103, 72),
    "volcanic": (79, 69, 65),
    "snow": (218, 226, 229),
    "forest": (66, 98, 60),
    "swamp": (74, 91, 63),
    "beach": (194, 180, 139),
    "mesa": (157, 111, 78),
}
_BACKGROUND_COLOR = np.asarray((88.0, 99.0, 76.0), dtype=np.float64)


def _u32(value: int) -> int:
    return int(value) & _MASK32


def _imul(left: int, right: int) -> int:
    return _u32(_u32(left) * _u32(right))


def hash_string(value: str) -> int:
    """FNV-1a with the same 32-bit multiplication semantics as rng.ts."""
    result = 2166136261
    for char in value:
        result ^= ord(char) & 0xFFFF
        result = _imul(result, 16777619)
    return _u32(result)


class Mulberry32:
    def __init__(self, seed: int) -> None:
        self.state = _u32(seed)

    def __call__(self) -> float:
        self.state = _u32(self.state + 0x6D2B79F5)
        value = _imul(self.state ^ (self.state >> 15), 1 | self.state)
        mixed = _u32(value + _imul(value ^ (value >> 7), 61 | value))
        value = _u32(mixed ^ value)
        value = _u32(value ^ (value >> 14))
        return value / 4294967296.0


def clamp(value: float, minimum: float, maximum: float) -> float:
    return minimum if value < minimum else maximum if value > maximum else value


def lerp(first: float, second: float, factor: float) -> float:
    return first + (second - first) * factor


def fade(value: float) -> float:
    return value * value * value * (value * (value * 6.0 - 15.0) + 10.0)


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 0.0 if value < edge0 else 1.0
    factor = clamp((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return factor * factor * (3.0 - 2.0 * factor)


def _js_round_non_negative(value: float) -> int:
    return math.floor(value + 0.5)


class Noise2D:
    def __init__(self, seed: int) -> None:
        random = Mulberry32(seed)
        base = list(range(256))
        for index in range(255, 0, -1):
            swap_index = math.floor(random() * (index + 1))
            base[index], base[swap_index] = base[swap_index], base[index]
        self.permutation = np.empty(512, dtype=np.uint8)
        for index in range(512):
            self.permutation[index] = base[index & 255]
        self.gradient_x = np.empty(256, dtype=np.float32)
        self.gradient_y = np.empty(256, dtype=np.float32)
        for index in range(256):
            angle = index / 256.0 * math.pi * 2.0
            self.gradient_x[index] = math.cos(angle)
            self.gradient_y[index] = math.sin(angle)

    def sample(self, x: float, y: float) -> float:
        xi = math.floor(x)
        yi = math.floor(y)
        xf = x - xi
        yf = y - yi
        u = fade(xf)
        v = fade(yf)
        permutation = self.permutation
        aa = int(permutation[(int(permutation[xi & 255]) + yi) & 255])
        ab = int(permutation[(int(permutation[xi & 255]) + yi + 1) & 255])
        ba = int(permutation[(int(permutation[(xi + 1) & 255]) + yi) & 255])
        bb = int(permutation[(int(permutation[(xi + 1) & 255]) + yi + 1) & 255])

        def dot(hash_value: int, dx: float, dy: float) -> float:
            return float(self.gradient_x[hash_value]) * dx + float(self.gradient_y[hash_value]) * dy

        x0 = lerp(dot(aa, xf, yf), dot(ba, xf - 1.0, yf), u)
        x1 = lerp(dot(ab, xf, yf - 1.0), dot(bb, xf - 1.0, yf - 1.0), u)
        return lerp(x0, x1, v) * 1.41421356

    def fbm(self, x: float, y: float, octaves: int = 5, lacunarity: float = 2.0, gain: float = 0.5) -> float:
        amplitude = 1.0
        frequency = 1.0
        total = 0.0
        normalization = 0.0
        for _ in range(max(0, octaves)):
            total += amplitude * self.sample(x * frequency, y * frequency)
            normalization += amplitude
            amplitude *= gain
            frequency *= lacunarity
        return total / normalization if normalization > 0.0 else 0.0

    def ridged(self, x: float, y: float, octaves: int = 5, lacunarity: float = 2.1, gain: float = 0.5) -> float:
        amplitude = 0.5
        frequency = 1.0
        total = 0.0
        previous = 1.0
        for _ in range(max(0, octaves)):
            ridge = 1.0 - abs(self.sample(x * frequency, y * frequency))
            sharp = ridge * ridge
            total += sharp * amplitude * previous
            previous = sharp
            amplitude *= gain
            frequency *= lacunarity
        return total

    def billow(self, x: float, y: float, octaves: int = 4, lacunarity: float = 2.0, gain: float = 0.5) -> float:
        amplitude = 1.0
        frequency = 1.0
        total = 0.0
        normalization = 0.0
        for _ in range(max(0, octaves)):
            total += amplitude * abs(self.sample(x * frequency, y * frequency))
            normalization += amplitude
            amplitude *= gain
            frequency *= lacunarity
        return total / normalization if normalization > 0.0 else 0.0

    def warp(self, x: float, y: float, strength: float) -> tuple[float, float]:
        offset_x = self.fbm(x + 5.2, y + 1.3, 4)
        offset_y = self.fbm(x - 3.7, y + 9.2, 4)
        return x + offset_x * strength, y + offset_y * strength


@dataclass(frozen=True)
class TerrainRegion:
    id: str
    kind: str
    center: tuple[float, float]
    radius: float
    irregularity: float
    base_elevation: float
    amplitude: float
    roughness: float
    terraces: int | None
    color: tuple[int, int, int]


@dataclass(frozen=True)
class TerrainRiver:
    id: str
    path: tuple[tuple[float, float], ...]
    width: float
    depth: float


@dataclass(frozen=True)
class TerrainProgram:
    seed: int
    size: float
    sea_level: float
    resolution: int
    regions: tuple[TerrainRegion, ...]
    rivers: tuple[TerrainRiver, ...]


@dataclass
class TerrainFields:
    program: TerrainProgram
    heights: np.ndarray
    region_weights: list[np.ndarray]
    dominant: np.ndarray
    river_distance: np.ndarray | None

    @property
    def min_height(self) -> float:
        return float(self.heights.min())

    @property
    def max_height(self) -> float:
        return float(self.heights.max())


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


def _parse_color(value: Any, kind: str) -> tuple[int, int, int]:
    fallback = _DEFAULT_SURFACE_COLORS.get(kind, (120, 128, 110))
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if len(text) == 4 and text.startswith("#"):
        text = "#" + "".join(char * 2 for char in text[1:])
    if len(text) != 7 or not text.startswith("#"):
        return fallback
    try:
        return tuple(int(text[index:index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]
    except ValueError:
        return fallback


def parse_program(descriptor: dict[str, Any], *, resolution: int) -> TerrainProgram:
    size = _finite(descriptor.get("size", 12.0), "size", positive=True)
    seed_raw = descriptor.get("seed", 0)
    if isinstance(seed_raw, bool):
        raise ValueError("seed must be an integer")
    try:
        seed = int(seed_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("seed must be an integer") from exc
    sea_level = _finite(descriptor.get("seaLevel", descriptor.get("sea_level", 0.0)), "seaLevel")
    resolution = max(2, min(1024, int(resolution)))

    regions_raw = descriptor.get("regions", [])
    if not isinstance(regions_raw, list):
        raise ValueError("regions must be a list")
    regions: list[TerrainRegion] = []
    seen_region_ids: set[str] = set()
    for index, raw_region in enumerate(regions_raw):
        if not isinstance(raw_region, dict):
            raise ValueError(f"regions[{index}] must be an object")
        region_id = str(raw_region.get("id") or f"region-{index + 1}").strip()
        if not region_id or region_id in seen_region_ids:
            raise ValueError(f"regions[{index}] has an empty or duplicate id")
        seen_region_ids.add(region_id)
        kind = str(raw_region.get("kind") or "plains").strip().lower()
        material = raw_region.get("material") if isinstance(raw_region.get("material"), dict) else {}
        terraces_raw = raw_region.get("terraces")
        terraces = None if terraces_raw is None else max(1, int(terraces_raw))
        regions.append(TerrainRegion(
            id=region_id,
            kind=kind,
            center=_unit_pair(raw_region.get("center", [0.5, 0.5]), f"region {region_id}.center"),
            radius=_finite(raw_region.get("radius", 0.35), f"region {region_id}.radius", positive=True),
            irregularity=clamp(_finite(raw_region.get("irregularity", 0.0), f"region {region_id}.irregularity"), 0.0, 1.0),
            base_elevation=_finite(raw_region.get("baseElevation", raw_region.get("base_elevation", 0.0)), f"region {region_id}.baseElevation"),
            amplitude=_finite(raw_region.get("amplitude", 1.0), f"region {region_id}.amplitude"),
            roughness=clamp(_finite(raw_region.get("roughness", 0.5), f"region {region_id}.roughness", non_negative=True), 0.0, 1.0),
            terraces=terraces,
            color=_parse_color(material.get("color"), kind),
        ))

    rivers_raw = descriptor.get("rivers", [])
    if not isinstance(rivers_raw, list):
        raise ValueError("rivers must be a list")
    rivers: list[TerrainRiver] = []
    for index, raw_river in enumerate(rivers_raw):
        if not isinstance(raw_river, dict):
            raise ValueError(f"rivers[{index}] must be an object")
        raw_path = raw_river.get("path")
        if not isinstance(raw_path, list) or len(raw_path) < 2:
            raise ValueError(f"river {index + 1} path must contain at least two points")
        rivers.append(TerrainRiver(
            id=str(raw_river.get("id") or f"river-{index + 1}"),
            path=tuple(_unit_pair(point, f"river {index + 1}.path[{point_index}]") for point_index, point in enumerate(raw_path)),
            width=_finite(raw_river.get("width", 0.25), f"river {index + 1}.width", positive=True),
            depth=_finite(raw_river.get("depth", 0.6), f"river {index + 1}.depth", non_negative=True),
        ))
    return TerrainProgram(seed=seed, size=size, sea_level=sea_level, resolution=resolution, regions=tuple(regions), rivers=tuple(rivers))


def _region_elevation(region: TerrainRegion, terrain_noise: Noise2D, detail_noise: Noise2D, u: float, v: float, normalized_distance: float) -> float:
    frequency = 3.0 / max(region.radius, 0.03)
    nx = u * frequency
    ny = v * frequency
    octaves = 4 + _js_round_non_negative(region.roughness * 3.0)
    gain = 0.42 + region.roughness * 0.22
    kind = region.kind

    if kind in {"mountain", "snow"}:
        ridge = terrain_noise.ridged(nx * 0.9, ny * 0.9, octaves, 2.15, gain)
        elevation = math.pow(ridge, 1.35) * region.amplitude * (0.75 if kind == "snow" else 1.0)
    elif kind == "volcanic":
        cone = math.pow(1.0 - normalized_distance, 1.6) * region.amplitude
        crater = smoothstep(0.16, 0.02, normalized_distance) * region.amplitude * 0.45
        crags = terrain_noise.ridged(nx, ny, octaves, 2.2, gain) * region.amplitude * 0.18
        elevation = cone - crater + crags
    elif kind in {"canyon", "mesa"}:
        base = terrain_noise.fbm(nx * 0.5, ny * 0.5, 4) * 0.5 + 0.5
        terrace_count = max(1, region.terraces or (5 if kind == "canyon" else 3))
        quantized = base * terrace_count
        step = math.floor(quantized)
        fraction = quantized - step
        elevation = (step + smoothstep(0.35, 0.65, fraction)) / terrace_count * region.amplitude
    elif kind in {"dunes", "desert"}:
        dune = terrain_noise.billow(nx * 0.5 + ny * 0.22, ny * 1.4, 4, 2.0, 0.5)
        flat = terrain_noise.fbm(nx * 0.4, ny * 0.4, 3) * 0.2
        elevation = (dune if kind == "dunes" else dune * 0.45 + flat) * region.amplitude
    elif kind in {"hills", "forest"}:
        rolling = terrain_noise.billow(nx * 0.7, ny * 0.7, 4, 2.0, 0.5)
        elevation = rolling * region.amplitude * (0.8 if kind == "forest" else 1.0)
    elif kind == "water":
        elevation = -math.pow(1.0 - normalized_distance, 1.4) * region.amplitude
    elif kind == "swamp":
        pools = terrain_noise.billow(nx * 1.2, ny * 1.2, 3)
        elevation = (0.15 - pools * 0.5) * region.amplitude * 0.4
    elif kind == "beach":
        elevation = terrain_noise.fbm(nx * 0.5, ny * 0.5, 3) * region.amplitude * 0.2
    else:
        elevation = (terrain_noise.fbm(nx * 0.6, ny * 0.6, 4) * 0.5 + 0.1) * region.amplitude * 0.5

    elevation += detail_noise.fbm(nx * 4.0, ny * 4.0, 3) * region.amplitude * 0.05 * (0.4 + region.roughness)
    if region.terraces and kind not in {"canyon", "mesa"}:
        step_height = region.amplitude / max(1, region.terraces)
        if abs(step_height) > 1e-12:
            quantized = elevation / step_height
            step = math.floor(quantized)
            elevation = (step + smoothstep(0.3, 0.7, quantized - step)) * step_height
    return region.base_elevation + elevation


def compile_fields(program: TerrainProgram) -> TerrainFields:
    res = program.resolution
    heights = np.empty(res * res, dtype=np.float32)
    dominant = np.full(res * res, -1, dtype=np.int16)
    region_weights = [np.zeros(res * res, dtype=np.float32) for _ in program.regions]
    warp_noise = Noise2D(program.seed ^ 0x5EED)
    background_noise = Noise2D(program.seed ^ 0xBA5E)
    fields = [
        (region, Noise2D(_u32(program.seed ^ hash_string(region.id))), Noise2D(_u32(program.seed ^ hash_string(f"{region.id}:detail"))))
        for region in program.regions
    ]

    for row in range(res):
        v = row / (res - 1)
        for column in range(res):
            u = column / (res - 1)
            index = row * res + column
            total_weight = 0.0
            height = 0.0
            best_region = -1
            best_weight = 0.0
            for region_index, (region, terrain_noise, detail_noise) in enumerate(fields):
                warp_strength = clamp(region.irregularity, 0.0, 1.0) * max(0.0, region.radius) * 0.9
                query_x = u * 3.0 + region_index * 7.31
                query_y = v * 3.0 - region_index * 4.17
                warped_x, warped_y = warp_noise.warp(query_x, query_y, 1.0)
                du = u - region.center[0] + (warped_x - query_x) * warp_strength * 0.33
                dv = v - region.center[1] + (warped_y - query_y) * warp_strength * 0.33
                normalized_distance = math.hypot(du, dv) / max(region.radius, 1e-4)
                weight = 1.0 - smoothstep(0.55, 1.15, normalized_distance)
                if weight <= 0.001:
                    continue
                region_weights[region_index][index] = weight
                total_weight += weight
                height += _region_elevation(region, terrain_noise, detail_noise, u, v, clamp(normalized_distance, 0.0, 1.0)) * weight
                if weight > best_weight:
                    best_weight = weight
                    best_region = region_index

            background_weight = max(0.0, 1.0 - total_weight)
            if background_weight > 0.0:
                background = (background_noise.fbm(u * 4.0, v * 4.0, 4) * 0.5 + 0.15) * 8.0
                height += background * background_weight
                total_weight += background_weight
            micro = background_noise.fbm(u * 34.0 + 11.3, v * 34.0 - 5.7, 3) * 1.1 + background_noise.fbm(u * 90.0 - 2.1, v * 90.0 + 8.4, 2) * 0.3
            heights[index] = height / max(total_weight, 1e-6) + micro
            if best_weight > 0.25:
                dominant[index] = best_region

    for index in range(len(heights)):
        total = sum(float(weights[index]) for weights in region_weights)
        if total > 1.0:
            for weights in region_weights:
                weights[index] = float(weights[index]) / total

    for row in range(res):
        v = row / (res - 1)
        for column in range(res):
            u = column / (res - 1)
            edge_distance = min(u, v, 1.0 - u, 1.0 - v)
            falloff = smoothstep(0.0, 0.08, edge_distance)
            index = row * res + column
            heights[index] = lerp(program.sea_level - 9.0, float(heights[index]), falloff)

    river_distance = _carve_rivers(program.rivers, heights, res, program.size, program.sea_level, warp_noise)
    return TerrainFields(program=program, heights=heights, region_weights=region_weights, dominant=dominant, river_distance=river_distance)


def _carve_rivers(rivers: tuple[TerrainRiver, ...], heights: np.ndarray, res: int, size: float, sea_level: float, meander: Noise2D) -> np.ndarray | None:
    if not rivers:
        return None
    distances = np.full(res * res, np.inf, dtype=np.float32)
    for river in rivers:
        if len(river.path) < 2:
            continue
        points: list[tuple[float, float]] = []
        for segment in range(len(river.path) - 1):
            u0, v0 = river.path[segment]
            u1, v1 = river.path[segment + 1]
            segment_length = math.hypot(u1 - u0, v1 - v0)
            steps = max(2, math.ceil(segment_length * res * 1.5))
            for step in range(steps + 1):
                fraction = step / steps
                u = u0 + (u1 - u0) * fraction
                v = v0 + (v1 - v0) * fraction
                wobble = meander.fbm(u * 6.0 + 11.7, v * 6.0 - 3.2, 3) * 0.02 * math.sin(fraction * math.pi)
                perpendicular_u = -(v1 - v0) / max(segment_length, 1e-6)
                perpendicular_v = (u1 - u0) / max(segment_length, 1e-6)
                points.append((u + perpendicular_u * wobble, v + perpendicular_v * wobble))
        influence = max(0.0, river.width) * 2.5
        cells_influence = math.ceil(influence / size * (res - 1)) + 1
        for point_u, point_v in points:
            center_i = _js_round_non_negative(point_u * (res - 1))
            center_j = _js_round_non_negative(point_v * (res - 1))
            for offset_j in range(-cells_influence, cells_influence + 1):
                row = center_j + offset_j
                if row < 0 or row >= res:
                    continue
                for offset_i in range(-cells_influence, cells_influence + 1):
                    column = center_i + offset_i
                    if column < 0 or column >= res:
                        continue
                    distance = math.hypot(offset_i, offset_j) / (res - 1) * size
                    index = row * res + column
                    distances[index] = min(float(distances[index]), distance)
        floor_height = sea_level - max(0.0, river.depth)
        for index in range(len(heights)):
            distance = float(distances[index])
            if distance > influence:
                continue
            center_weight = 1.0 - smoothstep(max(0.0, river.width) * 0.5, influence, distance)
            current = float(heights[index])
            heights[index] = min(current, lerp(current, floor_height, center_weight))
    return distances


def surface_colors(fields: TerrainFields) -> np.ndarray:
    colors = np.empty((len(fields.heights), 4), dtype=np.uint8)
    region_colors = [np.asarray(region.color, dtype=np.float64) for region in fields.program.regions]
    for index in range(len(fields.heights)):
        total = sum(float(weights[index]) for weights in fields.region_weights)
        rgb = _BACKGROUND_COLOR * max(0.0, 1.0 - total)
        for weights, region_color in zip(fields.region_weights, region_colors):
            rgb += region_color * float(weights[index])
        if total <= 1e-9:
            rgb = _BACKGROUND_COLOR.copy()
        colors[index, :3] = np.rint(np.clip(rgb, 0.0, 255.0)).astype(np.uint8)
        colors[index, 3] = 255
    return colors


def grid_sample(fields: TerrainFields, column: int, row: int) -> dict[str, Any]:
    res = fields.program.resolution
    if not (0 <= column < res and 0 <= row < res):
        raise ValueError("grid sample is outside the terrain")
    index = row * res + column
    return {
        "height": float(fields.heights[index]),
        "weights": [float(weights[index]) for weights in fields.region_weights],
        "dominant": int(fields.dominant[index]),
    }
