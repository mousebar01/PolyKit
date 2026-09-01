import json
import tempfile
import unittest
from pathlib import Path

import trimesh

from services.process_runner import ProcessExecutionError, run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/mesh-production"


class ClothingBlockoutProcessorTests(unittest.TestCase):
    def _run(self, root: Path, descriptor: dict) -> dict:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        temp = root / "tmp"
        temp.mkdir(exist_ok=True)
        return run_processor(
            PACK_DIR,
            "processor.py",
            {"text": json.dumps(descriptor)},
            {"_node_id": "clothing-blockout", "sections": 16},
            str(workspace),
            str(temp),
        )

    def test_builds_readable_glb_for_supported_garments(self) -> None:
        descriptor = {
            "garments": [
                {"id": "shirt", "kind": "top", "width": 0.6, "height": 0.7, "depth": 0.3, "y": 1.0},
                {"id": "skirt", "kind": "skirt", "width": 0.7, "height": 0.5, "depth": 0.35, "y": 0.5},
                {"id": "pants", "kind": "pants", "width": 0.7, "height": 0.9, "depth": 0.3},
                {"id": "cape", "kind": "cape", "width": 0.8, "height": 0.9, "depth": 0.05, "y": 1.1},
            ]
        }
        with tempfile.TemporaryDirectory() as td:
            result = self._run(Path(td), descriptor)
            output = Path(str(result["filePath"]))
            report_path = Path(str(result["sidecars"][0]))
            scene = trimesh.load(output, force="scene", process=False)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            self.assertIsInstance(scene, trimesh.Scene)
            self.assertEqual(len(scene.geometry), 5)  # pants intentionally has two leg solids
            self.assertTrue(all(len(mesh.faces) > 0 for mesh in scene.geometry.values()))
            self.assertEqual(report["kind"], "polykit.clothing-blockout")
            self.assertEqual(report["summary"]["garmentCount"], 4)
            self.assertEqual(result["metadata"]["evidence_kind"], "clothing-blockout")

    def test_rejects_unknown_kind_and_invalid_clearance(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(ProcessExecutionError):
                self._run(root, {"garments": [{"id": "robe", "kind": "robe", "width": 1, "height": 1, "depth": 1}]})
            with self.assertRaises(ProcessExecutionError):
                self._run(root, {"garments": [{"id": "shirt", "kind": "top", "width": 1, "height": 1, "depth": 1, "clearance": -0.1}]})


if __name__ == "__main__":
    unittest.main()
