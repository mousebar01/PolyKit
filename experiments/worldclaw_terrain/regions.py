"""Semantic terrain regions and local height functions.

A region provides two things:

* a signed-distance field used to build soft semantic masks;
* a local height function that is blended with the other regions.

Positive signed distance means "inside" the region. The background region uses
zero everywhere and therefore acts as the neutral softmax baseline.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from .noise import fbm_2d, ridged_fbm_2d

Color = tuple[float, float, float, float]
Point2D = tuple[float, float]


@dataclass(kw_only=True)
class TerrainRegion:
    id: str
    kind: str
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

    def signed_distance(self, x: float, y: float) -> float:
        raise NotImplementedError

    def mask_logit(self, x: float, y: float) -> float:
        width = max(1e-3, float(self.blend_width))
        return self.mask_bias + self.signed_distance(x, y) / width

    def local_height(self, x: float, y: float, *, seed: int) -> float:
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
        # Standard box SDF is negative inside. Invert it for our convention.
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

    def local_height(self, x: float, y: float, *, seed: int) -> float:
        height = super().local_height(x, y, seed=seed)
        if not self.channel_depth:
            return height
        half_width = max(1e-3, self.width * 0.5)
        distance = self.distance_to_centerline(x, y)
        center_weight = max(0.0, 1.0 - distance / half_width)
        # Squaring keeps the banks broad while concentrating the deepest cut
        # near the centerline.
        return height - self.channel_depth * center_weight * center_weight
