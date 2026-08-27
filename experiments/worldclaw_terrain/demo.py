"""Reference terrain scene for validating the WorldClaw prototype in Blender."""
from __future__ import annotations

from pathlib import Path

from .regions import BackgroundRegion, BoxRegion, CircleRegion, SplineRegion
from .terrain import Terrain


def build_demo(*, resolution: int = 257, seed: int = 42) -> Terrain:
    """Build a 1 km terrain with mountains, grassland, river, and southern plain."""
    terrain = Terrain(size=1024.0, resolution=resolution, seed=seed)

    terrain.add_region(
        BackgroundRegion(
            id="south_plain",
            base_height=7.0,
            noise_amplitude=2.5,
            noise_scale=180.0,
            octaves=4,
            color=(0.38, 0.34, 0.18, 1.0),
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
        )
    )

    stats = terrain.build()
    terrain.setup_diagnostics()
    print(
        "WorldClaw terrain built:",
        f"{stats.vertices} vertices, {stats.faces} faces,",
        f"height {stats.min_height:.2f}..{stats.max_height:.2f} m",
    )
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
