from __future__ import annotations

import math
from dataclasses import dataclass

try:
    import bpy  # type: ignore
except Exception:  # pragma: no cover
    bpy = None

from experiments.worldclaw_terrain.noise import fbm, ridged_fbm

from .config import GrasslandConfig


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge0 == edge1:
        return 0.0 if x < edge0 else 1.0
    t = _clamp01((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _gaussian(x: float, y: float, cx: float, cy: float, sx: float, sy: float) -> float:
    dx = (x - cx) / sx
    dy = (y - cy) / sy
    return math.exp(-(dx * dx + dy * dy))


def sample_height(x: float, y: float, *, size: float, seed: int) -> float:
    """Game-oriented rolling terrain with broad forms first, noise second."""
    nx = x / size
    ny = y / size

    broad = 16.0 * fbm(x, y, scale=310.0, octaves=4, lacunarity=2.0, gain=0.48, seed=seed)
    rolling = 6.5 * fbm(x + 91.0, y - 43.0, scale=120.0, octaves=3, lacunarity=2.0, gain=0.50, seed=seed + 11)

    # Large readable landmarks: one western hill chain and a distant northern ridge.
    west_hill = 48.0 * _gaussian(x, y, -0.27 * size, 0.06 * size, 0.25 * size, 0.19 * size)
    north_ridge = 38.0 * _gaussian(x, y, 0.12 * size, 0.38 * size, 0.44 * size, 0.13 * size)
    north_ridge += 10.0 * ridged_fbm(x - 40.0, y + 25.0, scale=205.0, octaves=4, seed=seed + 29)

    # Wide central lowland creates a natural traversal corridor.
    valley_center = 0.05 * size * math.sin((y / size) * math.pi * 1.8 + 0.5)
    valley_distance = abs(x - valley_center)
    valley = -24.0 * math.exp(-((valley_distance / (0.15 * size)) ** 2))

    # Flatten a broad playable meadow around the south-center without making it artificial.
    meadow = _gaussian(x, y, 0.02 * size, -0.24 * size, 0.33 * size, 0.20 * size)
    detailed = broad + rolling + west_hill + north_ridge + valley
    meadow_target = 5.0 + 3.0 * fbm(x, y, scale=180.0, octaves=2, seed=seed + 57)
    height = detailed * (1.0 - 0.48 * meadow) + meadow_target * (0.48 * meadow)
    return height


@dataclass(slots=True)
class TerrainBuild:
    object: object
    min_height: float
    max_height: float


def build_terrain(config: GrasslandConfig, *, name: str = "GrasslandTerrain") -> TerrainBuild:
    if bpy is None:
        raise RuntimeError("build_terrain must run inside Blender")
    if config.resolution < 3:
        raise ValueError("resolution must be >= 3")

    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)

    n = int(config.resolution)
    half = config.size * 0.5
    step = config.size / (n - 1)

    heights: list[float] = [0.0] * (n * n)
    vertices: list[tuple[float, float, float]] = []
    for iy in range(n):
        y = -half + iy * step
        for ix in range(n):
            x = -half + ix * step
            h = sample_height(x, y, size=config.size, seed=config.seed)
            heights[iy * n + ix] = h
            vertices.append((x, y, h))

    faces: list[tuple[int, int, int, int]] = []
    for iy in range(n - 1):
        row = iy * n
        next_row = (iy + 1) * n
        for ix in range(n - 1):
            a = row + ix
            b = a + 1
            d = next_row + ix
            c = d + 1
            faces.append((a, b, c, d))

    mesh = bpy.data.meshes.new(f"{name}Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    min_h = min(heights)
    max_h = max(heights)
    h_span = max(max_h - min_h, 1e-6)

    grass_values: list[float] = []
    rock_values: list[float] = []
    valley_values: list[float] = []
    color_values: list[tuple[float, float, float, float]] = []

    def H(ix: int, iy: int) -> float:
        return heights[max(0, min(n - 1, iy)) * n + max(0, min(n - 1, ix))]

    for iy in range(n):
        y = -half + iy * step
        for ix in range(n):
            x = -half + ix * step
            h = H(ix, iy)
            dx = (H(ix + 1, iy) - H(ix - 1, iy)) / (2.0 * step)
            dy = (H(ix, iy + 1) - H(ix, iy - 1)) / (2.0 * step)
            slope = math.atan(math.sqrt(dx * dx + dy * dy)) / (math.pi * 0.5)

            height01 = _clamp01((h - min_h) / h_span)
            grass = 1.0 - _smoothstep(config.max_grass_slope * 0.72, config.max_grass_slope, slope)
            high_fade = 1.0 - _smoothstep(0.82, 0.97, height01)
            valley_center = 0.05 * config.size * math.sin((y / config.size) * math.pi * 1.8 + 0.5)
            valley = math.exp(-((abs(x - valley_center) / (0.17 * config.size)) ** 2))
            patch = 0.78 + 0.22 * (0.5 + 0.5 * fbm(x + 33.0, y - 10.0, scale=80.0, octaves=2, seed=config.seed + 101))
            grass = _clamp01(grass * high_fade * patch + valley * config.valley_green_strength)
            rock = _clamp01(_smoothstep(config.rock_slope_start, 0.82, slope) + _smoothstep(0.86, 1.0, height01) * 0.25)

            grass_values.append(grass)
            rock_values.append(rock)
            valley_values.append(valley)

            # Broad clean colors: meadow/grass, dry upland, exposed rock.
            lush = (0.18, 0.38, 0.08)
            dry = (0.33, 0.39, 0.13)
            stone = (0.29, 0.28, 0.23)
            t = _clamp01(height01 * 0.65)
            base = tuple(lush[i] * (1.0 - t) + dry[i] * t for i in range(3))
            base = tuple(base[i] * (1.0 - rock * 0.82) + stone[i] * (rock * 0.82) for i in range(3))
            base = (base[0] * (1.0 - valley * 0.10), min(0.5, base[1] + valley * 0.055), base[2])
            color_values.append((*base, 1.0))

    for attr_name, values in (
        ("grass_mask", grass_values),
        ("rock_mask", rock_values),
        ("valley_mask", valley_values),
    ):
        attr = mesh.attributes.get(attr_name) or mesh.attributes.new(name=attr_name, type="FLOAT", domain="POINT")
        for item, value in zip(attr.data, values):
            item.value = float(value)

    color_attr = mesh.color_attributes.get("TerrainColor") or mesh.color_attributes.new(
        name="TerrainColor", type="FLOAT_COLOR", domain="CORNER"
    )
    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            color_attr.data[loop_index].color = color_values[vertex_index]

    for poly in mesh.polygons:
        poly.use_smooth = True

    obj["grassland_seed"] = config.seed
    obj["grassland_size"] = config.size
    obj["grassland_resolution"] = config.resolution
    return TerrainBuild(object=obj, min_height=min_h, max_height=max_h)
