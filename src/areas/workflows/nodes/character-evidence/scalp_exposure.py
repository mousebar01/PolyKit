"""Scalp exposure gate used by the character-evidence process pack.

The gate only counts hair points that stand outside a ring-stack skull. Nearby points
that have sunk inside the skull are deliberately not coverage, which catches the
recorded failure where widening a hair mass made the crown visibly bald.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Iterable, Sequence

Ring = tuple[float, float, float, float]
Point = tuple[float, float, float]


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def normalise_rings(rings: Any) -> list[Ring]:
    if not isinstance(rings, list):
        raise ValueError("rings must be a list")
    result: list[Ring] = []
    for index, raw in enumerate(rings):
        label = f"rings[{index}]"
        if isinstance(raw, dict):
            y = _finite(raw.get("y"), f"{label}.y")
            rx = _finite(raw.get("rx", raw.get("radiusX")), f"{label}.rx")
            rz = _finite(raw.get("rz", raw.get("radiusZ")), f"{label}.rz")
            zc = _finite(raw.get("zc", raw.get("zCentre", 0.0)), f"{label}.zc")
        elif isinstance(raw, list) and len(raw) in (3, 4):
            y, rx, rz = (_finite(raw[position], f"{label}[{position}]") for position in range(3))
            zc = _finite(raw[3], f"{label}[3]") if len(raw) == 4 else 0.0
        else:
            raise ValueError(f"{label} must contain [y, rx, rz] or [y, rx, rz, zc]")
        if rx <= 0.0 or rz <= 0.0:
            raise ValueError(f"{label} radii must be positive")
        result.append((y, rx, rz, zc))
    if len(result) < 2:
        raise ValueError("at least two scalp rings are required")
    result.sort(key=lambda ring: ring[0])
    if any(upper[0] - lower[0] <= 1e-9 for lower, upper in zip(result, result[1:])):
        raise ValueError("scalp rings must have distinct y values")
    return result


class ScalpField:
    def __init__(self, rings: Any):
        self.rings = normalise_rings(rings)
        self.y_min = self.rings[0][0]
        self.y_max = self.rings[-1][0]

    def section(self, y: float) -> tuple[float, float, float]:
        if y <= self.y_min:
            _, rx, rz, zc = self.rings[0]
            return rx, rz, zc
        if y >= self.y_max:
            _, rx, rz, zc = self.rings[-1]
            return rx, rz, zc
        for lower, upper in zip(self.rings, self.rings[1:]):
            if lower[0] <= y <= upper[0]:
                ratio = (y - lower[0]) / (upper[0] - lower[0])
                return tuple(lower[channel] + (upper[channel] - lower[channel]) * ratio for channel in (1, 2, 3))  # type: ignore[return-value]
        raise AssertionError(f"no scalp ring contains y={y}")

    def radial_distance(self, x: float, y: float, z: float) -> float:
        rx, rz, zc = self.section(y)
        dx, dz = x / rx, (z - zc) / rz
        value = dx * dx + dz * dz - 1.0
        gx, gz = 2.0 * x / (rx * rx), 2.0 * (z - zc) / (rz * rz)
        gradient = math.hypot(gx, gz)
        return -min(rx, rz) if gradient < 1e-12 else value / gradient

    def distance(self, x: float, y: float, z: float) -> float:
        radial = self.radial_distance(x, y, z)
        axial = max(self.y_min - y, y - self.y_max)
        return math.hypot(max(radial, 0.0), max(axial, 0.0)) + min(max(radial, axial), 0.0)

    def sample(self, u: float, v: float) -> Point:
        theta = 2.0 * math.pi * u
        y = self.y_min + (self.y_max - self.y_min) * v
        rx, rz, zc = self.section(y)
        return rx * math.cos(theta), y, zc + rz * math.sin(theta)

    def normal(self, u: float, v: float) -> Point:
        theta = 2.0 * math.pi * u
        cos_theta, sin_theta = math.cos(theta), math.sin(theta)
        height = self.y_max - self.y_min
        y = self.y_min + height * v
        rx, rz, _ = self.section(y)
        delta = max(height * 1e-4, 1e-9)
        rx_hi, rz_hi, zc_hi = self.section(min(y + delta, self.y_max))
        rx_lo, rz_lo, zc_lo = self.section(max(y - delta, self.y_min))
        actual = min(y + delta, self.y_max) - max(y - delta, self.y_min)
        if actual <= 0.0:
            drx = drz = dzc = 0.0
        else:
            drx, drz, dzc = ((hi - lo) / actual for hi, lo in ((rx_hi, rx_lo), (rz_hi, rz_lo), (zc_hi, zc_lo)))
        du = (-rx * sin_theta, 0.0, rz * cos_theta)
        dv = (drx * cos_theta * height, height, (dzc + drz * sin_theta) * height)
        nx = dv[1] * du[2] - dv[2] * du[1]
        ny = dv[2] * du[0] - dv[0] * du[2]
        nz = dv[0] * du[1] - dv[1] * du[0]
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        return (cos_theta, 0.0, sin_theta) if length < 1e-12 else (nx / length, ny / length, nz / length)

    def surface_samples(self, u_count: int, v_count: int, v_range: tuple[float, float]) -> list[dict[str, Any]]:
        if u_count < 3 or v_count < 2:
            raise ValueError("u_samples must be at least 3 and v_samples at least 2")
        low, high = v_range
        if not 0.0 <= low < high <= 1.0:
            raise ValueError("v_range must be ascending and inside [0,1]")
        samples: list[dict[str, Any]] = []
        height = self.y_max - self.y_min
        band = high - low
        for row in range(v_count):
            v = low + band * (row + 0.5) / v_count
            y = self.y_min + height * v
            rx, rz, _ = self.section(y)
            circumference = math.pi * (3.0 * (rx + rz) - math.sqrt((3.0 * rx + rz) * (rx + 3.0 * rz)))
            weight = circumference / u_count * (height * band / v_count)
            for column in range(u_count):
                u = column / u_count
                samples.append({"weight": weight, "point": self.sample(u, v), "normal": self.normal(u, v), "u": u, "v": v, "cap": False})
        if high >= 1.0 - 1e-9:
            top_rx, top_rz, top_zc = self.section(self.y_max)
            cap_rings = max(1, v_count // 4)
            for ring_index in range(cap_rings):
                middle = (ring_index + 0.5) / cap_rings
                outer, inner = (ring_index + 1) / cap_rings, ring_index / cap_rings
                weight = math.pi * top_rx * top_rz * (outer * outer - inner * inner) / u_count
                for column in range(u_count):
                    u = column / u_count
                    theta = 2.0 * math.pi * u
                    samples.append({"weight": weight, "point": (top_rx * middle * math.cos(theta), self.y_max, top_zc + top_rz * middle * math.sin(theta)), "normal": (0.0, 1.0, 0.0), "u": u, "v": 1.0, "cap": True, "capRing": ring_index})
        return samples


class _PointGrid:
    def __init__(self, points: Sequence[Point], cell: float):
        self.cell = max(cell, 1e-9)
        self.buckets: dict[tuple[int, int, int], list[Point]] = {}
        for point in points:
            self.buckets.setdefault(self._key(point), []).append(point)

    def _key(self, point: Point) -> tuple[int, int, int]:
        return tuple(int(math.floor(point[axis] / self.cell)) for axis in range(3))  # type: ignore[return-value]

    def near(self, point: Point) -> Iterable[Point]:
        cx, cy, cz = self._key(point)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    yield from self.buckets.get((cx + dx, cy + dy, cz + dz), [])


def _point(value: Any, label: str) -> Point:
    if not isinstance(value, list) or len(value) != 3 or not all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in value):
        raise ValueError(f"{label} must be a finite [x,y,z] point")
    return tuple(float(item) for item in value)  # type: ignore[return-value]


def largest_exposed_run(result: dict[str, Any], u_samples: int) -> int:
    rows: dict[tuple[float, int], set[int]] = {}
    for item in result.get("exposedSamples", []):
        column = int(round(float(item["u"]) * u_samples)) % u_samples
        rows.setdefault((float(item["v"]), int(item.get("capRing", -1))), set()).add(column)
    longest = 0
    for columns in rows.values():
        if len(columns) >= u_samples:
            return u_samples
        for start in columns:
            if (start - 1) % u_samples in columns:
                continue
            run = 1
            while (start + run) % u_samples in columns and run < u_samples:
                run += 1
            longest = max(longest, run)
    return longest


def measure_exposure(
    rings: Any,
    hair_points: Any,
    *,
    u_samples: int = 32,
    v_samples: int = 16,
    reach: float | None = None,
    lateral: float | None = None,
    hard_max: float = 0.05,
    v_range: tuple[float, float] = (0.0, 1.0),
) -> dict[str, Any]:
    field = ScalpField(rings)
    points = [_point(value, f"hairPoints[{index}]") for index, value in enumerate(hair_points)] if isinstance(hair_points, list) else []
    height = field.y_max - field.y_min
    if height <= 0.0:
        raise ValueError("scalp field has no height")
    reach_distance = float(reach) if reach is not None else 0.22 * height
    lateral_distance = float(lateral) if lateral is not None else 0.075 * height
    if reach_distance <= 0.0 or lateral_distance <= 0.0:
        raise ValueError("reach and lateral must both be positive")
    if not 0.0 <= v_range[0] < v_range[1] <= 1.0:
        raise ValueError("v_range must be ascending and inside [0,1]")
    outside = [point for point in points if field.distance(*point) > 0.0]
    grid = _PointGrid(outside, lateral_distance)
    march_steps = max(2, int(math.ceil(reach_distance / lateral_distance)) + 1)
    total_area = exposed_area = 0.0
    exposed: list[dict[str, Any]] = []
    for sample in field.surface_samples(u_samples, v_samples, v_range):
        px, py, pz = sample["point"]
        nx, ny, nz = sample["normal"]
        total_area += float(sample["weight"])
        covered = False
        for step in range(march_steps + 1):
            distance = reach_distance * step / march_steps
            query = (px + nx * distance, py + ny * distance, pz + nz * distance)
            if any(math.dist(query, hair) <= lateral_distance for hair in grid.near(query)):
                covered = True
                break
        if not covered:
            exposed_area += float(sample["weight"])
            exposed.append({"u": round(float(sample["u"]), 4), "v": round(float(sample["v"]), 4), "x": round(px, 5), "y": round(py, 5), "z": round(pz, 5), **({"cap": True, "capRing": sample["capRing"]} if sample.get("cap") else {})})
    fraction = exposed_area / total_area if total_area > 0.0 else 0.0
    report = {
        "schemaVersion": 1,
        "kind": "polykit.scalp-exposure",
        "exposedFraction": round(fraction, 6),
        "exposedSamples": exposed,
        "sampleCount": len(field.surface_samples(u_samples, v_samples, v_range)),
        "hairPointCount": len(points),
        "hairPointsInsideSkull": len(points) - len(outside),
        "reach": round(reach_distance, 6),
        "lateral": round(lateral_distance, 6),
        "vRange": [round(v_range[0], 6), round(v_range[1], 6)],
        "hardMax": round(float(hard_max), 6),
        "hardMaxUncalibrated": True,
        "verdict": "fail" if fraction > hard_max else "pass",
        "note": "Area-weighted scalp fraction without hair standing outside the skull; hair inside the skull is excluded from coverage.",
    }
    report["largestExposedRun"] = largest_exposed_run(report, u_samples)
    return report
