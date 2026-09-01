import json
import tempfile
import unittest
from pathlib import Path

from services.process_runner import run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/rigging-evidence"


class RiggingEvidenceProcessorTests(unittest.TestCase):
    def _run(self, descriptor: dict, root: Path) -> dict:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        temp = root / "tmp"
        temp.mkdir(exist_ok=True)
        result = run_processor(
            PACK_DIR,
            "processor.py",
            {"text": json.dumps(descriptor)},
            {"_node_id": "attachment-anchor-audit"},
            str(workspace),
            str(temp),
        )
        return json.loads(str(result["text"]))

    def test_attachment_anchor_audit_checks_declaration_and_proximity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            valid = {
                "componentTree": [
                    {"id": "body"},
                    {"id": "head", "parent": "body", "dimensions": {"radius": 0.4}},
                    {"id": "hat", "parent": "body", "role": "worn", "attachment": {"anchor": "head"}},
                ],
                "measured": {"head": [0, 1.8, 0], "hat": [0, 1.85, 0]},
            }
            report = self._run(valid, root)
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["passed"])
            self.assertEqual(report["attachments"][0]["anchorKind"], "component")
            self.assertTrue(report["attachments"][0]["withinLimit"])

            missing = {
                "componentTree": [
                    {"id": "body"},
                    {"id": "hat", "role": "worn", "parent": "body"},
                ]
            }
            missing_report = self._run(missing, root)
            self.assertEqual(missing_report["status"], "fail")
            self.assertIn("ANCHOR_DECLARED", missing_report["errors"][0])

            far = {
                "componentTree": [
                    {"id": "body"},
                    {"id": "head", "parent": "body", "dimensions": {"radius": 0.4}},
                    {"id": "hat", "parent": "body", "role": "worn", "attachment": {"anchor": "head"}},
                ],
                "measured": {"head": [0, 1.8, 0], "hat": [0, 3.0, 0]},
            }
            far_report = self._run(far, root)
            self.assertEqual(far_report["status"], "fail")
            self.assertIn("ANCHOR_PROXIMITY", far_report["errors"][0])


if __name__ == "__main__":
    unittest.main()
