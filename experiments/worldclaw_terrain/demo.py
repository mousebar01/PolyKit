"""Reference scenes for validating the Blender-first WorldClaw terrain prototype."""
from __future__ import annotations

from pathlib import Path

from .regions import (
    BackgroundRegion,
    BoxRegion,
    CircleRegion,
    LavaSplineRegion,
    SplineRegion,
    VolcanoRegion,
)
from .styles import DEFAULT_STYLIZED_STYLE, VOLCANIC_STYLIZED_STYLE
from .terrain import Terrain


def _print_stats(label: str, terrain: Terrain) -> None:
    stats = terrain.last_stats
    if stats is None:
        return
    print(
        label,
        f"{stats.vertices} vertices, {stats.faces} faces,",
        f"height {stats.min_height:.2f}..{stats.max_height:.2f} m,",
        f"max slope {stats.max_slope_degrees:.1f} deg,",
        f"style={terrain.style.name}",
    )


def build_demo(*, resolution: int = 257, seed: int = 42) -> Terrain:
    """Build a colorful 1 km adventure terrain with mountain, grass, and river."""
    terrain = Terrain(
        size=1024.0,
        resolution=resolution,
        seed=seed,
        style=DEFAULT_STYLIZED_STYLE,
    )

    terrain.add_region(
        BackgroundRegion(
            id="south_plain",
            base_height=7.0,
            noise_amplitude=2.5,
            noise_scale=180.0,
            octaves=4,
            color=(0.42, 0.36, 0.17, 1.0),
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
            terrace_step=5.0,
            terrace_strength=0.08,
            color=(0.19, 0.43, 0.10, 1.0),
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
            base_height=118.0,
            noise_amplitude=25.0,
            noise_scale=150.0,
            octaves=6,
            lacunarity=2.05,
            gain=0.53,
            ridge_strength=72.0,
            ridge_scale=82.0,
            terrace_step=12.0,
            terrace_strength=0.10,
            color=(0.36, 0.35, 0.34, 1.0),
        )
    )

    # Rivers are overlays: they cut into the already-composed mountain/grassland
    # instead of forcing the strip toward an unrelated absolute base height.
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
            channel_depth=11.0,
            noise_amplitude=0.8,
            noise_scale=95.0,
            octaves=3,
            color=(0.045, 0.22, 0.44, 1.0),
        )
    )

    terrain.build()
    terrain.setup_diagnostics()
    _print_stats("WorldClaw stylized terrain built:", terrain)
    return terrain


def build_stylized_volcano_demo(
    *,
    resolution: int = 257,
    seed: int = 73,
) -> Terrain:
    """Build a game-oriented volcanic biome for material/terrain stress testing.

    The target is strong third-person readability: one dominant landmark,
    obvious crater, readable lava paths, broad color groups, and moderate surface
    detail. It intentionally does not pursue photorealistic geological fidelity.
    """
    terrain = Terrain(
        size=1280.0,
        resolution=resolution,
        seed=seed,
        name="WorldClawTerrain_StylizedVolcano",
        style=VOLCANIC_STYLIZED_STYLE,
    )

    terrain.add_region(
        BackgroundRegion(
            id="volcanic_lowlands",
            kind="plain",
            base_height=11.0,
            noise_amplitude=4.0,
            noise_scale=210.0,
            octaves=4,
            terrace_step=5.0,
            terrace_strength=0.08,
            color=(0.27, 0.22, 0.14, 1.0),
        )
    )

    terrain.add_region(
        CircleRegion(
            id="outer_badlands",
            kind="badlands",
            center=(0.0, 120.0),
            radius=535.0,
            blend_width=150.0,
            mask_bias=-0.15,
            base_height=35.0,
            noise_amplitude=12.0,
            noise_scale=190.0,
            ridge_strength=11.0,
            ridge_scale=105.0,
            terrace_step=10.0,
            terrace_strength=0.12,
            color=(0.29, 0.19, 0.13, 1.0),
        )
    )

    terrain.add_region(
        VolcanoRegion(
            id="main_volcano",
            center=(0.0, 145.0),
            radius=425.0,
            blend_width=82.0,
            mask_bias=0.38,
            base_height=42.0,
            cone_height=232.0,
            cone_power=1.24,
            crater_radius=72.0,
            crater_depth=96.0,
            rim_height=27.0,
            rim_width=24.0,
            noise_amplitude=15.0,
            noise_scale=145.0,
            octaves=5,
            lacunarity=2.0,
            gain=0.5,
            ridge_strength=42.0,
            ridge_scale=78.0,
            terrace_step=14.0,
            terrace_strength=0.14,
            color=(0.145, 0.125, 0.13, 1.0),
        )
    )

    # A material-only lava pool in the crater. height_mode='overlay' means it
    # contributes a mask/heat signal without replacing the crater geometry.
    terrain.add_region(
        CircleRegion(
            id="crater_lava",
            kind="lava",
            center=(0.0, 145.0),
            radius=58.0,
            blend_width=13.0,
            mask_bias=1.65,
            height_mode="overlay",
            color=(1.0, 0.16, 0.01, 1.0),
        )
    )

    lava_flows = (
        (
            "lava_south",
            (
                (5.0, 108.0),
                (24.0, 40.0),
                (5.0, -52.0),
                (45.0, -155.0),
                (92.0, -272.0),
                (132.0, -405.0),
                (170.0, -555.0),
            ),
            68.0,
        ),
        (
            "lava_southwest",
            (
                (-38.0, 112.0),
                (-92.0, 54.0),
                (-158.0, -28.0),
                (-245.0, -125.0),
                (-318.0, -246.0),
                (-392.0, -390.0),
            ),
            52.0,
        ),
        (
            "lava_east",
            (
                (42.0, 128.0),
                (103.0, 96.0),
                (175.0, 48.0),
                (255.0, -18.0),
                (345.0, -98.0),
                (455.0, -165.0),
            ),
            44.0,
        ),
    )
    for index, (region_id, points, width) in enumerate(lava_flows):
        terrain.add_region(
            LavaSplineRegion(
                id=region_id,
                points=points,
                width=width,
                blend_width=max(12.0, width * 0.28),
                mask_bias=1.8 - index * 0.12,
                channel_depth=5.5 if index == 0 else 4.0,
                levee_height=4.2 if index == 0 else 3.0,
                noise_amplitude=0.7,
                noise_scale=70.0,
                octaves=3,
                color=(1.0, 0.13, 0.006, 1.0),
            )
        )

    terrain.build()
    terrain.setup_diagnostics()
    _print_stats("WorldClaw stylized volcano built:", terrain)
    return terrain


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


def render_stylized_volcano_demo(
    output_dir: str | Path | None = None,
    *,
    resolution: int = 768,
    terrain_resolution: int = 257,
    seed: int = 73,
) -> tuple[Terrain, dict[str, str]]:
    terrain = build_stylized_volcano_demo(
        resolution=terrain_resolution,
        seed=seed,
    )
    paths = terrain.render_diagnostics(output_dir=output_dir, resolution=resolution)
    for label, path in paths.items():
        print(f"{label}: {path}")
    return terrain, paths
