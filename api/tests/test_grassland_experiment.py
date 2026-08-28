from __future__ import annotations

import unittest

from experiments.grassland.config import GrasslandConfig
from experiments.grassland.terrain import sample_height


class GrasslandExperimentTests(unittest.TestCase):
    def test_preview_config_is_lighter_than_quality(self):
        preview = GrasslandConfig.preview(seed=1)
        quality = GrasslandConfig.quality(seed=1)
        self.assertLess(preview.resolution, quality.resolution)
        self.assertLess(preview.grass_density, quality.grass_density)
        self.assertLess(preview.size, quality.size)

    def test_height_is_deterministic(self):
        a = sample_height(21.5, -77.25, size=700.0, seed=73)
        b = sample_height(21.5, -77.25, size=700.0, seed=73)
        self.assertAlmostEqual(a, b, places=10)

    def test_benchmark_has_large_scale_height_structure(self):
        size = 700.0
        seed = 73
        samples = [
            sample_height(-0.27 * size, 0.06 * size, size=size, seed=seed),
            sample_height(0.0, -0.24 * size, size=size, seed=seed),
            sample_height(0.12 * size, 0.38 * size, size=size, seed=seed),
            sample_height(0.0, 0.0, size=size, seed=seed),
        ]
        self.assertGreater(max(samples) - min(samples), 20.0)


if __name__ == "__main__":
    unittest.main()
