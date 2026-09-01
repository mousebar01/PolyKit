import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from services.process_runner import run_processor


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = REPO_ROOT / "out" / "builtin-node-packs" / "reference-evidence"


class PoseSweepGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["node", "scripts/build-builtins.mjs"], cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)

    @staticmethod
    def _frame(path: Path, box: tuple[int, int, int, int]) -> None:
        image = Image.new("RGBA", (128, 128), (15, 23, 42, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle(box, fill=(220, 220, 220, 255))
        image.save(path)

    def _run(self, boxes: list[tuple[int, int, int, int]], params: dict[str, object] | None = None) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths: list[str] = []
            for index, box in enumerate(boxes):
                path = root / f"pose-{index}.png"
                self._frame(path, box)
                paths.append(str(path))
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()
            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePaths": paths},
                {"_node_id": "pose-sweep-gate", **(params or {})},
                str(workspace),
                str(temp),
            )
            report = next(Path(str(item)) for item in result["sidecars"] if str(item).endswith(".json"))
            return json.loads(report.read_text(encoding="utf-8"))

    def test_changed_ordered_sweep_passes_and_records_delta(self) -> None:
        report = self._run([(40, 16, 88, 112), (24, 28, 96, 112), (36, 12, 92, 104), (16, 40, 104, 112)])
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["frameCount"], 4)
        self.assertGreater(report["summary"]["maxPoseDelta"], 0.03)

    def test_collapsed_frame_fails(self) -> None:
        report = self._run([(32, 16, 96, 112), (62, 58, 66, 62), (30, 20, 98, 112), (28, 24, 100, 110)])
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["summary"]["collapsedFrames"], [1])

    def test_missing_frame_count_is_an_explicit_error(self) -> None:
        report = self._run([(32, 16, 96, 112), (24, 28, 96, 112)], {"required_frames": 4})
        self.assertEqual(report["status"], "fail")
        self.assertIn("expected 4 ordered frames", report["errors"][0])


if __name__ == "__main__":
    unittest.main()
