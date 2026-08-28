from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GrasslandConfig:
    """Parameters for the standalone Blender grassland benchmark."""

    size: float = 900.0
    resolution: int = 257
    seed: int = 73

    # Environment density is points / m^2. Keep preview values practical.
    grass_density: float = 0.95
    flower_density: float = 0.014
    rock_density: float = 0.0045
    shrub_density: float = 0.0025

    grass_height: float = 1.05
    grass_scale_variation: float = 0.42
    wind_strength: float = 0.20
    wind_speed: float = 1.25
    wind_scale: float = 0.025

    # Terrain rules.
    max_grass_slope: float = 0.58
    rock_slope_start: float = 0.48
    valley_green_strength: float = 0.28

    # Render defaults.
    render_resolution: int = 896

    @classmethod
    def preview(cls, *, seed: int = 73) -> "GrasslandConfig":
        return cls(
            size=700.0,
            resolution=193,
            seed=seed,
            grass_density=0.62,
            flower_density=0.010,
            rock_density=0.0035,
            shrub_density=0.0017,
            render_resolution=768,
        )

    @classmethod
    def quality(cls, *, seed: int = 73) -> "GrasslandConfig":
        return cls(
            size=1000.0,
            resolution=321,
            seed=seed,
            grass_density=1.55,
            flower_density=0.020,
            rock_density=0.006,
            shrub_density=0.0032,
            render_resolution=1024,
        )
