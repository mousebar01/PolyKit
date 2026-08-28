"""Portable terrain package helpers shared by Linux generation and Blender import."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class TerrainPackage:
    path: Path
    preset: str
    seed: int
    tile_size_m: float
    source_resolution: int
    raw_height: np.ndarray
    eroded_height: np.ndarray | None
    erosion_mask: np.ndarray | None
    metadata: dict[str, Any]

    @property
    def best_height(self) -> np.ndarray:
        return self.eroded_height if self.eroded_height is not None else self.raw_height


def _as_square(array: np.ndarray, resolution: int) -> np.ndarray:
    result = np.asarray(array, dtype=np.float32)
    if result.ndim == 1:
        result = result.reshape((resolution, resolution))
    if result.shape != (resolution, resolution):
        raise ValueError(
            f"expected {(resolution, resolution)} terrain field, got {result.shape}"
        )
    return np.ascontiguousarray(result, dtype=np.float32)


def save_terrain_package(
    path: str | Path,
    *,
    preset: str,
    seed: int,
    tile_size_m: float,
    raw_height: np.ndarray,
    eroded_height: np.ndarray | None = None,
    erosion_mask: np.ndarray | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = np.asarray(raw_height, dtype=np.float32)
    if raw.ndim != 2 or raw.shape[0] != raw.shape[1]:
        raise ValueError("raw_height must be a square 2D array")
    resolution = int(raw.shape[0])

    payload: dict[str, np.ndarray] = {
        "raw_height": np.ascontiguousarray(raw),
        "tile_size_m": np.asarray([float(tile_size_m)], dtype=np.float32),
        "seed": np.asarray([int(seed)], dtype=np.int64),
        "source_resolution": np.asarray([resolution], dtype=np.int32),
        "preset": np.asarray([str(preset)]),
    }
    if eroded_height is not None:
        payload["eroded_height"] = _as_square(eroded_height, resolution)
    if erosion_mask is not None:
        payload["erosion_mask"] = _as_square(erosion_mask, resolution)

    np.savez_compressed(path, **payload)

    meta = {
        "format": "polykit.infinigen_terrain.v1",
        "preset": str(preset),
        "seed": int(seed),
        "tile_size_m": float(tile_size_m),
        "source_resolution": resolution,
        "has_eroded_height": eroded_height is not None,
        "has_erosion_mask": erosion_mask is not None,
    }
    if metadata:
        meta.update(metadata)
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path


def load_terrain_package(path: str | Path) -> TerrainPackage:
    path = Path(path)
    with np.load(path, allow_pickle=False) as data:
        resolution = int(data["source_resolution"][0])
        raw = _as_square(data["raw_height"], resolution)
        eroded = (
            _as_square(data["eroded_height"], resolution)
            if "eroded_height" in data.files
            else None
        )
        erosion_mask = (
            _as_square(data["erosion_mask"], resolution)
            if "erosion_mask" in data.files
            else None
        )
        preset = str(data["preset"][0])
        seed = int(data["seed"][0])
        tile_size_m = float(data["tile_size_m"][0])

    meta_path = path.with_suffix(".json")
    metadata: dict[str, Any] = {}
    if meta_path.exists():
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))

    return TerrainPackage(
        path=path,
        preset=preset,
        seed=seed,
        tile_size_m=tile_size_m,
        source_resolution=resolution,
        raw_height=raw,
        eroded_height=eroded,
        erosion_mask=erosion_mask,
        metadata=metadata,
    )


def resample_field(field: np.ndarray, target_resolution: int) -> np.ndarray:
    """Deterministic grid sampling without scipy/OpenCV dependencies."""
    source = np.asarray(field, dtype=np.float32)
    if source.ndim != 2 or source.shape[0] != source.shape[1]:
        raise ValueError("field must be a square 2D array")
    target_resolution = int(target_resolution)
    if target_resolution < 3:
        raise ValueError("target_resolution must be at least 3")
    if target_resolution == source.shape[0]:
        return np.ascontiguousarray(source)
    indices = np.linspace(0, source.shape[0] - 1, target_resolution)
    indices = np.rint(indices).astype(np.int32)
    return np.ascontiguousarray(source[np.ix_(indices, indices)], dtype=np.float32)


def smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    if edge1 <= edge0:
        raise ValueError("edge1 must be greater than edge0")
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def derive_surface_fields(height: np.ndarray, tile_size_m: float) -> dict[str, np.ndarray]:
    """Derive inexpensive evaluation fields from an imported heightfield.

    These fields are not intended to replace Infinigen's own semantic layers;
    they make the generated terrain easier to inspect in stock Blender.
    """
    h = np.asarray(height, dtype=np.float32)
    if h.ndim != 2 or h.shape[0] != h.shape[1]:
        raise ValueError("height must be a square 2D array")
    resolution = h.shape[0]
    spacing = float(tile_size_m) / max(1, resolution - 1)
    gy, gx = np.gradient(h, spacing, spacing)
    slope_radians = np.arctan(np.sqrt(gx * gx + gy * gy))
    slope01 = np.clip(slope_radians / math.radians(70.0), 0.0, 1.0).astype(np.float32)

    minimum = float(h.min())
    maximum = float(h.max())
    span = max(1e-6, maximum - minimum)
    height01 = ((h - minimum) / span).astype(np.float32)

    laplacian = np.gradient(gx, spacing, axis=1) + np.gradient(gy, spacing, axis=0)
    curvature_scale = float(np.percentile(np.abs(laplacian), 95.0))
    if curvature_scale <= 1e-8:
        curvature01 = np.zeros_like(h, dtype=np.float32)
    else:
        curvature01 = np.clip(
            0.5 + 0.5 * laplacian / curvature_scale,
            0.0,
            1.0,
        ).astype(np.float32)

    steep = smoothstep(0.42, 0.78, slope01)
    traversable = (1.0 - smoothstep(0.30, 0.64, slope01)).astype(np.float32)
    return {
        "height01": height01,
        "slope01": slope01,
        "curvature01": curvature01,
        "rock_mask": steep.astype(np.float32),
        "traversable_mask": traversable,
    }
