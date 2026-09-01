import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from services.process_runner import run_processor


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = REPO_ROOT / "out" / "builtin-node-packs" / "rigging-evidence"


class FacialRigAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["node", "scripts/build-builtins.mjs"], cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)

    def _run(self, node_id: str, descriptor: dict[str, object], params: dict[str, object] | None = None) -> dict[str, object]:
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
                {"_node_id": node_id, **(params or {})},
                str(workspace),
                str(temp),
            )
            return json.loads(str(result["text"]))

    def test_minimal_facial_profile_passes(self) -> None:
        descriptor = {"blendShapes": [{"name": name, "min": 0, "max": 1} for name in ["jawOpen", "mouthClose", "mouthSmile_L", "mouthSmile_R", "eyeBlink_L", "eyeBlink_R"]]}
        report = self._run("facial-rig-audit", descriptor, {"profile": "minimal"})
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["missingShapes"], [])

    def test_invalid_range_and_missing_arkit_channels_fail(self) -> None:
        report = self._run("facial-rig-audit", {"blendShapes": [{"name": "jawOpen", "min": -0.1, "max": 1.0}]}, {"profile": "arkit-lite"})
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("range" in error for error in report["errors"]))
        self.assertIn("mouthClose", report["missingShapes"])

    def test_lip_sync_checks_visemes_and_monotonic_curves(self) -> None:
        descriptor = {
            "blendShapes": ["mouthOpen", "mouthClose"],
            "requiredVisemes": ["AA", "sil"],
            "visemes": {"AA": "mouthOpen", "sil": ["mouthClose"]},
            "curves": [{"time": 0.0, "weights": {"mouthOpen": 0.0}}, {"time": 0.2, "weights": {"mouthOpen": 1.0}}],
        }
        report = self._run("lip-sync-audit", descriptor)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["visemeCount"], 2)

        invalid = self._run("lip-sync-audit", {**descriptor, "curves": [{"time": 1.0, "weights": {"mouthOpen": 1.1}}, {"time": 0.5, "weights": {"mouthOpen": 0.2}}]})
        self.assertEqual(invalid["status"], "fail")
        self.assertTrue(any("monotonically" in error for error in invalid["errors"]))
        self.assertTrue(any("within [0, 1]" in error for error in invalid["errors"]))

    def test_fabrik_reaches_target_and_flags_unreachable_target(self) -> None:
        chain = {
            "chain": [
                {"id": "root", "position": [0, 0, 0]},
                {"id": "elbow", "position": [1, 0, 0]},
                {"id": "wrist", "position": [2, 0, 0]},
            ],
            "target": [1.0, 1.0, 0.0],
        }
        report = self._run("ik-solve", chain)
        self.assertEqual(report["status"], "pass")
        self.assertLessEqual(report["summary"]["endError"], 0.001)
        self.assertLessEqual(report["summary"]["maxSegmentLengthError"], 1e-6)

        unreachable = self._run("ik-solve", {**chain, "target": [4, 0, 0]})
        self.assertEqual(unreachable["status"], "needs_review")
        self.assertTrue(unreachable["summary"]["unreachable"])
        self.assertAlmostEqual(unreachable["summary"]["reach"], 2.0)

    def test_mixamo_aliases_and_parent_chain_pass(self) -> None:
        parent_by_name = {
            "Spine": "Hips", "Spine1": "Spine", "Spine2": "Spine1", "Neck": "Spine2", "Head": "Neck",
            "LeftShoulder": "Spine2", "LeftArm": "LeftShoulder", "LeftForeArm": "LeftArm", "LeftHand": "LeftForeArm",
            "RightShoulder": "Spine2", "RightArm": "RightShoulder", "RightForeArm": "RightArm", "RightHand": "RightForeArm",
            "LeftUpLeg": "Hips", "LeftLeg": "LeftUpLeg", "LeftFoot": "LeftLeg", "RightUpLeg": "Hips", "RightLeg": "RightUpLeg", "RightFoot": "RightLeg",
        }
        bones = [{"id": "mixamorig:Hips", "name": "mixamorig:Hips"}]
        for name, parent in parent_by_name.items():
            bones.append({"id": f"mixamorig:{name}", "name": f"mixamorig:{name}", "parent": f"mixamorig:{parent}"})
        report = self._run("mixamo-audit", {"rig": {"bones": bones}})
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["mixamoReady"])
        self.assertEqual(report["missingCore"], [])

        broken = [bone for bone in bones if bone["name"] != "mixamorig:LeftFoot"]
        broken_report = self._run("mixamo-audit", {"rig": {"bones": broken}})
        self.assertEqual(broken_report["status"], "fail")
        self.assertIn("LeftFoot", broken_report["missingCore"])


if __name__ == "__main__":
    unittest.main()
