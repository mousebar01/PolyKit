import tempfile
import unittest
from pathlib import Path

from services.runtime_paths import runtime_paths
from services.world_domain import create_world_document
from services.world_validation import validate_world
from services.world_workflows import build_structure_workflow


class WorldWorkflowBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-world-bridge-")
        runtime_paths.update(workspace_dir=Path(self._tmp.name))

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def _world(self):
        world = create_world_document(name="Cabin", prompt="Build a small playable cabin")
        runtime = world["runtime"]
        runtime["build"]["buildings"] = [
            {
                "id": "cabin",
                "name": "Cabin",
                "generator": "blender-parametric",
                "parameters": {
                    "width": 8.0,
                    "depth": 7.0,
                    "wallHeight": 4.2,
                    "roofPitchDeg": 38.0,
                    "roofOverhang": 0.5,
                    "contactTolerance": 0.03,
                },
                "anchors": [
                    {"id": "floor", "partId": "floor", "position": [0.0, 0.0, 0.0]},
                    {"id": "wall", "partId": "wall", "position": [0.0, 0.0, 0.0]},
                ],
                "attachments": [
                    {"id": "wall-floor", "from": "floor", "to": "wall", "mode": "support", "tolerance": 0.03}
                ],
            }
        ]
        runtime["scene"] = {
            "id": world["id"],
            "objects": [{"id": "cabin", "name": "Cabin", "role": "hero", "size": [8, 4.2, 7]}],
            "relations": [],
            "instances": [{"objectId": "cabin", "position": [0, 0, 0], "rotation": [0, 0, 0], "scale": 1}],
            "metadata": {"layoutQuality": {"status": "pass"}},
            "diagnostics": [],
        }
        runtime["game"]["objectives"] = [
            {"id": "reach-cabin", "label": "Reach the cabin", "trigger": "reach", "targetId": "cabin"}
        ]
        return world

    def _construction_run(self, world_id: str):
        return {
            "run_id": "run-cabin",
            "status": "done",
            "meta": {
                "workflow_metadata": {
                    "world_id": world_id,
                    "building_id": "cabin",
                    "workflow_recipe": "building-construction",
                }
            },
        }

    def test_domain_validators_report_facts_without_orchestration_outcomes(self) -> None:
        world = self._world()
        world_id = world["id"]
        for capability in ("world.spec.validate", "world.blockout.validate", "world.gameplay.validate"):
            report = validate_world(world_id, world, capability)
            self.assertEqual(report["status"], "pass")
            self.assertNotIn("outcome", report)

        construction = validate_world(
            world_id,
            world,
            "world.construction.validate",
            run=self._construction_run(world_id),
        )
        self.assertEqual(construction["status"], "pass")
        self.assertNotIn("outcome", construction)
        self.assertEqual(construction["evidence"]["kind"], "construction-report")

    def test_construction_rejects_a_run_from_another_world(self) -> None:
        world = self._world()
        run = self._construction_run("other-world")
        report = validate_world(world["id"], world, "world.construction.validate", run=run)
        self.assertEqual(report["status"], "fail")
        self.assertNotIn("outcome", report)
        self.assertTrue(any(issue["code"] == "construction-run-mismatch" for issue in report["issues"]))

    def test_gameplay_requires_an_objective_for_a_playable_world(self) -> None:
        world = self._world()
        world["runtime"]["game"]["objectives"] = []
        report = validate_world(world["id"], world, "world.gameplay.validate")
        self.assertEqual(report["status"], "needs_review")
        self.assertNotIn("outcome", report)

    def test_final_validation_never_silently_passes_missing_visual_evidence(self) -> None:
        world = self._world()
        report = validate_world(
            world["id"],
            world,
            "world.final.validate",
            run=self._construction_run(world["id"]),
        )
        self.assertEqual(report["status"], "needs_review")
        self.assertNotIn("outcome", report)
        self.assertTrue(any(issue["code"] == "final-visual-evidence-missing" for issue in report["issues"]))

    def test_structure_compiler_targets_the_existing_blender_node(self) -> None:
        world = self._world()
        request = build_structure_workflow(world, world_id=world["id"], collection="Scenes")
        self.assertEqual(request.workflow_id, "building-construction")
        self.assertEqual(request.prompt["brief"].class_type, "polykit.text")
        self.assertEqual(request.prompt["build"].class_type, "blender-scene/build")
        self.assertEqual(request.prompt["output"].class_type, "polykit.output")
        params = request.prompt["build"].inputs["params"]
        self.assertEqual(params["cabin_width"], 8.0)
        self.assertEqual(params["cabin_depth"], 7.0)
        self.assertEqual(params["wall_height"], 4.2)
        self.assertEqual(params["contact_tolerance"], 0.03)
        self.assertEqual(request.metadata["workflow_recipe"], "building-construction")
        self.assertEqual(request.metadata["world_id"], world["id"])


if __name__ == "__main__":
    unittest.main()
