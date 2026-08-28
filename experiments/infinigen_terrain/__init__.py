"""Infinigen terrain experiment bridge.

This package keeps Infinigen as an external, pinned dependency. Linux generates
terrain packages with Infinigen's real terrain code; Blender 5.2 on Windows can
then import the package for visual evaluation and later MCP editing.
"""

from .package import TerrainPackage, derive_surface_fields, load_terrain_package

__all__ = ["TerrainPackage", "derive_surface_fields", "load_terrain_package"]
