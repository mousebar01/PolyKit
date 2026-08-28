"""Blender-first WorldClaw terrain capability prototype."""

from .materials import VolcanicMaterialSettings
from .regions import (
    BackgroundRegion,
    BoxRegion,
    CircleRegion,
    LavaFlowRegion,
    SplineRegion,
    TerrainRegion,
    VolcanoRegion,
)
from .surface import SurfaceSample
from .terrain import BuildStats, Terrain

__all__ = [
    "BackgroundRegion",
    "BoxRegion",
    "BuildStats",
    "CircleRegion",
    "LavaFlowRegion",
    "SplineRegion",
    "SurfaceSample",
    "Terrain",
    "TerrainRegion",
    "VolcanicMaterialSettings",
    "VolcanoRegion",
]
