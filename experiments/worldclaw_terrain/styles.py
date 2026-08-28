"""Visual profiles for Blender-first stylized terrain rendering.

The terrain generator owns spatial semantics and geometry.  These profiles only
control how those fields are interpreted by the Blender material.  Keeping the
style data separate makes the same region/height pipeline usable for bright
adventure worlds, volcanic zones, deserts, snow fields, and other game-oriented
biomes without baking a photorealistic material model into the terrain math.
"""
from __future__ import annotations

from dataclasses import dataclass

Color = tuple[float, float, float, float]


@dataclass(kw_only=True)
class StylizedTerrainStyle:
    """Parameters for a readable, game-oriented procedural terrain material.

    The defaults intentionally favor broad color grouping and restrained
    micro-detail.  They are meant for stylized third-person game worlds rather
    than photorealistic scans.
    """

    name: str = "stylized_adventure"

    # Broad color breakup. Generated coordinates keep the pattern independent
    # from the world size and avoid needing a UV unwrap for the prototype.
    macro_noise_scale: float = 3.2
    macro_noise_detail: float = 2.0
    macro_noise_roughness: float = 0.52
    macro_shadow: float = 0.82
    macro_mid: float = 0.98
    macro_highlight: float = 1.10

    # Surface detail stays intentionally modest so silhouettes remain readable.
    micro_noise_scale: float = 22.0
    micro_noise_detail: float = 2.2
    micro_noise_roughness: float = 0.58
    bump_strength: float = 0.24
    bump_distance: float = 0.70

    # Stylized rock breakup. Voronoi is used mainly as graphic fracture language,
    # not as a literal physically accurate basalt simulation.
    crack_scale: float = 17.0
    crack_width: float = 0.055
    crack_bump_strength: float = 0.34

    # Clean slope-based material grouping is useful for game readability.
    slope_rock_start: float = 0.34
    slope_rock_end: float = 0.66
    slope_rock_strength: float = 0.62
    rock_tint: Color = (0.19, 0.19, 0.22, 1.0)

    # Ash/dust is a secondary broad layer, not a realistic particle deposit sim.
    ash_strength: float = 0.72
    ash_tint: Color = (0.34, 0.31, 0.31, 1.0)

    # Roughness variation is deliberately compressed to keep a coherent art style.
    base_roughness: float = 0.57
    roughness_noise_strength: float = 0.13
    ash_roughness_boost: float = 0.18
    lava_roughness_drop: float = 0.28
    min_roughness: float = 0.28
    max_roughness: float = 0.92

    # Lava is designed as a readable hot core with darker cooling edges/cracks.
    lava_surface_mix: float = 0.93
    lava_emission_strength: float = 5.5
    lava_cool: Color = (0.18, 0.012, 0.004, 1.0)
    lava_red: Color = (0.95, 0.035, 0.006, 1.0)
    lava_orange: Color = (1.0, 0.22, 0.012, 1.0)
    lava_hot: Color = (1.0, 0.83, 0.18, 1.0)
    lava_core: Color = (1.0, 0.98, 0.62, 1.0)

    # Scene lighting defaults for a colorful third-person game presentation.
    sun_energy: float = 2.4
    sun_angle_degrees: float = 10.0
    fill_energy: float = 550.0
    world_color: Color = (0.045, 0.075, 0.14, 1.0)
    world_strength: float = 0.55


DEFAULT_STYLIZED_STYLE = StylizedTerrainStyle()

VOLCANIC_STYLIZED_STYLE = StylizedTerrainStyle(
    name="stylized_volcanic",
    macro_noise_scale=3.8,
    macro_shadow=0.74,
    macro_mid=0.92,
    macro_highlight=1.06,
    micro_noise_scale=26.0,
    bump_strength=0.30,
    bump_distance=0.82,
    crack_scale=20.0,
    crack_width=0.048,
    crack_bump_strength=0.46,
    slope_rock_start=0.27,
    slope_rock_end=0.58,
    slope_rock_strength=0.78,
    rock_tint=(0.115, 0.105, 0.125, 1.0),
    ash_strength=0.82,
    ash_tint=(0.28, 0.255, 0.27, 1.0),
    base_roughness=0.61,
    lava_emission_strength=6.5,
    lava_surface_mix=0.97,
    world_color=(0.035, 0.045, 0.085, 1.0),
    world_strength=0.42,
)
