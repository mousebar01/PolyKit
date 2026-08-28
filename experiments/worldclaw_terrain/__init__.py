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
from .stylized import StylizedTerrain, StylizedVolcanoRegion
from .stylized_materials import (
    STYLIZED_VOLCANIC_SETTINGS,
    StylizedMaterialSettings,
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
    "STYLIZED_VOLCANIC_SETTINGS",
    "StylizedMaterialSettings",
    "StylizedTerrain",
    "StylizedVolcanoRegion",
    "SurfaceSample",
    "Terrain",
    "TerrainRegion",
    "VolcanicMaterialSettings",
    "VolcanoRegion",
]
