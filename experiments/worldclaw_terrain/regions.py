"""Semantic terrain regions and local/overlay height functions.

A region provides a signed-distance field used for soft semantic masks.  Base
regions also provide an absolute local height that participates in normalized
height blending.  Overlay regions (rivers, lava flows, paths) instead modify the
already-composed terrain height, avoiding the common failure mode where a narrow
semantic strip flattens an entire mountain toward its own absolute elevation.

Positive signed distance means "inside" the region.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from .noise import fbm_2d, ridged_fbm_2d

Color = tuple[float, float, float, float]
Point2D = tuple[float, float]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge1 <= edge0:
        return 1.0 if value >= edge1 else 0.0
    t = _clamp01((value - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


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
    terrace_step: float = 0.0
    terrace_strength: float = 0.0
    color: Color = (0.35, 0.35, 0.35, 1.0)
    height_mode: str = "blend"  # "blend" or "overlay"

    def signed_distance(self, x: float, y: float) -> float:
        raise NotImplementedError

    def mask_logit(self, x: float, y: float) -> float:
        width = max(1e-3, float(self.blend_width))
        return self.mask_bias + self.signed_distance(x, y) / width

    def coverage(self, x: float, y: float) -> float:
        """Independent 0..1 coverage used by overlay regions and diagnostics."""
        value = max(-60.0, min(60.0, self.mask_logit(x, y)))
        return 1.0 / (1.0 + math.exp(-value))

    def local_height(self, x: float, y: float, *, seed: int) -> float:
        """Absolute height contribution for ``height_mode='blend'`` regions."""
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
        if self.terrace_step > 1e-6 and self.terrace_strength > 1e-6:
            step = float(self.terrace_step)
            strength = _clamp01(float(self.terrace_strength))
            terraced = round(height / step) * step
            height = height * (1.0 - strength) + terraced * strength
        return height

    def height_offset(self, x: float, y: float, *, seed: int) -> float:
        """Relative height modification for ``height_mode='overlay'`` regions."""
        del x, y, seed
        return 0.0


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
    """Stylized volcanic massif with a broad cone, crater bowl, and rim.

    The operator intentionally exaggerates silhouette readability.  Noise and
    ridges from :class:`TerrainRegion` are layered over this macro shape.
    """

    kind: str = "volcano"
    cone_height: float = 180.0
    cone_power: float = 1.35
    crater_radius: float = 70.0
    crater_depth: float = 72.0
    rim_height: float = 18.0
    rim_width: float = 28.0

    def local_height(self, x: float, y: float, *, seed: int) -> float:
        height = super().local_height(x, y, seed=seed)
        distance = math.hypot(x - self.center[0], y - self.center[1])
        radius = max(1e-3, float(self.radius))
        radial = _clamp01(distance / radius)
        envelope = max(0.0, 1.0 - radial)
        cone = self.cone_height * (envelope ** max(0.2, self.cone_power))

        crater_radius = max(1e-3, float(self.crater_radius))
        crater_ratio = distance / crater_radius
        # A fourth-power falloff keeps the bowl broad but its outer transition
        # compact enough to read clearly from a third-person camera.
        crater = self.crater_depth * math.exp(-(crater_ratio ** 4))

        rim_width = max(1e-3, float(self.rim_width))
        rim_delta = (distance - crater_radius) / rim_width
        rim = self.rim_height * math.exp(-(rim_delta * rim_delta))
        return height + cone - crater + rim


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
    """Polyline-backed overlay strip used for rivers, paths, and channels."""

    points: Sequence[Point2D] = ()
    width: float = 80.0
    channel_depth: float = 0.0
    height_mode: str = "overlay"

    def distance_to_centerline(self, x: float, y: float) -> float:
        return distance_to_polyline(x, y, self.points)

    def center_weight(self, x: float, y: float) -> float:
        half_width = max(1e-3, self.width * 0.5)
        return _clamp01(1.0 - self.distance_to_centerline(x, y) / half_width)

    def signed_distance(self, x: float, y: float) -> float:
        return self.width * 0.5 - self.distance_to_centerline(x, y)

    def height_offset(self, x: float, y: float, *, seed: int) -> float:
        center = self.center_weight(x, y)
        if center <= 0.0:
            return 0.0
        offset = -self.channel_depth * center * center
        if self.noise_amplitude:
            scale = max(1e-3, float(self.noise_scale))
            offset += (
                self.noise_amplitude
                * fbm_2d(
                    x / scale,
                    y / scale,
                    seed=seed,
                    octaves=max(1, self.octaves),
                    lacunarity=self.lacunarity,
                    gain=self.gain,
                )
                * center
            )
        return offset


@dataclass(kw_only=True)
class LavaSplineRegion(SplineRegion):
    """Stylized lava channel with a shallow cut and raised cooling levees."""

    kind: str = "lava"
    channel_depth: float = 5.0
    levee_height: float = 3.5
    levee_position: float = 0.72
    levee_width: float = 0.18

    def height_offset(self, x: float, y: float, *, seed: int) -> float:
        offset = super().height_offset(x, y, seed=seed)
        half_width = max(1e-3, self.width * 0.5)
        normalized_distance = self.distance_to_centerline(x, y) / half_width
        width = max(1e-3, self.levee_width)
        levee_delta = (normalized_distance - self.levee_position) / width
        levee = self.levee_height * math.exp(-(levee_delta * levee_delta))
        # Fade the levee out beyond the semantic strip.
        return offset + levee * (1.0 - _smoothstep(0.95, 1.25, normalized_distance))
