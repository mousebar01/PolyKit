from __future__ import annotations

import unittest

from .regions import BackgroundRegion, LavaSplineRegion, VolcanoRegion


class TerrainRegionTests(unittest.TestCase):
    def test_background_is_neutral(self) -> None:
        region = BackgroundRegion(id="plain", base_height=7.0)
        self.assertEqual(region.signed_distance(12.0, -4.0), 0.0)
        self.assertAlmostEqual(region.local_height(0.0, 0.0, seed=1), 7.0)

    def test_volcano_has_crater_below_rim(self) -> None:
        region = VolcanoRegion(
            id="volcano",
            center=(0.0, 0.0),
            radius=400.0,
            base_height=20.0,
            cone_height=220.0,
            crater_radius=70.0,
            crater_depth=100.0,
            rim_height=30.0,
            rim_width=22.0,
            noise_amplitude=0.0,
            ridge_strength=0.0,
        )
        center = region.local_height(0.0, 0.0, seed=1)
        rim = region.local_height(70.0, 0.0, seed=1)
        outer = region.local_height(360.0, 0.0, seed=1)
        self.assertGreater(rim, center)
        self.assertGreater(rim, outer)

    def test_lava_is_overlay_and_cuts_center(self) -> None:
        region = LavaSplineRegion(
            id="lava",
            points=((0.0, -100.0), (0.0, 100.0)),
            width=60.0,
            channel_depth=8.0,
            levee_height=4.0,
            noise_amplitude=0.0,
        )
        self.assertEqual(region.height_mode, "overlay")
        center = region.height_offset(0.0, 0.0, seed=1)
        bank = region.height_offset(22.0, 0.0, seed=1)
        outside = region.height_offset(80.0, 0.0, seed=1)
        self.assertLess(center, 0.0)
        self.assertGreater(bank, center)
        self.assertAlmostEqual(outside, 0.0, places=3)


if __name__ == "__main__":
    unittest.main()
