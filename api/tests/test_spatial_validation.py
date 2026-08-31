import tempfile
import unittest
from pathlib import Path

import trimesh

from services.spatial_validation import build_world_spatial_bundle
from services.world_domain import create_world_document


class SpatialValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-spatial-validation-")
        self.root = Path(self._tmp.name)
        (self.root / "Workflows").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
