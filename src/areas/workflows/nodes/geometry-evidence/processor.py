"""Shape-specific geometry evidence process nodes."""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from typing import Any, Sequence


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _eigenvalues(matrix: list[list[float]]) -> tuple[float, float, float]:
    p1 = matrix[0][1] ** 2 + matrix[0][2] ** 2 + matrix[1][2] ** 2
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if p1 == 0.0:
        return tuple(sorted((matrix[0][0], matrix[1][1], matrix[2][2]), reverse=True))  # type: ignore[return-value]
    q = trace / 3.0
    p2 = sum((matrix[index][index] - q) ** 2 for index in range(3)) + 2.0 * p1
    scale = math.sqrt(p2 / 6.0)
    normalised = [[(matrix[row][column] - (q if row == column else 0.0)) / scale for column in range(3)] for row in range(3)]
    determinant = (
        normalised[0][0] * (normalised[1][1] * normalised[2][2] - normalised[1][2] * normalised[2][1])
        - normalised[0][1] * (normalised[1][0] * normalised[2][2] - normalised[1][2] * normalised[2][0])
        + normalised[0][2] * (normalised[1][0] * normalised[2][1] - normalised[1][1] * normalised[2][0])
    )
    phi = math.acos(max(-1.0, min(1.0, determinant / 2.0))) / 3.0
    first = q + 2.0 * scale * math.cos(phi)
    third = q + 2.0 * scale * math.cos(phi + 2.0 * math.pi / 3.0)
    return tuple(sorted((first, trace - first - third, third), reverse=True))  # type: ignore[return-value]


def _best_fit_plane(points: list[tuple[float, float, float]]) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], float]:
    origin = tuple(_mean([point[axis] for point in points]) for axis in range(3))
    xx = xy = xz = yy = yz = zz = 0.0
    for point in points:
        delta = [point[axis] - origin[axis] for axis in range(3)]
        xx += delta[0] * delta[0]
        xy += delta[0] * delta[1]
        xz += delta[0] * delta[2]
        yy += delta[1] * delta[1]
        yz += delta[1] * delta[2]
        zz += delta[2] * delta[2]
    covariance = [[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]]
    first, second, third = _eigenvalues(covariance)
    planarity = (second - third) / second if second > 1e-15 else 0.0
    trace = xx + yy + zz
    shifted = [[(trace if row == column else 0.0) - covariance[row][column] for column in range(3)] for row in range(3)]
    vector = [1.0, 0.37, 0.11]
    for _ in range(200):
        next_vector = [sum(shifted[row][column] * vector[column] for column in range(3)) for row in range(3)]
        length = math.sqrt(sum(value * value for value in next_vector))
        if length < 1e-15:
            break
        vector = [value / length for value in next_vector]
    normal = tuple(vector)
    seed = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    u_raw = (seed[1] * normal[2] - seed[2] * normal[1], seed[2] * normal[0] - seed[0] * normal[2], seed[0] * normal[1] - seed[1] * normal[0])
    u_length = math.sqrt(sum(value * value for value in u_raw)) or 1.0
    u = tuple(value / u_length for value in u_raw)
    v = (normal[1] * u[2] - normal[2] * u[1], normal[2] * u[0] - normal[0] * u[2], normal[0] * u[1] - normal[1] * u[0])
    return origin, u, v, normal, planarity


def _fit_arc_centre(points: list[tuple[float, float]]) -> tuple[tuple[float, float], float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    low_x, high_x = min(xs) - span, max(xs) + span
    low_y, high_y = min(ys) - span, max(ys) + span
    best = (_mean(xs), _mean(ys))
    best_spread = float("inf")
    for _ in range(6):
        step_x, step_y = (high_x - low_x) / 12.0, (high_y - low_y) / 12.0
        for i in range(13):
            for j in range(13):
                centre = (low_x + i * step_x, low_y + j * step_y)
                radii = [math.hypot(x - centre[0], y - centre[1]) for x, y in points]
                mean = _mean(radii)
                spread = math.sqrt(_mean([(radius - mean) ** 2 for radius in radii]))
                if spread < best_spread:
                    best, best_spread = centre, spread
        low_x, high_x = best[0] - step_x, best[0] + step_x
        low_y, high_y = best[1] - step_y, best[1] + step_y
    return best, best_spread


def _angular_span(points: list[tuple[float, float]], centre: tuple[float, float]) -> float:
    angles = sorted(math.degrees(math.atan2(y - centre[1], x - centre[0])) for x, y in points)
    if len(angles) < 2:
        return 0.0
    gaps = [angles[index + 1] - angles[index] for index in range(len(angles) - 1)]
    gaps.append(angles[0] + 360.0 - angles[-1])
    return round(360.0 - max(gaps), 4)


def _measure(points: list[tuple[float, float, float]]) -> dict[str, Any]:
    origin, u, v, normal, planarity = _best_fit_plane(points)
    planar: list[tuple[float, float]] = []
    off_plane: list[float] = []
    for point in points:
        delta = tuple(point[axis] - origin[axis] for axis in range(3))
        planar.append((sum(delta[axis] * u[axis] for axis in range(3)), sum(delta[axis] * v[axis] for axis in range(3))))
        off_plane.append(abs(sum(delta[axis] * normal[axis] for axis in range(3))))
    centre, spread = _fit_arc_centre(planar)
    radii = [math.hypot(point[0] - centre[0], point[1] - centre[1]) for point in planar]
    bend_radius = _mean(radii)
    extent = max(max(point[0] for point in planar) - min(point[0] for point in planar), max(point[1] for point in planar) - min(point[1] for point in planar)) or 1.0
    centre_distance = math.hypot(centre[0] - _mean([point[0] for point in planar]), centre[1] - _mean([point[1] for point in planar]))
    return {
        "sampledVertexCount": len(points),
        "planeNormal": [round(component, 5) for component in normal],
        "planarity": round(planarity, 6),
        "maxOffPlaneDistance": round(max(off_plane), 6),
        "bendRadius": round(bend_radius, 6),
        "radiusSpread": round(spread, 6),
        "radiusSpreadOverBendRadius": round(spread / bend_radius, 6) if bend_radius > 1e-12 else None,
        "angularSpanDeg": _angular_span(planar, centre),
        "centreDistanceOverExtent": round(centre_distance / extent, 6),
        "planarExtent": round(extent, 6),
    }


def _check(name: str, value: Any, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "value": value, "status": "pass" if passed else "fail", "detail": detail}


def _audit(descriptor: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    raw_points = descriptor.get("points")
    if not isinstance(raw_points, list) or len(raw_points) < 6:
        raise ValueError("swept-arc-audit requires at least six points")
    points: list[tuple[float, float, float]] = []
    for index, raw_point in enumerate(raw_points):
        if not isinstance(raw_point, list) or len(raw_point) != 3 or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) for value in raw_point):
            raise ValueError(f"points[{index}] must be a finite [x,y,z] point")
        points.append(tuple(float(value) for value in raw_point))  # type: ignore[assignment]
    measured = _measure(points)
    expectations = descriptor.get("expectations") if isinstance(descriptor.get("expectations"), dict) else {}
    def number(name: str, default: float) -> float:
        value = params.get(name, expectations.get(name, default))
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    checks = [
        _check("planeDetermined", measured["planarity"], measured["planarity"] >= number("min_planarity", 0.35), "the point cloud must determine a stable sweep plane"),
        _check("angularSpan", measured["angularSpanDeg"], measured["angularSpanDeg"] >= number("min_angular_span_deg", 90.0), "a curved hook must occupy a meaningful angular span"),
        _check("centreDistance", measured["centreDistanceOverExtent"], measured["centreDistanceOverExtent"] <= number("max_centre_distance_over_extent", 2.0), "the fitted centre cannot be pushed arbitrarily far away to make a straight rod look circular"),
        _check("arcResidual", measured["radiusSpreadOverBendRadius"], measured["radiusSpreadOverBendRadius"] is not None and measured["radiusSpreadOverBendRadius"] <= number("max_radius_spread_ratio", 0.15), "radial spread relative to bend radius must stay bounded"),
    ]
    expected_radius = number("bend_radius", 0.0)
    if expected_radius > 0.0:
        tolerance = number("bend_radius_tolerance", max(0.1 * expected_radius, 1e-6))
        checks.append(_check("bendRadius", measured["bendRadius"], abs(measured["bendRadius"] - expected_radius) <= tolerance, f"bend radius must be within {tolerance:g} of the expected {expected_radius:g}"))
    failures = [check for check in checks if check["status"] == "fail"]
    return {
        "schemaVersion": 1,
        "kind": "polykit.swept-arc-audit",
        "status": "fail" if failures else "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "passed": not failures,
        "measured": measured,
        "checks": checks,
        "failureCount": len(failures),
        "reviewNotes": [
            "The audit measures executed points, not the authoring intent of a curve node.",
            "A short, thick rod can make a fitted plane arbitrary; keep planeDetermined visible even when other numbers look plausible.",
        ],
    }


def main() -> None:
    try:
        payload = json.loads(sys.stdin.readline())
        input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        node_id = str(params.get("_node_id") or "swept-arc-audit")
        if node_id != "swept-arc-audit":
            error(f"geometry-evidence: unsupported node {node_id!r}")
            return
        text = input_data.get("text")
        if not isinstance(text, str) or not text.strip():
            error("geometry-evidence: swept-arc-audit requires a JSON text descriptor")
            return
        descriptor = json.loads(text)
        if not isinstance(descriptor, dict):
            raise ValueError("swept arc descriptor must be a JSON object")
        progress(10, "Fitting swept arc geometry…")
        report = _audit(descriptor, params)
        progress(90, "Writing geometry evidence…")
        progress(100, "Geometry evidence ready")
        emit({"type": "done", "result": {"text": json.dumps(report, ensure_ascii=False, indent=2), "metadata": {"evidence_kind": "swept-arc-audit", "schema_version": 1, "status": report["status"], "failure_count": report["failureCount"]}}})
    except Exception as exc:
        error(f"geometry-evidence: {exc}")


if __name__ == "__main__":
    main()
