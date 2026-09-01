import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from services.process_runner import run_processor


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = REPO_ROOT / "out" / "builtin-node-packs" / "rigging-evidence"


class RiggingIKTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["node", "scripts/build-builtins.mjs"], cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)

    def _run(self, descriptor: dict[str, object]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()
            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"text": json.dumps(descriptor)},
                {"_node_id": "ik-solve"},
                str(workspace),
                str(temp),
            )
            return json.loads(str(result["text"]))

    def test_fabrik_reaches_target_and_flags_unreachable_target(self) -> None:
        chain = {
            "chain": [
                {"id": "root", "position": [0, 0, 0]},
                {"id": "elbow", "position": [1, 0, 0]},
                {"id": "wrist", "position": [2, 0, 0]},
            ],
            "target": [1.0, 1.0, 0.0],
        }
        report = self._run(chain)
        self.assertEqual(report["status"], "pass")
        self.assertLessEqual(report["summary"]["endError"], 0.001)
        self.assertLessEqual(report["summary"]["maxSegmentLengthError"], 1e-6)

        unreachable = self._run({**chain, "target": [4, 0, 0]})
        self.assertEqual(unreachable["status"], "needs_review")
        self.assertTrue(unreachable["summary"]["unreachable"])
        self.assertAlmostEqual(unreachable["summary"]["reach"], 2.0)


if __name__ == "__main__":
    unittest.main()
