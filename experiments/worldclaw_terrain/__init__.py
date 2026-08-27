"""Minimal Blender-first WorldClaw terrain capability prototype."""

from .regions import BackgroundRegion, BoxRegion, CircleRegion, SplineRegion, TerrainRegion
from .terrain import BuildStats, Terrain

__all__ = [
    "BackgroundRegion",
    "BoxRegion",
    "BuildStats",
    "CircleRegion",
    "SplineRegion",
    "Terrain",
    "TerrainRegion",
]
