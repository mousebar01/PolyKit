"""Semantic terrain regions, geomorphic operators, and material signals.

The prototype separates *replacement* regions (mountain/plain/river biomes) from
*additive* geomorphic overlays (lava flows, scars, deposits). Replacement regions
are normalized with a softmax. Additive regions use their own smooth mask and
modify the already blended base height.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from .noise import fbm_2d, ridged_fbm_2d

Color = tuple[float, float, float, float]
Point2D = tuple[float, float]


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def smoothstep01(value: float) -> float:
    value = clamp01(value)
    return value * value * (3.0 - 2.0 * value)


@dataclass(kw_only=True)
class TerrainRegion:
    id: str
    kind: str
    blend_mode: str = "replace"  # replace | add
    base_height: float = 0.0
    blend_width: float = 40.0
    mask_bias: float = 0.0
    noise_amplitude: float = 0.0
    noise_scale: float = 120.0
    octaves: int = 5
    lacunarity: float = 2.0
    gain: float = 0.5
    ridge_strength: float = 0.0
    ridge_scale: float = 80.0
    color: Color = (0.35, 0.35, 0.35, 1.0)

    # Semantic material signals. Terrain persists them as mesh attributes.
    heat_strength: float = 0.0
    ash_strength: float = 0.0
    rock_strength: float = 0.5

    def signed_distance(self, x: float, y: float) -> float:
        raise NotImplementedError

    def mask_logit(self, x: float, y: float) -> float:
        width = max(1e-3, float(self.blend_width))
        return self.mask_bias + self.signed_distance(x, y) / width

    def additive_mask(self, x: float, y: float) -> float:
        """Smooth independent mask for overlay regions."""
        value = max(-60.0, min(60.0, self.mask_logit(x, y)))
        return 1.0 / (1.0 + math.exp(-value))

    def local_height(self, x: float, y: float, *, seed: int) -> float:
        """Absolute height for replacement regions."""
        height = float(self.base_height)
        if self.noise_amplitude:
            scale = max(1e-3, float(self.noise_scale))
            height += self.noise_amplitude * fbm_2d(
                x / scale,
                y / scale,
                seed=seed,
                octaves=self.octaves,
                lacunarity=self.lacunarity,
                gain=self.gain,
            )
        if self.ridge_strength:
            scale = max(1e-3, float(self.ridge_scale))
            height += self.ridge_strength * ridged_fbm_2d(
                x / scale,
                y / scale,
                seed=seed + 1777,
                octaves=self.octaves,
                lacunarity=self.lacunarity,
                gain=self.gain,
            )
        return height

    def height_modifier(self, x: float, y: float, *, seed: int) -> float:
        """Height delta for additive regions."""
        value = 0.0
        if self.noise_amplitude:
            scale = max(1e-3, float(self.noise_scale))
            value += self.noise_amplitude * fbm_2d(
                x / scale,
                y / scale,
                seed=seed,
                octaves=self.octaves,
                lacunarity=self.lacunarity,
                gain=self.gain,
            )
        if self.ridge_strength:
            scale = max(1e-3, float(self.ridge_scale))
            value += self.ridge_strength * ridged_fbm_2d(
                x / scale,
                y / scale,
                seed=seed + 1777,
                octaves=self.octaves,
                lacunarity=self.lacunarity,
                gain=self.gain,
            )
        return value

    def heat_profile(self, x: float, y: float) -> float:
        del x, y
        return clamp01(self.heat_strength)

    def ash_profile(self, x: float, y: float) -> float:
        del x, y
        return clamp01(self.ash_strength)

    def rock_profile(self, x: float, y: float) -> float:
        del x, y
        return clamp01(self.rock_strength)


@dataclass(kw_only=True)
class BackgroundRegion(TerrainRegion):
    """Neutral region that covers the world and anchors softmax blending."""

    kind: str = "plain"

    def signed_distance(self, x: float, y: float) -> float:
        del x, y
        return 0.0


@dataclass(kw_only=True)
class CircleRegion(TerrainRegion):
    center: Point2D = (0.0, 0.0)
    radius: float = 100.0

    def signed_distance(self, x: float, y: float) -> float:
        dx = x - self.center[0]
        dy = y - self.center[1]
        return self.radius - math.hypot(dx, dy)


@dataclass(kw_only=True)
class VolcanoRegion(CircleRegion):
    """Radial volcanic massif with a rim and caldera depression."""

    kind: str = "volcano"
    cone_height: float = 220.0
    cone_power: float = 1.55
    crater_radius: float = 90.0
    crater_depth: float = 70.0
    rim_height: float = 28.0
    rim_width: float = 28.0
    radial_noise_amplitude: float = 18.0
    radial_noise_scale: float = 115.0
    crater_heat: float = 0.45

    def radial_distance(self, x: float, y: float) -> float:
        return math.hypot(x - self.center[0], y - self.center[1])

    def local_height(self, x: float, y: float, *, seed: int) -> float:
        height = super().local_height(x, y, seed=seed)
        distance = self.radial_distance(x, y)
        radius = max(1e-3, self.radius)
        radial = clamp01(1.0 - distance / radius)
        cone = self.cone_height * (radial ** max(0.2, self.cone_power))

        crater_radius = max(1e-3, self.crater_radius)
        crater = self.crater_depth * math.exp(-0.5 * (distance / crater_radius) ** 2)
        rim_width = max(1e-3, self.rim_width)
        rim = self.rim_height * math.exp(
            -0.5 * ((distance - crater_radius) / rim_width) ** 2
        )

        radial_noise = 0.0
        if self.radial_noise_amplitude:
            scale = max(1e-3, self.radial_noise_scale)
            radial_noise = self.radial_noise_amplitude * radial * fbm_2d(
                x / scale,
                y / scale,
                seed=seed + 4_901,
                octaves=max(3, self.octaves),
                lacunarity=self.lacunarity,
                gain=self.gain,
            )
        return height + cone + rim - crater + radial_noise

    def heat_profile(self, x: float, y: float) -> float:
        distance = self.radial_distance(x, y)
        crater_radius = max(1e-3, self.crater_radius)
        crater_core = math.exp(-0.5 * (distance / (crater_radius * 0.72)) ** 2)
        return clamp01(self.heat_strength + self.crater_heat * crater_core)

    def ash_profile(self, x: float, y: float) -> float:
        distance = self.radial_distance(x, y)
        radius = max(1e-3, self.radius)
        radial = clamp01(1.0 - distance / radius)
        return clamp01(self.ash_strength * (0.45 + 0.55 * radial))


@dataclass(kw_only=True)
class BoxRegion(TerrainRegion):
    center: Point2D = (0.0, 0.0)
    size: Point2D = (100.0, 100.0)

    def signed_distance(self, x: float, y: float) -> float:
        half_x = max(1e-3, self.size[0] * 0.5)
        half_y = max(1e-3, self.size[1] * 0.5)
        qx = abs(x - self.center[0]) - half_x
        qy = abs(y - self.center[1]) - half_y
        outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
        inside = min(max(qx, qy), 0.0)
        return -(outside + inside)


def _distance_point_to_segment(
    x: float,
    y: float,
    a: Point2D,
    b: Point2D,
) -> float:
    abx = b[0] - a[0]
    aby = b[1] - a[1]
    length_sq = abx * abx + aby * aby
    if length_sq <= 1e-12:
        return math.hypot(x - a[0], y - a[1])
    t = ((x - a[0]) * abx + (y - a[1]) * aby) / length_sq
    t = max(0.0, min(1.0, t))
    px = a[0] + abx * t
    py = a[1] + aby * t
    return math.hypot(x - px, y - py)


def distance_to_polyline(x: float, y: float, points: Sequence[Point2D]) -> float:
    if not points:
        return math.inf
    if len(points) == 1:
        return math.hypot(x - points[0][0], y - points[0][1])
    return min(
        _distance_point_to_segment(x, y, points[index], points[index + 1])
        for index in range(len(points) - 1)
    )


@dataclass(kw_only=True)
class SplineRegion(TerrainRegion):
    """Polyline-backed strip region, primarily used for river valleys."""

    points: Sequence[Point2D] = ()
    width: float = 80.0
    channel_depth: float = 0.0

    def distance_to_centerline(self, x: float, y: float) -> float:
        return distance_to_polyline(x, y, self.points)

    def signed_distance(self, x: float, y: float) -> float:
        return self.width * 0.5 - self.distance_to_centerline(x, y)

    def center_weight(self, x: float, y: float, *, power: float = 2.0) -> float:
        half_width = max(1e-3, self.width * 0.5)
        distance = self.distance_to_centerline(x, y)
        value = clamp01(1.0 - distance / half_width)
        return value ** max(0.1, power)

    def local_height(self, x: float, y: float, *, seed: int) -> float:
        height = super().local_height(x, y, seed=seed)
        if not self.channel_depth:
            return height
        return height - self.channel_depth * self.center_weight(x, y, power=2.0)

    def heat_profile(self, x: float, y: float) -> float:
        return clamp01(self.heat_strength * self.center_weight(x, y, power=0.72))


@dataclass(kw_only=True)
class LavaFlowRegion(SplineRegion):
    """Additive lava-flow operator with a hot core and raised cooled levees."""

    kind: str = "lava"
    blend_mode: str = "add"
    flow_thickness: float = 5.0
    incision_depth: float = 2.5
    levee_height: float = 4.0
    levee_position: float = 0.72
    levee_width: float = 0.14
    heat_falloff: float = 0.68
    heat_strength: float = 1.0
    ash_strength: float = 0.05
    rock_strength: float = 0.35

    def height_modifier(self, x: float, y: float, *, seed: int) -> float:
        del seed
        half_width = max(1e-3, self.width * 0.5)
        distance = self.distance_to_centerline(x, y)
        normalized = distance / half_width
        if normalized >= 1.5:
            return 0.0

        core = clamp01(1.0 - normalized)
        thickness = self.flow_thickness * (core ** 1.35)
        incision = self.incision_depth * (core ** 2.2)
        levee_width = max(1e-3, self.levee_width)
        levee = self.levee_height * math.exp(
            -0.5 * ((normalized - self.levee_position) / levee_width) ** 2
        )
        return thickness + levee - incision

    def heat_profile(self, x: float, y: float) -> float:
        half_width = max(1e-3, self.width * 0.5)
        distance = self.distance_to_centerline(x, y)
        normalized = clamp01(distance / half_width)
        core = (1.0 - normalized) ** max(0.1, self.heat_falloff)
        return clamp01(self.heat_strength * core)

    def ash_profile(self, x: float, y: float) -> float:
        return clamp01(self.ash_strength * (1.0 - self.heat_profile(x, y)))
