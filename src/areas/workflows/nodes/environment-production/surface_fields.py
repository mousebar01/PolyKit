"""Deterministic terrain surface/biome fields for Terrain Compiler v2.

This module mirrors worlds/runtime/surfaceFields.ts. Surface channels are a
small fixed vocabulary; region count affects influence masks, not material-layer
count. Geometry stays in terrain_fields.py.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

SURFACE_KINDS = (
    "rock",
    "grass",
    "sand",
    "snow",
    "forest",
    "swamp",
    "beach",
    "water",
)
_SURFACE_INDEX = {name: index for index, name in enumerate(SURFACE_KINDS)}
_LEGACY_SURFACE_BY_KIND = {
    "mountain": "rock",
    "hills": "grass",
    "plains": "grass",
    "desert": "sand",
    "dunes": "sand",
    "water": "water",
    "canyon": "rock",
    "volcanic": "rock",
    "snow": "snow",
    "forest": "forest",
    "swamp": "swamp",
    "beach": "beach",
    "mesa": "rock",
}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return minimum if value < minimum else maximum if value > maximum else value


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 0.0 if value < edge0 else 1.0
    factor = _clamp((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return factor * factor * (3.0 - 2.0 * factor)


def surface_for_region(explicit_surface: Any, kind: Any) -> str:
    if isinstance(explicit_surface, str) and explicit_surface.strip():
        value = explicit_surface.strip().lower()
        if value not in _SURFACE_INDEX:
            raise ValueError(f"Unsupported terrain surface: {explicit_surface}")
        return value
    return _LEGACY_SURFACE_BY_KIND.get(str(kind or "").strip().lower(), "rock")


def _suitability(surface: str, altitude: float, slope: float) -> float:
    if surface == "rock":
        return 1.0
    if surface == "snow":
        return _clamp(
            _smoothstep(0.35, 0.72, altitude) * (1.0 - 0.7 * _smoothstep(38.0, 60.0, slope)),
            0.0,
            1.0,
        )
    if surface == "forest":
        return _clamp(
            (1.0 - _smoothstep(28.0, 48.0, slope)) * (1.0 - _smoothstep(0.65, 0.9, altitude)),
            0.0,
            1.0,
        )
    if surface == "grass":
        return _clamp(
            (1.0 - _smoothstep(35.0, 55.0, slope)) * (1.0 - _smoothstep(0.78, 0.98, altitude)),
            0.0,
            1.0,
        )
    if surface == "sand":
        return _clamp(
            (1.0 - _smoothstep(25.0, 45.0, slope)) * (1.0 - _smoothstep(0.58, 0.9, altitude)),
            0.0,
            1.0,
        )
    if surface == "beach":
        return _clamp(
            (1.0 - _smoothstep(18.0, 36.0, slope)) * (1.0 - _smoothstep(0.12, 0.32, altitude)),
            0.0,
            1.0,
        )
    if surface == "swamp":
        return _clamp(
            (1.0 - _smoothstep(12.0, 30.0, slope)) * (1.0 - _smoothstep(0.18, 0.42, altitude)),
            0.0,
            1.0,
        )
    if surface == "water":
        return _clamp(1.0 - _smoothstep(0.02, 0.12, altitude), 0.0, 1.0)
    return 0.0


def compile_surface_fields(
    *,
    heights: np.ndarray,
    region_weights: Sequence[np.ndarray],
    region_surfaces: Sequence[str],
    resolution: int,
    size: float,
    sea_level: float,
) -> tuple[list[np.ndarray], np.ndarray]:
    res = int(resolution)
    if res < 2 or len(heights) != res * res:
        raise ValueError("Surface fields require a square terrain height grid")
    if any(len(weights) != len(heights) for weights in region_weights):
        raise ValueError("Surface region weights must match the terrain height grid")

    surface_weights = [np.zeros(len(heights), dtype=np.float32) for _ in SURFACE_KINDS]
    dominant_surface = np.full(len(heights), -1, dtype=np.int8)
    max_height = max(float(value) for value in heights)
    relief = max(max_height - float(sea_level), 1e-6)
    cell_size = max(float(size), 0.001) / (res - 1)
    water_index = _SURFACE_INDEX["water"]
    rock_index = _SURFACE_INDEX["rock"]
    grass_index = _SURFACE_INDEX["grass"]

    def grid_height(column: int, row: int) -> float:
        safe_column = max(0, min(res - 1, column))
        safe_row = max(0, min(res - 1, row))
        return float(heights[safe_row * res + safe_column])

    for row in range(res):
        for column in range(res):
            index = row * res + column
            height = float(heights[index])
            if height <= sea_level:
                surface_weights[water_index][index] = 1.0
                dominant_surface[index] = water_index
                continue

            left = grid_height(column - 1, row)
            right = grid_height(column + 1, row)
            down = grid_height(column, row - 1)
            up = grid_height(column, row + 1)
            nx = left - right
            nz = down - up
            ny = 2.0 * cell_size
            length = math.hypot(nx, ny, nz) or 1.0
            slope = math.acos(_clamp(ny / length, -1.0, 1.0)) * 180.0 / math.pi
            altitude = _clamp((height - sea_level) / relief, 0.0, 1.0)
            accumulated = [0.0] * len(SURFACE_KINDS)
            total_region_weight = 0.0

            for region_index, weights in enumerate(region_weights):
                weight = _clamp(float(weights[index]), 0.0, 1.0)
                if weight <= 0.0:
                    continue
                total_region_weight += weight
                surface = region_surfaces[region_index] if region_index < len(region_surfaces) else "rock"
                if surface not in _SURFACE_INDEX:
                    raise ValueError(f"Unsupported terrain surface: {surface}")
                surface_index = _SURFACE_INDEX[surface]
                fit = _suitability(surface, altitude, slope)
                if surface == "rock":
                    accumulated[rock_index] += weight
                else:
                    primary = weight * fit
                    accumulated[surface_index] += primary
                    accumulated[rock_index] += weight - primary

            background_weight = max(0.0, 1.0 - total_region_weight)
            background_rock = _clamp(
                0.15
                + 0.65 * _smoothstep(18.0, 45.0, slope)
                + 0.2 * _smoothstep(0.6, 0.9, altitude),
                0.0,
                1.0,
            )
            accumulated[rock_index] += background_weight * background_rock
            accumulated[grass_index] += background_weight * (1.0 - background_rock)

            total = sum(accumulated)
            if total <= 1e-9:
                accumulated[grass_index] = 1.0
                total = 1.0

            best_index = -1
            best_weight = -1.0
            for surface_index, raw_weight in enumerate(accumulated):
                weight = raw_weight / total
                surface_weights[surface_index][index] = weight
                if weight > best_weight:
                    best_weight = weight
                    best_index = surface_index
            dominant_surface[index] = best_index

    return surface_weights, dominant_surface


def grid_surface_sample(
    surface_weights: Sequence[np.ndarray],
    dominant_surface: np.ndarray,
    column: int,
    row: int,
    resolution: int,
) -> dict[str, Any]:
    res = int(resolution)
    if not (0 <= column < res and 0 <= row < res):
        raise ValueError("grid sample is outside the terrain")
    index = row * res + column
    return {
        "surfaceWeights": [float(weights[index]) for weights in surface_weights],
        "dominantSurface": int(dominant_surface[index]),
    }
