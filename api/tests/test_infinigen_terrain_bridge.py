from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.infinigen_terrain.package import (  # noqa: E402
    derive_surface_fields,
    load_terrain_package,
    resample_field,
    save_terrain_package,
)


class InfinigenTerrainBridgeTests(unittest.TestCase):
    def test_round_trip_package_without_pickle(self):
        raw = np.arange(81, dtype=np.float32).reshape(9, 9)
        eroded = raw * 0.8
        erosion_mask = np.linspace(0.0, 1.0, 81, dtype=np.float32).reshape(9, 9)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "terrain.npz"
            save_terrain_package(
                path,
                preset="canyons",
                seed=17,
                tile_size_m=200.0,
                raw_height=raw,
                eroded_height=eroded,
                erosion_mask=erosion_mask,
                metadata={"generator": "test"},
            )
            package = load_terrain_package(path)

        self.assertEqual(package.preset, "canyons")
        self.assertEqual(package.seed, 17)
        self.assertEqual(package.source_resolution, 9)
        self.assertAlmostEqual(package.tile_size_m, 200.0)
        np.testing.assert_allclose(package.raw_height, raw)
        np.testing.assert_allclose(package.best_height, eroded)
        np.testing.assert_allclose(package.erosion_mask, erosion_mask)

    def test_resample_field_preserves_corners(self):
        source = np.arange(81, dtype=np.float32).reshape(9, 9)
        sampled = resample_field(source, 5)
        self.assertEqual(sampled.shape, (5, 5))
        self.assertEqual(float(sampled[0, 0]), float(source[0, 0]))
        self.assertEqual(float(sampled[-1, -1]), float(source[-1, -1]))

    def test_surface_fields_detect_steep_gradient(self):
        x = np.linspace(0.0, 40.0, 33, dtype=np.float32)
        height = np.tile(x, (33, 1))
        fields = derive_surface_fields(height, tile_size_m=64.0)
        self.assertEqual(fields["height01"].shape, height.shape)
        self.assertGreater(float(fields["slope01"].mean()), 0.4)
        self.assertLess(float(fields["traversable_mask"].mean()), 0.7)
        self.assertTrue(np.all(fields["curvature01"] >= 0.0))
        self.assertTrue(np.all(fields["curvature01"] <= 1.0))

    def test_blender_import_module_is_importable_without_blender(self):
        from experiments.infinigen_terrain import blender_import

        self.assertTrue(hasattr(blender_import, "import_terrain_package"))


if __name__ == "__main__":
    unittest.main()
