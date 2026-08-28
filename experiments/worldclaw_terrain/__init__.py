"""Blender-first WorldClaw terrain capability prototype."""

from .regions import (
    BackgroundRegion,
    BoxRegion,
    CircleRegion,
    LavaSplineRegion,
    SplineRegion,
    TerrainRegion,
    VolcanoRegion,
)
from .styles import (
    DEFAULT_STYLIZED_STYLE,
    VOLCANIC_STYLIZED_STYLE,
    StylizedTerrainStyle,
)
from .terrain import BuildStats, Terrain

__all__ = [
    "BackgroundRegion",
    "BoxRegion",
    "BuildStats",
    "CircleRegion",
    "DEFAULT_STYLIZED_STYLE",
    "LavaSplineRegion",
    "SplineRegion",
    "StylizedTerrainStyle",
    "Terrain",
    "TerrainRegion",
    "VOLCANIC_STYLIZED_STYLE",
    "VolcanoRegion",
]
