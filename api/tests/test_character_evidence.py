import json
import math
import tempfile
import unittest
from pathlib import Path

from services.process_runner import run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/character-evidence"


class CharacterEvidenceProcessorTests(unittest.TestCase):
    def _run(self, root: Path, *, text: str = "", style_heads: float = 8.0) -> dict:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        temp = root / "tmp"
        temp.mkdir(exist_ok=True)
        result = run_processor(
            PACK_DIR,
            "processor.py",
            {"text": text},
            {"_node_id": "humanoid-proportions", "style_heads": style_heads},
            str(workspace),
            str(temp),
        )
        return json.loads(str(result["text"]))

    def _run_hair_profile(self, root: Path, profile: dict) -> dict:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        temp = root / "tmp"
        temp.mkdir(exist_ok=True)
        result = run_processor(
            PACK_DIR,
            "processor.py",
            {"text": json.dumps(profile)},
            {"_node_id": "hair-profile"},
            str(workspace),
            str(temp),
        )
        return json.loads(str(result["text"]))

    def _run_hair_compile(self, root: Path, profile: dict) -> dict:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        temp = root / "tmp"
        temp.mkdir(exist_ok=True)
        result = run_processor(
            PACK_DIR,
            "processor.py",
            {"text": json.dumps(profile)},
            {"_node_id": "hair-profile-compile"},
            str(workspace),
            str(temp),
        )
        return json.loads(str(result["text"]))

    def test_canonical_eight_head_table_is_explicitly_provenanced(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = self._run(Path(td))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["provenance"], "canon-table")
        anatomy = report["anatomy"]
        self.assertEqual(anatomy["styleHeads"], 8.0)
        self.assertEqual(anatomy["proportions"]["legs"], 4.0)
        self.assertEqual(anatomy["proportions"]["hipLineY"], 0.5)
        self.assertIn("chestWidth", report["unsourced"])

    def test_reference_free_spec_receives_anatomy_without_overwriting_other_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec = {"name": "stylized", "sourceImage": "/dev/null", "preSpecAssessment": {"style": "low-poly"}}
            report = self._run(Path(td), text=json.dumps(spec))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["spec"]["name"], "stylized")
        self.assertEqual(report["spec"]["preSpecAssessment"]["style"], "low-poly")
        self.assertEqual(report["spec"]["preSpecAssessment"]["anatomy"]["source"], "canon-table")

    def test_reference_named_spec_is_rejected_instead_of_receiving_fake_canon(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            spec = {"name": "measured", "sourceImage": "Workflows/reference.png"}
            report = self._run(Path(td), text=json.dumps(spec))
        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["passed"])
        self.assertIn("measure anatomy from it", report["errors"][0])

    def test_unsupported_head_count_fails_without_interpolation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = self._run(Path(td), style_heads=4.0)
        self.assertEqual(report["status"], "fail")
        self.assertIn("limited to 8 heads", report["errors"][0])

    def test_hair_profile_accepts_scalp_bound_mass_and_marks_locks_as_uncalibrated(self) -> None:
        profile = {
            "representationTier": "locks",
            "scalpComponentId": "head",
            "hairline": {"controlPoints": [{"u": 0.2, "v": 0.3}, {"u": 0.5, "v": 0.28}, {"u": 0.8, "v": 0.3}]},
            "flowField": {"gravity": 0.35, "partLine": {"u": 0.52}, "whorls": [{"u": 0.52, "v": 0.8, "strength": 0.2}]},
            "masses": [{"id": "fringe", "region": "fringe", "primitive": "tapered-sweep", "root": {"u": 0.5, "v": 0.3}, "length": 0.4, "width": 0.12, "thickness": 0.04, "taper": 0.1, "uncalibrated": True}],
        }
        with tempfile.TemporaryDirectory() as td:
            report = self._run_hair_profile(Path(td), profile)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["massCount"], 1)
        self.assertIn("masses[].taper", report["uncalibratedFields"])
        self.assertTrue(any("locks-tier" in warning for warning in report["warnings"]))

    def test_hair_profile_rejects_absolute_root_and_opaque_card_primitive(self) -> None:
        profile = {
            "representationTier": "masses",
            "scalpComponentId": "head",
            "masses": [
                {"id": "side", "primitive": "plane-card", "root": {"position": [0, 1, 0]}},
                {"id": "side", "primitive": "tube", "root": {"u": 0.5, "v": 0.5}},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            report = self._run_hair_profile(Path(td), profile)
        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["passed"])
        self.assertTrue(any("plane-card" in error for error in report["errors"]))
        self.assertTrue(any("scalp {u, v}" in error for error in report["errors"]))
        self.assertTrue(any("duplicate id" in error for error in report["errors"]))

    def test_hair_profile_compile_emits_scalp_attached_components_without_inventing_geometry(self) -> None:
        profile = {
            "componentId": "hair-system",
            "representationTier": "masses",
            "scalpComponentId": "head",
            "hairline": {"controlPoints": [{"u": 0.2, "v": 0.3}, {"u": 0.5, "v": 0.28}, {"u": 0.8, "v": 0.3}]},
            "flowField": {"gravity": 0.35, "partLine": {"u": 0.52}},
            "masses": [{"id": "fringe", "region": "fringe", "primitive": "tapered-sweep", "root": {"u": 0.5, "v": 0.3}, "length": 0.4, "width": 0.12, "thickness": 0.04}],
        }
        with tempfile.TemporaryDirectory() as td:
            report = self._run_hair_compile(Path(td), profile)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["componentTree"][0]["parent"], "head")
        self.assertEqual(report["componentTree"][0]["attachment"]["anchor"], "head")
        mass = report["componentTree"][1]
        self.assertEqual(mass["primitive"], "tapered-sweep")
        self.assertEqual(mass["surfaceUv"], {"u": 0.5, "v": 0.3})
        self.assertEqual(mass["parameters"]["length"], 0.4)

    def test_hair_profile_compile_needs_review_when_mass_geometry_is_underspecified(self) -> None:
        profile = {"representationTier": "masses", "scalpComponentId": "head", "masses": [{"id": "side", "root": {"u": 0.5, "v": 0.5}}]}
        with tempfile.TemporaryDirectory() as td:
            report = self._run_hair_compile(Path(td), profile)
        self.assertEqual(report["status"], "needs_review")
        self.assertIn("hair-side.primitive", report["unresolved"])
        self.assertIn("hair-side.parameters", report["unresolved"])

    def test_hair_profile_compile_rejects_self_parenting_component_id(self) -> None:
        profile = {"componentId": "head", "representationTier": "shell", "scalpComponentId": "head"}
        with tempfile.TemporaryDirectory() as td:
            report = self._run_hair_compile(Path(td), profile)
        self.assertEqual(report["status"], "fail")
        self.assertTrue(any("componentId" in error for error in report["errors"]))

    def test_scalp_exposure_distinguishes_proud_hair_from_sunk_hair(self) -> None:
        rings = [[0.0, 1.0, 1.0], [1.0, 1.0, 1.0]]
        u_samples, v_samples = 16, 8

        def shell(offset: float) -> list[list[float]]:
            points: list[list[float]] = []
            for row in range(v_samples):
                v = 0.55 + 0.4 * (row + 0.5) / v_samples
                y = v
                for column in range(u_samples):
                    theta = 2.0 * 3.141592653589793 * column / u_samples
                    points.append([(1.0 + offset) * math.cos(theta), y, (1.0 + offset) * math.sin(theta)])
            return points

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            def run(points: list[list[float]]) -> dict:
                result = run_processor(
                    PACK_DIR,
                    "processor.py",
                    {"text": json.dumps({"rings": rings, "hairPoints": points})},
                    {"_node_id": "scalp-exposure", "v_low": 0.55, "v_high": 0.95, "u_samples": u_samples, "v_samples": v_samples},
                    str(workspace),
                    str(temp),
                )
                return json.loads(str(result["text"]))

            proud = run(shell(0.05))
            self.assertEqual(proud["verdict"], "pass")
            self.assertEqual(proud["exposedFraction"], 0.0)
            self.assertEqual(proud["hairPointsInsideSkull"], 0)

            sunk = run(shell(-0.02))
            self.assertEqual(sunk["verdict"], "fail")
            self.assertEqual(sunk["exposedFraction"], 1.0)
            self.assertEqual(sunk["hairPointsInsideSkull"], len(shell(-0.02)))


if __name__ == "__main__":
    unittest.main()
