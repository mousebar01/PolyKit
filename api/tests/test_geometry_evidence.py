import json
import math
import tempfile
import unittest
from pathlib import Path

from services.process_runner import run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/geometry-evidence"


class GeometryEvidenceProcessorTests(unittest.TestCase):
    def _run(self, descriptor: dict, root: Path) -> dict:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        temp = root / "tmp"
        temp.mkdir(exist_ok=True)
        result = run_processor(
            PACK_DIR,
            "processor.py",
            {"text": json.dumps(descriptor)},
            {"_node_id": "swept-arc-audit"},
            str(workspace),
            str(temp),
        )
        return json.loads(str(result["text"]))

    def test_hook_arc_passes_and_straight_rod_fails_angular_gate(self) -> None:
        arc_points = []
        for index in range(25):
            angle = math.radians(-45.0 + 270.0 * index / 24.0)
            for z in (-0.08, 0.0, 0.08):
                arc_points.append([2.0 * math.cos(angle), 2.0 * math.sin(angle), z])
        straight_points = [[-2.0 + 4.0 * index / 24.0, 0.0, z] for index in range(25) for z in (-0.08, 0.0, 0.08)]
        with tempfile.TemporaryDirectory() as td:
            arc = self._run({"points": arc_points, "expectations": {"bend_radius": 2.0, "bend_radius_tolerance": 0.15}}, Path(td))
            rod = self._run({"points": straight_points}, Path(td))
        self.assertEqual(arc["status"], "pass")
        self.assertTrue(arc["passed"])
        self.assertGreater(arc["measured"]["angularSpanDeg"], 200.0)
        self.assertAlmostEqual(arc["measured"]["bendRadius"], 2.0, delta=0.15)
        self.assertEqual(rod["status"], "fail")
        self.assertFalse(rod["passed"])
        self.assertTrue(any(check["check"] == "angularSpan" and check["status"] == "fail" for check in rod["checks"]))


if __name__ == "__main__":
    unittest.main()
