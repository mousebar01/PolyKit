"""Derived terrain surface fields shared by materials and scatter."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .noise import fbm_2d
from .regions import TerrainRegion, clamp01


@dataclass(frozen=True)
class SurfaceSample:
    x: float
    y: float
    z: float
    height01: float
    slope01: float
    lava_heat: float
    ash_mask: float
    rock_mask: float
    normal: tuple[float, float, float]


def _normalized(vx: float, vy: float, vz: float) -> tuple[float, float, float]:
    length = math.sqrt(vx * vx + vy * vy + vz * vz)
    if length <= 1e-12:
        return (0.0, 0.0, 1.0)
    return (vx / length, vy / length, vz / length)


def derive_surface_samples(
    *,
    heights: list[float],
    masks: list[list[float]],
    regions: list[TerrainRegion],
    resolution: int,
    step: float,
    half_size: float,
    seed: int,
    max_slope_degrees: float = 62.0,
) -> list[SurfaceSample]:
    if not heights:
        return []
    minimum = min(heights)
    maximum = max(heights)
    height_span = max(1e-6, maximum - minimum)
    max_slope = math.radians(max(1.0, max_slope_degrees))
    result: list[SurfaceSample] = []

    def h(row: int, col: int) -> float:
        row = max(0, min(resolution - 1, row))
        col = max(0, min(resolution - 1, col))
        return heights[row * resolution + col]

    for row in range(resolution):
        y = -half_size + row * step
        for col in range(resolution):
            x = -half_size + col * step
            index = row * resolution + col
            z = heights[index]

            left = h(row, col - 1)
            right = h(row, col + 1)
            down = h(row - 1, col)
            up = h(row + 1, col)
            x_den = step if col in {0, resolution - 1} else step * 2.0
            y_den = step if row in {0, resolution - 1} else step * 2.0
            dzdx = (right - left) / max(1e-6, x_den)
            dzdy = (up - down) / max(1e-6, y_den)
            normal = _normalized(-dzdx, -dzdy, 1.0)
            slope_angle = math.atan(math.sqrt(dzdx * dzdx + dzdy * dzdy))
            slope01 = clamp01(slope_angle / max_slope)
            height01 = clamp01((z - minimum) / height_span)

            sample_masks = masks[index]
            heat = 0.0
            ash = 0.0
            rock = 0.0
            replace_weight = 0.0
            for region, mask in zip(regions, sample_masks):
                if mask <= 1e-7:
                    continue
                local_heat = clamp01(region.heat_profile(x, y))
                local_ash = clamp01(region.ash_profile(x, y))
                local_rock = clamp01(region.rock_profile(x, y))
                if region.blend_mode == "add":
                    heat = max(heat, mask * local_heat)
                    ash = max(ash, mask * local_ash)
                    rock = max(rock, mask * local_rock)
                else:
                    heat += mask * local_heat
                    ash += mask * local_ash
                    rock += mask * local_rock
                    replace_weight += mask

            if replace_weight > 1.0 + 1e-5:
                heat /= replace_weight
                ash /= replace_weight
                rock /= replace_weight

            ash_noise = 0.82 + 0.18 * (
                0.5
                + 0.5
                * fbm_2d(
                    x / 180.0,
                    y / 180.0,
                    seed=seed + 91_117,
                    octaves=4,
                    lacunarity=2.0,
                    gain=0.5,
                )
            )
            ash = clamp01(ash * ash_noise * (1.0 - heat ** 0.55) * (1.0 - 0.58 * slope01))
            rock = clamp01(
                rock * (0.38 + 0.62 * slope01) * (1.0 - 0.38 * ash)
                + 0.22 * slope01
            )
            heat = clamp01(heat)

            result.append(
                SurfaceSample(
                    x=x,
                    y=y,
                    z=z,
                    height01=height01,
                    slope01=slope01,
                    lava_heat=heat,
                    ash_mask=ash,
                    rock_mask=rock,
                    normal=normal,
                )
            )
    return result
