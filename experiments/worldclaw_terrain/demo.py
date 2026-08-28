"""Reference scenes for validating the WorldClaw terrain prototype in Blender."""
from __future__ import annotations

from pathlib import Path

from .materials import VolcanicMaterialSettings
from .regions import (
    BackgroundRegion,
    BoxRegion,
    CircleRegion,
    LavaFlowRegion,
    SplineRegion,
    VolcanoRegion,
)
from .terrain import Terrain


def build_demo(*, resolution: int = 257, seed: int = 42) -> Terrain:
    """Build the original 1 km mountain/grassland/river validation scene."""
    terrain = Terrain(size=1024.0, resolution=resolution, seed=seed)

    terrain.add_region(
        BackgroundRegion(
            id="south_plain",
            base_height=7.0,
            noise_amplitude=2.5,
            noise_scale=180.0,
            octaves=4,
            color=(0.38, 0.34, 0.18, 1.0),
            rock_strength=0.18,
        )
    )
    terrain.add_region(
        BoxRegion(
            id="central_grassland",
            kind="grassland",
            center=(0.0, -25.0),
            size=(860.0, 650.0),
            blend_width=105.0,
            mask_bias=-0.05,
            base_height=24.0,
            noise_amplitude=8.0,
            noise_scale=165.0,
            octaves=5,
            color=(0.16, 0.32, 0.09, 1.0),
            rock_strength=0.20,
        )
    )
    terrain.add_region(
        CircleRegion(
            id="north_mountains",
            kind="mountain",
            center=(0.0, 275.0),
            radius=365.0,
            blend_width=72.0,
            mask_bias=0.15,
            base_height=135.0,
            noise_amplitude=30.0,
            noise_scale=150.0,
            octaves=6,
            lacunarity=2.05,
            gain=0.53,
            ridge_strength=88.0,
            ridge_scale=78.0,
            color=(0.34, 0.33, 0.31, 1.0),
            rock_strength=0.85,
        )
    )
    terrain.add_region(
        SplineRegion(
            id="north_south_river",
            kind="river",
            points=(
                (-95.0, 500.0),
                (-58.0, 365.0),
                (-85.0, 230.0),
                (-18.0, 105.0),
                (35.0, -30.0),
                (18.0, -170.0),
                (92.0, -330.0),
                (145.0, -500.0),
            ),
            width=92.0,
            blend_width=24.0,
            mask_bias=1.35,
            base_height=8.0,
            channel_depth=12.0,
            noise_amplitude=1.6,
            noise_scale=95.0,
            octaves=3,
            color=(0.055, 0.18, 0.31, 1.0),
            rock_strength=0.08,
        )
    )

    stats = terrain.build()
    terrain.setup_diagnostics()
    _print_stats("WorldClaw terrain", stats)
    return terrain


def build_volcano_demo(
    *,
    resolution: int = 257,
    seed: int = 73,
    scatter_rocks: bool = False,
    rock_count: int = 140,
) -> Terrain:
    """Build a demanding volcanic scene for material/geomorph validation."""
    material = VolcanicMaterialSettings(
        emission_strength=8.5,
        bump_strength=0.46,
        bump_distance=0.28,
        macro_scale=3.7,
        detail_scale=36.0,
        crack_scale=21.0,
    )
    terrain = Terrain(
        size=1400.0,
        resolution=resolution,
        seed=seed,
        name="WorldClawTerrain_DeathMountain",
        material_profile="volcanic",
        volcanic_material=material,
        compositor_glow=True,
    )

    terrain.add_region(
        BackgroundRegion(
            id="volcanic_plain",
            kind="volcanic_plain",
            base_height=4.0,
            noise_amplitude=5.5,
            noise_scale=210.0,
            octaves=5,
            color=(0.075, 0.063, 0.052, 1.0),
            ash_strength=0.72,
            rock_strength=0.48,
        )
    )
    terrain.add_region(
        VolcanoRegion(
            id="main_volcano",
            center=(0.0, 165.0),
            radius=520.0,
            blend_width=92.0,
            mask_bias=0.42,
            base_height=22.0,
            cone_height=305.0,
            cone_power=1.44,
            crater_radius=112.0,
            crater_depth=92.0,
            rim_height=42.0,
            rim_width=30.0,
            noise_amplitude=18.0,
            noise_scale=170.0,
            ridge_strength=64.0,
            ridge_scale=92.0,
            radial_noise_amplitude=24.0,
            radial_noise_scale=125.0,
            octaves=6,
            lacunarity=2.08,
            gain=0.52,
            color=(0.045, 0.038, 0.033, 1.0),
            heat_strength=0.03,
            crater_heat=0.88,
            ash_strength=0.86,
            rock_strength=0.98,
        )
    )

    lava_color = (0.95, 0.055, 0.004, 1.0)
    terrain.add_region(
        LavaFlowRegion(
            id="lava_west",
            points=(
                (-34.0, 172.0),
                (-88.0, 92.0),
                (-150.0, -5.0),
                (-235.0, -120.0),
                (-330.0, -245.0),
                (-430.0, -420.0),
                (-525.0, -620.0),
            ),
            width=82.0,
            blend_width=17.0,
            mask_bias=0.55,
            flow_thickness=8.5,
            incision_depth=4.8,
            levee_height=6.5,
            levee_position=0.74,
            levee_width=0.13,
            heat_strength=1.0,
            heat_falloff=0.60,
            color=lava_color,
        )
    )
    terrain.add_region(
        LavaFlowRegion(
            id="lava_south",
            points=(
                (15.0, 170.0),
                (38.0, 80.0),
                (58.0, -30.0),
                (112.0, -145.0),
                (158.0, -285.0),
                (145.0, -430.0),
                (205.0, -620.0),
            ),
            width=74.0,
            blend_width=16.0,
            mask_bias=0.52,
            flow_thickness=7.5,
            incision_depth=4.0,
            levee_height=5.8,
            heat_strength=0.92,
            heat_falloff=0.66,
            color=lava_color,
        )
    )
    terrain.add_region(
        LavaFlowRegion(
            id="lava_east",
            points=(
                (58.0, 188.0),
                (132.0, 125.0),
                (220.0, 78.0),
                (330.0, 52.0),
                (455.0, 35.0),
                (610.0, 18.0),
            ),
            width=62.0,
            blend_width=14.0,
            mask_bias=0.48,
            flow_thickness=6.2,
            incision_depth=3.2,
            levee_height=5.0,
            heat_strength=0.80,
            heat_falloff=0.72,
            color=lava_color,
        )
    )

    stats = terrain.build()
    terrain.setup_diagnostics()
    if scatter_rocks:
        rocks = terrain.scatter_rocks(count=rock_count)
        print(f"WorldClaw volcanic rock instances: {len(rocks)}")
    _print_stats("WorldClaw volcanic terrain", stats)
    return terrain


def _print_stats(label, stats) -> None:
    print(
        f"{label} built:",
        f"{stats.vertices} vertices, {stats.faces} faces,",
        f"height {stats.min_height:.2f}..{stats.max_height:.2f} m,",
        f"slope01 max {stats.max_slope:.3f}, lava max {stats.max_lava_heat:.3f}",
    )


def render_demo(
    output_dir: str | Path | None = None,
    *,
    resolution: int = 768,
    terrain_resolution: int = 257,
    seed: int = 42,
) -> tuple[Terrain, dict[str, str]]:
    terrain = build_demo(resolution=terrain_resolution, seed=seed)
    paths = terrain.render_diagnostics(output_dir=output_dir, resolution=resolution)
    for label, path in paths.items():
        print(f"{label}: {path}")
    return terrain, paths


def render_volcano_demo(
    output_dir: str | Path | None = None,
    *,
    resolution: int = 896,
    terrain_resolution: int = 257,
    seed: int = 73,
    scatter_rocks: bool = False,
) -> tuple[Terrain, dict[str, str]]:
    terrain = build_volcano_demo(
        resolution=terrain_resolution,
        seed=seed,
        scatter_rocks=scatter_rocks,
    )
    paths = terrain.render_diagnostics(output_dir=output_dir, resolution=resolution)
    for label, path in paths.items():
        print(f"{label}: {path}")
    return terrain, paths
