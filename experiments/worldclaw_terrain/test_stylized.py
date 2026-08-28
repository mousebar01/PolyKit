from __future__ import annotations

import unittest

from .stylized import StylizedVolcanoRegion, _smoothstep


class StylizedTerrainMathTests(unittest.TestCase):
    def test_smoothstep_is_bounded_and_monotonic(self) -> None:
        values = [_smoothstep(0.3, 0.7, x / 10.0) for x in range(11)]
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 1.0)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in values))
        self.assertTrue(all(a <= b for a, b in zip(values, values[1:])))

    def test_stylized_volcano_preserves_visible_crater(self) -> None:
        volcano = StylizedVolcanoRegion(
            id="volcano",
            center=(0.0, 0.0),
            radius=420.0,
            base_height=35.0,
            cone_height=235.0,
            cone_power=1.25,
            crater_radius=78.0,
            crater_depth=104.0,
            rim_height=32.0,
            rim_width=24.0,
            noise_amplitude=0.0,
            ridge_strength=0.0,
            radial_noise_amplitude=0.0,
            terrace_step=15.0,
            terrace_strength=0.10,
        )
        center = volcano.local_height(0.0, 0.0, seed=1)
        rim = volcano.local_height(78.0, 0.0, seed=1)
        shoulder = volcano.local_height(230.0, 0.0, seed=1)
        self.assertGreater(rim, center)
        self.assertGreater(rim, shoulder)


if __name__ == "__main__":
    unittest.main()
