"""Deterministic 2D noise helpers for the Blender terrain prototype.

The implementation is deliberately dependency-free so the exact same terrain
parameters produce the same samples inside Blender on Windows and in ordinary
Python tooling.
"""
from __future__ import annotations

import math


def _mix32(value: int) -> int:
    value &= 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x7FEB352D) & 0xFFFFFFFF
    value ^= value >> 15
    value = (value * 0x846CA68B) & 0xFFFFFFFF
    value ^= value >> 16
    return value & 0xFFFFFFFF


def _lattice_value(ix: int, iy: int, seed: int) -> float:
    mixed = _mix32(ix * 0x1F123BB5 ^ iy * 0x5F356495 ^ seed * 0x6C8E9CF5)
    return (mixed / 0xFFFFFFFF) * 2.0 - 1.0


def _smoothstep01(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def value_noise_2d(x: float, y: float, seed: int = 0) -> float:
    """Return smooth deterministic value noise in approximately ``[-1, 1]``."""
    x0 = math.floor(x)
    y0 = math.floor(y)
    tx = _smoothstep01(x - x0)
    ty = _smoothstep01(y - y0)

    v00 = _lattice_value(x0, y0, seed)
    v10 = _lattice_value(x0 + 1, y0, seed)
    v01 = _lattice_value(x0, y0 + 1, seed)
    v11 = _lattice_value(x0 + 1, y0 + 1, seed)

    a = _lerp(v00, v10, tx)
    b = _lerp(v01, v11, tx)
    return _lerp(a, b, ty)


def fbm_2d(
    x: float,
    y: float,
    *,
    seed: int = 0,
    octaves: int = 5,
    lacunarity: float = 2.0,
    gain: float = 0.5,
) -> float:
    """Fractal Brownian motion normalized to roughly ``[-1, 1]``."""
    amplitude = 1.0
    frequency = 1.0
    total = 0.0
    normalization = 0.0

    for octave in range(max(1, int(octaves))):
        total += amplitude * value_noise_2d(
            x * frequency,
            y * frequency,
            seed + octave * 1013,
        )
        normalization += amplitude
        frequency *= lacunarity
        amplitude *= gain

    return total / normalization if normalization else 0.0


def ridged_fbm_2d(
    x: float,
    y: float,
    *,
    seed: int = 0,
    octaves: int = 5,
    lacunarity: float = 2.0,
    gain: float = 0.5,
) -> float:
    """Return a normalized ``[0, 1]`` ridge field useful for mountain crests."""
    amplitude = 1.0
    frequency = 1.0
    total = 0.0
    normalization = 0.0

    for octave in range(max(1, int(octaves))):
        sample = value_noise_2d(
            x * frequency,
            y * frequency,
            seed + 7919 + octave * 1013,
        )
        ridge = 1.0 - abs(sample)
        ridge *= ridge
        total += amplitude * ridge
        normalization += amplitude
        frequency *= lacunarity
        amplitude *= gain

    return total / normalization if normalization else 0.0
