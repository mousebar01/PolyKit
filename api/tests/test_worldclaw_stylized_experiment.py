from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.worldclaw_terrain.stylized import StylizedVolcanoRegion, _smoothstep


class WorldClawStylizedExperimentTests(unittest.TestCase):
    def test_walkability_smoothstep_is_monotonic(self) -> None:
        values = [_smoothstep(0.34, 0.66, value / 10.0) for value in range(11)]
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 1.0)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in values))
        self.assertTrue(all(a <= b for a, b in zip(values, values[1:])))

    def test_stylized_volcano_keeps_crater_below_rim(self) -> None:
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
