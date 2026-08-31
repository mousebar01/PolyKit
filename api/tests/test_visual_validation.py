import tempfile
import unittest
from pathlib import Path

from PIL import Image

from services.runtime_paths import runtime_paths
from services.visual_validation import (
    build_visual_validation_report,
    compare_reference_images,
    validate_visual_validation_report,
)
from services.world_domain import create_world_document
from services.world_validation import validate_world


class VisualValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-visual-validation-")
        self.root = Path(self._tmp.name)
        runtime_paths.update(workspace_dir=self.root)

        self.reference = self.root / "reference.png"
        self.candidate = self.root / "candidate.png"
        image = Image.new("RGB", (320, 180), (60, 80, 100))
        image.save(self.reference)
        image.save(self.candidate)

        self.observations = [
            {
                "id": "hero",
                "priority": "P0",
                "bbox_normalized": [0.25, 0.2, 0.5, 0.6],
                "candidate_bbox_normalized": [0.25, 0.2, 0.5, 0.6],
            }
        ]

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def _metric_bundle(self):
        return compare_reference_images(
            self.reference,
            self.candidate,
            self.root / "evidence",
            tag="attempt-1",
            observations=self.observations,
        )

    def _semantic_pass(self):
        return [
            {
                "id": "semantic.final-review",
                "category": "semantic",
                "judge": "semantic",
                "required": True,
                "status": "pass",
                "subjects": ["hero"],
                "confidence": 0.95,
                "evidence_refs": ["evidence:reference", "evidence:candidate"],
                "message": "Reference identity and material category match.",
            }
        ]

    def test_identical_images_with_measured_p0_pass_metric_judge(self) -> None:
        bundle = self._metric_bundle()
        self.assertEqual(bundle["status"], "pass")
        self.assertTrue(all(item["status"] == "pass" for item in bundle["checks"]))
        self.assertTrue((self.root / "evidence" / "reference-overlay-attempt-1.png").is_file())
        self.assertTrue((self.root / "evidence" / "reference-difference-attempt-1.png").is_file())

    def test_missing_semantic_review_prevents_report_pass(self) -> None:
        report = build_visual_validation_report(
            world_id="world-1",
            run_id="run-1",
            target={"kind": "reference-image", "camera_id": "camera-main", "camera_revision": 1},
            candidate={"camera_id": "camera-main", "camera_revision": 1},
            metric_bundle=self._metric_bundle(),
        )
        self.assertEqual(report["status"], "needs_review")
        semantic = next(item for item in report["checks"] if item["judge"] == "semantic")
        self.assertEqual(semantic["status"], "needs_review")

        validation = validate_visual_validation_report(
            report,
            world_id="world-1",
            run_id="run-1",
            workspace_root=self.root,
        )
        self.assertEqual(validation["status"], "needs_review")

    def test_semantic_failure_vetoes_passing_metrics(self) -> None:
        semantic = self._semantic_pass()
        semantic[0]["status"] = "fail"
        semantic[0]["message"] = "Candidate reads as solid flooring instead of standing water."
        report = build_visual_validation_report(
            world_id="world-1",
            run_id="run-1",
            target={"kind": "reference-image"},
            candidate={},
            metric_bundle=self._metric_bundle(),
            semantic_checks=semantic,
        )
        self.assertEqual(report["summary"]["metric_status"], "pass")
        self.assertEqual(report["summary"]["semantic_status"], "fail")
        self.assertEqual(report["status"], "fail")

    def test_missing_candidate_p0_bounds_are_not_silently_passed(self) -> None:
        observations = [
            {
                "id": "hero",
                "priority": "P0",
                "bbox_normalized": [0.25, 0.2, 0.5, 0.6],
            }
        ]
        bundle = compare_reference_images(
            self.reference,
            self.candidate,
            self.root / "evidence-missing-bounds",
            tag="attempt-2",
            observations=observations,
        )
        self.assertEqual(bundle["status"], "needs_review")
        bbox_check = next(item for item in bundle["checks"] if item["id"] == "p0.hero.bbox-geometry")
        self.assertEqual(bbox_check["status"], "not_evaluated")

    def test_world_visual_validator_accepts_completed_run_report(self) -> None:
        report = build_visual_validation_report(
            world_id="world-1",
            run_id="run-1",
            target={"kind": "reference-image", "camera_id": "camera-main", "camera_revision": 1},
            candidate={"camera_id": "camera-main", "camera_revision": 1},
            metric_bundle=self._metric_bundle(),
            semantic_checks=self._semantic_pass(),
        )
        self.assertEqual(report["status"], "pass")
        world = create_world_document(name="Reference world", prompt="Match the supplied reference")
        world["id"] = "world-1"
        run = {
            "run_id": "run-1",
            "status": "done",
            "meta": {
                "workflow_metadata": {
                    "world_id": "world-1",
                    "visual_validation_report": report,
                }
            },
        }

        result = validate_world("world-1", world, "world.visual.validate", run=run)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["details"]["validation_status"], "pass")

    def test_world_visual_validator_fails_without_report(self) -> None:
        world = create_world_document(name="Reference world", prompt="Match the supplied reference")
        world["id"] = "world-1"
        result = validate_world("world-1", world, "world.visual.validate")
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(item["code"] == "visual-report-missing" for item in result["issues"]))


if __name__ == "__main__":
    unittest.main()
