import tempfile
import unittest
from pathlib import Path

import trimesh

from services.spatial_validation import build_world_spatial_bundle


class SpatialCameraVolumeValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-spatial-camera-volume-")
        self.root = Path(self._tmp.name)
        (self.root / "Workflows").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self) -> dict:
        return {
            "run_id": "run-1",
            "status": "done",
            "meta": {
                "observability": {
                    "artifacts": [
                        {"kind": "mesh", "workspace_path": "Workflows/scene.glb"}
                    ]
                }
            },
        }

    def _write_scene(self, items: list[tuple[str, trimesh.Trimesh]]) -> None:
        scene = trimesh.Scene()
        for name, mesh in items:
            scene.add_geometry(mesh, node_name=name, geom_name=name)
        (self.root / "Workflows" / "scene.glb").write_bytes(scene.export(file_type="glb"))

    def _scene_world(self) -> dict:
        return {
            "runtime": {
                "build": {"buildings": []},
                "scene": {
                    "kind": "polykit.scene-plan",
                    "version": 1,
                    "objects": [
                        {
                            "id": "hero",
                            "name": "Hero",
                            "role": "hero",
                            "size": [1.0, 1.0, 1.0],
                        }
                    ],
                    "instances": [
                        {
                            "id": "instance_hero",
                            "objectId": "hero",
                            "position": [0.0, 0.0, 0.0],
                            "rotation": [0.0, 0.0, 0.0],
                            "scale": 1.0,
                        }
                    ],
                    "relations": [],
                    "metadata": {"layoutQuality": {"status": "pass"}},
                },
            }
        }

    def _camera_target(self, *, look_at: list[float] | None = None) -> dict:
        return {
            "camera_id": "camera-main",
            "camera_revision": 3,
            "require_visibility": True,
            "camera": {
                "id": "camera-main",
                "revision": 3,
                "position": [0.0, 0.5, -4.0],
                "target": look_at or [0.0, 0.5, 0.0],
                "up": [0.0, 1.0, 0.0],
                "vertical_fov_deg": 60.0,
                "aspect_ratio": 1.0,
                "near": 0.1,
                "far": 20.0,
            },
            "observations": [
                {"id": "hero-p0", "priority": "P0", "world_object_id": "hero"}
            ],
        }

    def _build_world(
        self,
        mode: str,
        *,
        source_part: str,
        target_part: str,
        normal: list[float] | None = None,
    ) -> dict:
        source = {"id": "source", "partId": source_part, "position": [0.0, 0.0, 0.0]}
        target = {"id": "target", "partId": target_part, "position": [0.0, 0.0, 0.0]}
        if normal is not None:
            target["normal"] = normal
        return {
            "runtime": {
                "build": {
                    "buildings": [
                        {
                            "id": "structure",
                            "name": "Structure",
                            "generator": "test",
                            "parameters": {},
                            "anchors": [source, target],
                            "attachments": [
                                {
                                    "id": "volume-relation",
                                    "from": "source",
                                    "to": "target",
                                    "mode": mode,
                                    "tolerance": 0.01,
                                }
                            ],
                        }
                    ]
                },
                "scene": None,
            }
        }

    def test_p0_camera_frustum_and_line_of_sight_pass(self) -> None:
        hero = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
        hero.apply_translation([0.0, 0.5, 0.0])
        self._write_scene([("Hero", hero)])

        bundle = build_world_spatial_bundle(
            "world-1",
            self._scene_world(),
            self._run(),
            target=self._camera_target(),
            workspace_root=self.root,
        )

        self.assertEqual(bundle["status"], "pass")
        frustum = next(item for item in bundle["checks"] if item["id"] == "spatial.p0.hero-p0.frustum")
        sight = next(item for item in bundle["checks"] if item["id"] == "spatial.p0.hero-p0.line-of-sight")
        self.assertEqual(frustum["status"], "pass")
        self.assertEqual(sight["status"], "pass")
        self.assertGreater(sight["metrics"]["visible_rays"], 0)

    def test_p0_outside_camera_frustum_fails(self) -> None:
        hero = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
        hero.apply_translation([0.0, 0.5, 0.0])
        self._write_scene([("Hero", hero)])

        bundle = build_world_spatial_bundle(
            "world-1",
            self._scene_world(),
            self._run(),
            target=self._camera_target(look_at=[0.0, 0.5, -8.0]),
            workspace_root=self.root,
        )

        frustum = next(item for item in bundle["checks"] if item["id"] == "spatial.p0.hero-p0.frustum")
        self.assertEqual(frustum["status"], "fail")
        self.assertEqual(bundle["status"], "fail")

    def test_occluded_p0_never_manufactures_visibility_pass(self) -> None:
        hero = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
        hero.apply_translation([0.0, 0.5, 0.0])
        blocker = trimesh.creation.box(extents=[3.0, 3.0, 0.25])
        blocker.apply_translation([0.0, 0.5, -2.0])
        self._write_scene([("Hero", hero), ("Blocker", blocker)])

        bundle = build_world_spatial_bundle(
            "world-1",
            self._scene_world(),
            self._run(),
            target=self._camera_target(),
            workspace_root=self.root,
        )

        sight = next(item for item in bundle["checks"] if item["id"] == "spatial.p0.hero-p0.line-of-sight")
        self.assertNotEqual(sight["status"], "pass")
        self.assertIn(sight["status"], {"needs_review", "not_evaluated"})
        self.assertNotEqual(bundle["status"], "pass")

    def test_inside_uses_final_watertight_volume(self) -> None:
        container = trimesh.creation.box(extents=[4.0, 4.0, 4.0])
        inner = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
        self._write_scene([("Container", container), ("Inner", inner)])

        world = self._build_world("inside", source_part="inner", target_part="container")
        passing = build_world_spatial_bundle(
            "world-1", world, self._run(), workspace_root=self.root
        )
        relation = next(item for item in passing["checks"] if "volume-relation" in item["id"])
        self.assertEqual(relation["status"], "pass")
        self.assertEqual(passing["status"], "pass")

        inner = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
        inner.apply_translation([3.0, 0.0, 0.0])
        self._write_scene([("Container", container), ("Inner", inner)])
        failing = build_world_spatial_bundle(
            "world-1", world, self._run(), workspace_root=self.root
        )
        relation = next(item for item in failing["checks"] if "volume-relation" in item["id"])
        self.assertEqual(relation["status"], "fail")
        self.assertGreater(relation["metrics"]["outside_count"], 0)
        self.assertEqual(failing["status"], "fail")

    def test_passes_through_requires_inside_and_both_sides(self) -> None:
        wall = trimesh.creation.box(extents=[1.0, 4.0, 4.0])
        conduit = trimesh.creation.box(extents=[4.0, 0.5, 0.5])
        self._write_scene([("Wall", wall), ("Conduit", conduit)])
        world = self._build_world(
            "passes-through",
            source_part="conduit",
            target_part="wall",
            normal=[1.0, 0.0, 0.0],
        )

        passing = build_world_spatial_bundle(
            "world-1", world, self._run(), workspace_root=self.root
        )
        relation = next(item for item in passing["checks"] if "volume-relation" in item["id"])
        self.assertEqual(relation["status"], "pass")
        self.assertTrue(relation["metrics"]["positive_outside"])
        self.assertTrue(relation["metrics"]["negative_outside"])
        self.assertGreater(relation["metrics"]["inside_count"], 0)

        one_sided = trimesh.creation.box(extents=[2.0, 0.5, 0.5])
        one_sided.apply_translation([-0.75, 0.0, 0.0])
        self._write_scene([("Wall", wall), ("Conduit", one_sided)])
        failing = build_world_spatial_bundle(
            "world-1", world, self._run(), workspace_root=self.root
        )
        relation = next(item for item in failing["checks"] if "volume-relation" in item["id"])
        self.assertEqual(relation["status"], "fail")
        self.assertFalse(relation["metrics"]["positive_outside"])
        self.assertEqual(failing["status"], "fail")


if __name__ == "__main__":
    unittest.main()
