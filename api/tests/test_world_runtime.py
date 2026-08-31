import tempfile
import unittest
from pathlib import Path

from services.runtime_paths import runtime_paths
from services.world_domain import create_world_document
from services.world_store import save_world


class WorldRuntimeAttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-world-runtime-")
        runtime_paths.update(workspace_dir=Path(self._tmp.name))

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def _world_with_building(self, *, gap: float = 0.0, mode: str = "support", include_positions: bool = True):
        world = create_world_document(name="Cabin", prompt="A parameterized winter cabin")
        left_anchor = {"id": "wall-top", "partId": "wall"}
        right_anchor = {"id": "roof-bearing", "partId": "roof"}
        if include_positions:
            left_anchor["position"] = [0.0, 2.8, 0.0]
            right_anchor["position"] = [gap, 2.8, 0.0]
        if mode == "flush":
            left_anchor["normal"] = [1.0, 0.0, 0.0]
            right_anchor["normal"] = [-1.0, 0.0, 0.0]
        world["runtime"]["build"]["buildings"] = [
            {
                "id": "cabin",
                "name": "Winter Cabin",
                "generator": "blender-parametric",
                "parameters": {"width": 12.0, "wallHeight": 5.6},
                "anchors": [left_anchor, right_anchor],
                "attachments": [
                    {
                        "id": "wall-roof-contact",
                        "from": "wall-top",
                        "to": "roof-bearing",
                        "mode": mode,
                        "tolerance": 0.05,
                    }
                ],
            }
        ]
        return world

    def test_support_attachment_inside_tolerance_passes_construction_gate(self) -> None:
        world = self._world_with_building(gap=0.02)
        saved = save_world(world)
        gate = saved["runtime"]["quality"]["construction"]
        self.assertEqual(gate["status"], "pass")
        self.assertEqual(gate["issues"], [])

    def test_contact_gap_fails_construction_gate(self) -> None:
        world = self._world_with_building(gap=0.12)
        saved = save_world(world)
        gate = saved["runtime"]["quality"]["construction"]
        self.assertEqual(gate["status"], "fail")
        issue = next(item for item in gate["issues"] if item["code"] == "attachment-gap")
        self.assertAlmostEqual(issue["measured"], 0.12)
        self.assertEqual(issue["subjectId"], "cabin")

    def test_flush_attachment_checks_opposed_normals(self) -> None:
        world = self._world_with_building(mode="flush")
        world["runtime"]["build"]["buildings"][0]["anchors"][1]["normal"] = [1.0, 0.0, 0.0]
        saved = save_world(world)
        gate = saved["runtime"]["quality"]["construction"]
        self.assertEqual(gate["status"], "fail")
        self.assertTrue(any(item["code"] == "attachment-normal-mismatch" for item in gate["issues"]))

    def test_missing_or_volume_only_evidence_never_silently_passes(self) -> None:
        world = self._world_with_building(mode="inside", include_positions=False)
        saved = save_world(world)
        gate = saved["runtime"]["quality"]["construction"]
        self.assertEqual(gate["status"], "needs_review")
        self.assertTrue(any(item["severity"] == "warning" for item in gate["issues"]))

    def test_authored_gate_is_replaced_by_derived_result(self) -> None:
        world = self._world_with_building(gap=0.2)
        world["runtime"]["quality"]["construction"] = {
            "status": "pass",
            "issues": [],
        }
        saved = save_world(world)
        self.assertEqual(saved["runtime"]["quality"]["construction"]["status"], "fail")

    def test_world_quality_contains_no_agent_stage_progress(self) -> None:
        world = create_world_document(name="No stages")
        self.assertNotIn("state", world["runtime"])
        self.assertNotIn("stages", world["runtime"]["quality"])
        self.assertEqual(set(world["runtime"]["quality"]), {"construction", "visual", "gameplay"})


if __name__ == "__main__":
    unittest.main()
