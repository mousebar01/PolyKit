"""Coverage-aware Terrain Compiler v2 composition.

Descriptors without an explicit ``coverage`` field keep the existing local-mask
behavior in terrain_fields.py. A normalized ``coverage=world`` region acts as
the complete terrain base; local regions consume that base weight where they
have influence, so no generic background terrain leaks into a single-region
world.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

import terrain_fields as base


@dataclass(frozen=True)
class CoverageProgram:
    base_program: base.TerrainProgram
    coverage: tuple[str, ...]

    @property
    def seed(self) -> int:
        return self.base_program.seed

    @property
    def size(self) -> float:
        return self.base_program.size

    @property
    def sea_level(self) -> float:
        return self.base_program.sea_level

    @property
    def resolution(self) -> int:
        return self.base_program.resolution

    @property
    def regions(self) -> tuple[base.TerrainRegion, ...]:
        return self.base_program.regions

    @property
    def rivers(self) -> tuple[base.TerrainRiver, ...]:
        return self.base_program.rivers


def _coverage(value: Any, *, index: int) -> str:
    if value is None:
        return "local"
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"regions[{index}].coverage must be world or local")
    normalized = value.strip().lower()
    if normalized not in {"world", "local"}:
        raise ValueError(f"regions[{index}].coverage must be world or local")
    return normalized


def parse_program(descriptor: dict[str, Any], *, resolution: int) -> CoverageProgram:
    program = base.parse_program(descriptor, resolution=resolution)
    raw_regions = descriptor.get("regions") if isinstance(descriptor.get("regions"), list) else []
    coverage = tuple(
        _coverage(raw_regions[index].get("coverage") if index < len(raw_regions) and isinstance(raw_regions[index], dict) else None, index=index)
        for index in range(len(program.regions))
    )
    if coverage.count("world") > 1:
        raise ValueError("regions may contain at most one coverage=world region")
    return CoverageProgram(base_program=program, coverage=coverage)


def _sample_region(
    *,
    program: CoverageProgram,
    fields: list[tuple[base.TerrainRegion, base.Noise2D, base.Noise2D]],
    region_index: int,
    u: float,
    v: float,
    warp_noise: base.Noise2D,
) -> tuple[float, float]:
    region, terrain_noise, detail_noise = fields[region_index]
    warp_strength = base.clamp(region.irregularity, 0.0, 1.0) * max(0.0, region.radius) * 0.9
    query_x = u * 3.0 + region_index * 7.31
    query_y = v * 3.0 - region_index * 4.17
    warped_x, warped_y = warp_noise.warp(query_x, query_y, 1.0)
    du = u - region.center[0] + (warped_x - query_x) * warp_strength * 0.33
    dv = v - region.center[1] + (warped_y - query_y) * warp_strength * 0.33
    normalized_distance = math.hypot(du, dv) / max(region.radius, 1e-4)
    weight = 1.0 - base.smoothstep(0.55, 1.15, normalized_distance)
    elevation = base._region_elevation(
        region,
        terrain_noise,
        detail_noise,
        u,
        v,
        base.clamp(normalized_distance, 0.0, 1.0),
    )
    return weight, elevation


def compile_fields(program: CoverageProgram) -> base.TerrainFields:
    if "world" not in program.coverage:
        return base.compile_fields(program.base_program)

    world_index = program.coverage.index("world")
    res = program.resolution
    heights = np.empty(res * res, dtype=np.float32)
    dominant = np.full(res * res, -1, dtype=np.int16)
    region_weights = [np.zeros(res * res, dtype=np.float32) for _ in program.regions]
    warp_noise = base.Noise2D(program.seed ^ 0x5EED)
    background_noise = base.Noise2D(program.seed ^ 0xBA5E)
    fields = [
        (
            region,
            base.Noise2D(base._u32(program.seed ^ base.hash_string(region.id))),
            base.Noise2D(base._u32(program.seed ^ base.hash_string(f"{region.id}:detail"))),
        )
        for region in program.regions
    ]

    for row in range(res):
        v = row / (res - 1)
        for column in range(res):
            u = column / (res - 1)
            index = row * res + column
            raw_local: list[tuple[int, float, float]] = []
            local_total = 0.0
            world_elevation = 0.0

            for region_index in range(len(fields)):
                raw_weight, elevation = _sample_region(
                    program=program,
                    fields=fields,
                    region_index=region_index,
                    u=u,
                    v=v,
                    warp_noise=warp_noise,
                )
                if region_index == world_index:
                    world_elevation = elevation
                    continue
                if raw_weight <= 0.001:
                    continue
                raw_local.append((region_index, raw_weight, elevation))
                local_total += raw_weight

            local_scale = 1.0 / local_total if local_total > 1.0 else 1.0
            world_weight = max(0.0, 1.0 - min(local_total, 1.0))
            region_weights[world_index][index] = world_weight
            height = world_elevation * world_weight
            best_region = world_index
            best_weight = world_weight

            for region_index, raw_weight, elevation in raw_local:
                effective_weight = raw_weight * local_scale
                region_weights[region_index][index] = effective_weight
                height += elevation * effective_weight
                if effective_weight > best_weight:
                    best_region = region_index
                    best_weight = effective_weight

            micro = (
                background_noise.fbm(u * 34.0 + 11.3, v * 34.0 - 5.7, 3) * 1.1
                + background_noise.fbm(u * 90.0 - 2.1, v * 90.0 + 8.4, 2) * 0.3
            )
            heights[index] = height + micro
            if best_weight > 0.25:
                dominant[index] = best_region

    for row in range(res):
        v = row / (res - 1)
        for column in range(res):
            u = column / (res - 1)
            edge_distance = min(u, v, 1.0 - u, 1.0 - v)
            falloff = base.smoothstep(0.0, 0.08, edge_distance)
            index = row * res + column
            heights[index] = base.lerp(program.sea_level - 9.0, float(heights[index]), falloff)

    river_distance = base._carve_rivers(
        program.rivers,
        heights,
        res,
        program.size,
        program.sea_level,
        warp_noise,
    )
    return base.TerrainFields(
        program=program.base_program,
        heights=heights,
        region_weights=region_weights,
        dominant=dominant,
        river_distance=river_distance,
    )


surface_colors = base.surface_colors
grid_sample = base.grid_sample


__all__ = [
    "CoverageProgram",
    "compile_fields",
    "grid_sample",
    "parse_program",
    "surface_colors",
]
