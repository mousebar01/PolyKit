import tempfile
import unittest
from pathlib import Path

import trimesh
from PIL import Image

from services.runtime_paths import runtime_paths
from services.spatial_validation import build_world_spatial_bundle
from services.visual_validation import build_visual_validation_report, compare_reference_images
from services.world_domain import create_world_document
from services.world_validation import validate_world


class SpatialValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-spatial-validation-")
        self.root = Path(self._tmp.name)
        (self.root / "Workflows").mkdir(parents=True, exist_ok=True)
        runtime_paths.update(workspace_dir=self.root)

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def _run(self, workspace_path: str = "Workflows/cabin.glb") -> dict:
        return {
            "run_id": "run-1",
            "status": "done",
            "meta": {
                "observability": {
                    "artifacts": [
                        {
                            "kind": "mesh",
                            "workspace_path": workspace_path,
                        }
                    ]
                }
            },
        }

    def _world(self, *, gap: float = 0.0) -> dict:
        world = create_world_document(name="Cabin", prompt="A reference cabin")
        world["id"] = "world-1"
        world["runtime"]["build"]["buildings"] = [
            {
                "id": "cabin",
                "name": "Cabin",
                "generator": "blender-parametric",
                "parameters": {},
                "anchors": [
                    {"id": "floor-left", "partId": "floor", "position": [0.0, 0.0, 0.0]},
                    {"id": "wall-bottom", "partId": "left-wall", "position": [gap, 0.0, 0.0]},
                ],
                "attachments": [
                    {
                        "id": "wall-floor",
                        "from": "floor-left",
                        "to": "wall-bottom",
                        "mode": "support",
                        "tolerance": 0.05,
                    }
                ],
            }
        ]
        return world

    def _write_cabin(self, *, wall_x: float = 0.0) -> None:
        scene = trimesh.Scene()
        floor = trimesh.creation.box(extents=[4.0, 0.2, 4.0])
        floor.apply_translation([0.0, -0.1, 0.0])
        wall = trimesh.creation.box(extents=[0.2, 2.0, 4.0])
        wall.apply_translation([wall_x, 1.0, 0.0])
        scene.add_geometry(floor, node_name="Cabin_Floor", geom_name="Cabin_Floor")
        scene.add_geometry(wall, node_name="Cabin_Wall_Left", geom_name="Cabin_Wall_Left")
        (self.root / "Workflows" / "cabin.glb").write_bytes(scene.export(file_type="glb"))

    def _visual_report(self, *, spatial_status: str = "pass") -> dict:
        reference = self.root / "reference.png"
        candidate = self.root / "candidate.png"
        image = Image.new("RGB", (320, 180), (60, 80, 100))
        image.save(reference)
        image.save(candidate)
        metric_bundle = compare_reference_images(
            reference,
            candidate,
            self.root / "visual-evidence",
            tag="spatial-authority",
            observations=[
                {
                    "id": "hero",
                    "priority": "P0",
                    "bbox_normalized": [0.25, 0.2, 0.5, 0.6],
                    "candidate_bbox_normalized": [0.25, 0.2, 0.5, 0.6],
                }
            ],
        )
        metric_bundle["evidence"].append(
            {
                "id": "evidence:spatial-mesh",
                "kind": "geometry_snapshot_source",
                "workspace_path": "Workflows/cabin.glb",
            }
        )
        semantic = [
            {
                "id": "semantic.final-review",
                "category": "semantic",
                "judge": "semantic",
                "required": True,
                "status": "pass",
                "subjects": ["hero"],
                "confidence": 0.95,
                "evidence_refs": ["evidence:reference", "evidence:candidate"],
                "message": "Reference identity matches.",
            }
        ]
        spatial = [
            {
                "id": "spatial.caller-claimed-pass",
                "category": "spatial",
                "judge": "spatial",
                "required": True,
                "status": spatial_status,
                "subjects": ["cabin"],
                "evidence_refs": ["evidence:spatial-mesh"],
                "message": "Caller-authored spatial result.",
            }
        ]
        return build_visual_validation_report(
            world_id="world-1",
            run_id="run-1",
            target={"kind": "reference-image", "require_spatial": True},
            candidate={},
            metric_bundle=metric_bundle,
            semantic_checks=semantic,
            spatial_checks=spatial,
        )

    def test_final_glb_and_buildspec_contact_pass(self) -> None:
        self._write_cabin()
        bundle = build_world_spatial_bundle(
            "world-1",
            self._world(),
            self._run(),
            workspace_root=self.root,
        )
        self.assertEqual(bundle["status"], "pass")
        contact = next(item for item in bundle["checks"] if "wall-floor" in item["id"])
        self.assertEqual(contact["status"], "pass")
        self.assertLessEqual(contact["metrics"]["measured"], 0.05)
        self.assertEqual(bundle["snapshot"]["kind"], "polykit.spatial-snapshot")

    def test_buildspec_anchor_gap_fails_even_when_mesh_is_readable(self) -> None:
        self._write_cabin()
        bundle = build_world_spatial_bundle(
            "world-1",
            self._world(gap=0.3),
            self._run(),
            workspace_root=self.root,
        )
        self.assertEqual(bundle["status"], "fail")
        contact = next(item for item in bundle["checks"] if "wall-floor" in item["id"])
        self.assertEqual(contact["status"], "fail")
        self.assertGreater(contact["metrics"]["anchor_gap"], 0.05)

    def test_missing_mesh_never_passes(self) -> None:
        bundle = build_world_spatial_bundle(
            "world-1",
            self._world(),
            {"run_id": "run-1", "status": "done", "meta": {"observability": {"artifacts": []}}},
            workspace_root=self.root,
        )
        self.assertEqual(bundle["status"], "needs_review")
        mesh_check = next(item for item in bundle["checks"] if item["id"] == "spatial.final-mesh")
        self.assertEqual(mesh_check["status"], "not_evaluated")

    def test_p0_world_object_requires_compiled_instance(self) -> None:
        self._write_cabin()
        world = self._world()
        world["runtime"]["scene"] = {
            "kind": "polykit.scene-plan",
            "version": 1,
            "objects": [{"id": "doorway", "name": "Doorway", "role": "hero"}],
            "instances": [],
            "relations": [],
            "metadata": {"layoutQuality": {"status": "pass"}},
        }
        target = {
            "observations": [
                {"id": "doorway-p0", "priority": "P0", "world_object_id": "doorway"}
            ]
        }
        bundle = build_world_spatial_bundle(
            "world-1",
            world,
            self._run(),
            target=target,
            workspace_root=self.root,
        )
        p0 = next(item for item in bundle["checks"] if item["id"] == "spatial.p0.doorway-p0.world-object")
        self.assertEqual(p0["status"], "fail")
        self.assertEqual(bundle["status"], "fail")

    def test_world_spatial_capability_reads_run_geometry(self) -> None:
        self._write_cabin()
        result = validate_world("world-1", self._world(), "world.spatial.validate", run=self._run())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["details"]["bundle_status"], "pass")
        self.assertEqual(result["details"]["snapshot"]["kind"], "polykit.spatial-snapshot")

    def test_visual_gate_rechecks_spatial_geometry_instead_of_trusting_report(self) -> None:
        self._write_cabin()
        report = self._visual_report(spatial_status="pass")
        self.assertEqual(report["status"], "pass")
        run = self._run()
        run["meta"]["workflow_metadata"] = {
            "world_id": "world-1",
            "visual_validation_report": report,
        }
        result = validate_world("world-1", self._world(gap=0.3), "world.visual.validate", run=run)
        self.assertEqual(result["details"]["validation_status"], "pass")
        self.assertEqual(result["details"]["authoritative_spatial_status"], "fail")
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("wall-floor" in item["code"] for item in result["issues"]))


if __name__ == "__main__":
    unittest.main()
