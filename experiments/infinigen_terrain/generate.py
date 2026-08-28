"""Generate portable terrain packages with Infinigen's real terrain asset code.

Run this on Linux from an environment where Infinigen terrain support is
installed. The resulting .npz package is intentionally Blender-version-neutral
and can be imported by Blender 5.2 on Windows.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

import numpy as np

from .package import save_terrain_package


PRESET_KEYS = (
    "multi_mountains",
    "canyon",
    "canyons",
    "cliff",
    "mesa",
    "mountain",
    "river",
    "volcano",
    "coast",
)

BENCHMARK_PRESETS = (
    "multi_mountains",
    "canyons",
    "cliff",
    "river",
)


def _load_infinigen_api():
    try:
        import infinigen  # type: ignore
        from infinigen.core.util.math import FixedSeed, int_hash  # type: ignore
        from infinigen.core.util.organization import LandTile, Process  # type: ignore
        from infinigen.terrain.assets.landtiles import assets_to_data, landtile_asset  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on external install.
        raise RuntimeError(
            "Infinigen terrain is not available. Run this script on Linux from "
            "an Infinigen environment installed with terrain support."
        ) from exc

    presets = {
        "multi_mountains": LandTile.MultiMountains,
        "canyon": LandTile.Canyon,
        "canyons": LandTile.Canyons,
        "cliff": LandTile.Cliff,
        "mesa": LandTile.Mesa,
        "mountain": LandTile.Mountain,
        "river": LandTile.River,
        "volcano": LandTile.Volcano,
        "coast": LandTile.Coast,
    }
    return infinigen, FixedSeed, int_hash, Process, assets_to_data, landtile_asset, presets


def generate_one(
    *,
    preset_key: str,
    seed: int,
    resolution: int,
    output_dir: Path,
    cache_dir: Path,
    device: str,
    force: bool,
) -> Path:
    (
        infinigen,
        FixedSeed,
        int_hash,
        Process,
        assets_to_data,
        landtile_asset,
        presets,
    ) = _load_infinigen_api()

    if preset_key not in presets:
        raise ValueError(f"unsupported Infinigen preset: {preset_key}")
    if resolution < 128:
        raise ValueError("resolution below 128 is not useful for this benchmark")

    preset = presets[preset_key]
    asset_folder = cache_dir / preset_key / f"seed_{seed}_r{resolution}"
    if force and asset_folder.exists():
        shutil.rmtree(asset_folder)
    asset_folder.parent.mkdir(parents=True, exist_ok=True)

    finish = asset_folder / "FINISH"
    # Infinigen's exact finish filename is versioned through AssetFile, so the
    # folder itself is used as the stable cache identity. Re-running its asset
    # function is safe when the caller requests --force; otherwise reuse any
    # existing generated fields if they can be read.
    need_generate = not asset_folder.exists() or not any(asset_folder.iterdir())
    if need_generate:
        print(
            f"[infinigen] generating {preset_key} seed={seed} "
            f"resolution={resolution} device={device}"
        )
        asset_folder.mkdir(parents=True, exist_ok=True)
        with FixedSeed(int_hash([preset, int(seed), 0])):
            landtile_asset(
                asset_folder,
                preset,
                resolution=int(resolution),
                device=device,
            )
    else:
        print(f"[infinigen] reusing cached asset {asset_folder}")

    tile_size, raw_resolution, raw_data = assets_to_data(
        asset_folder,
        None,
        N=int(resolution),
        do_smooth=False,
    )
    raw = np.asarray(raw_data["heightmap"], dtype=np.float32).reshape(
        (raw_resolution, raw_resolution)
    )

    eroded = None
    erosion_mask = None
    erosion_error = None
    try:
        _, erosion_resolution, erosion_data = assets_to_data(
            asset_folder,
            Process.Erosion,
            N=int(resolution),
            do_smooth=False,
        )
        eroded = np.asarray(erosion_data["heightmap"], dtype=np.float32).reshape(
            (erosion_resolution, erosion_resolution)
        )
        erosion_mask = np.asarray(erosion_data["mask"], dtype=np.float32).reshape(
            (erosion_resolution, erosion_resolution)
        )
    except Exception as exc:  # Some custom land tiles may not emit erosion fields.
        erosion_error = f"{type(exc).__name__}: {exc}"
        print(f"[infinigen] erosion field unavailable: {erosion_error}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"infinigen_{preset_key}_seed{seed}_r{resolution}.npz"
    metadata = {
        "generator": "Infinigen",
        "infinigen_version": str(getattr(infinigen, "__version__", "unknown")),
        "preset_key": preset_key,
        "device": device,
        "cache_dir": str(asset_folder),
        "erosion_error": erosion_error,
    }
    save_terrain_package(
        output,
        preset=preset_key,
        seed=seed,
        tile_size_m=float(tile_size),
        raw_height=raw,
        eroded_height=eroded,
        erosion_mask=erosion_mask,
        metadata=metadata,
    )
    print(f"[infinigen] package: {output}")
    print(
        f"[infinigen] raw z={float(raw.min()):.2f}..{float(raw.max()):.2f}m, "
        f"tile={float(tile_size):.1f}m"
    )
    if eroded is not None:
        print(
            f"[infinigen] eroded z={float(eroded.min()):.2f}.."
            f"{float(eroded.max()):.2f}m"
        )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Infinigen terrain packages for Blender evaluation."
    )
    parser.add_argument(
        "--preset",
        action="append",
        choices=PRESET_KEYS,
        help="Preset to generate. Repeat for multiple presets.",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Generate the complex benchmark suite: multi_mountains/canyons/cliff/river.",
    )
    parser.add_argument("--seed", type=int, default=73)
    parser.add_argument("--resolution", type=int, default=1024)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".artifacts/infinigen-terrain"),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/infinigen-terrain"),
    )
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    presets = list(args.preset or ())
    if args.benchmark:
        for preset in BENCHMARK_PRESETS:
            if preset not in presets:
                presets.append(preset)
    if not presets:
        presets = ["multi_mountains"]

    try:
        for offset, preset in enumerate(presets):
            generate_one(
                preset_key=preset,
                seed=args.seed + offset,
                resolution=args.resolution,
                output_dir=args.output_dir,
                cache_dir=args.cache_dir,
                device=args.device,
                force=args.force,
            )
    except Exception as exc:
        print(f"[infinigen] generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
