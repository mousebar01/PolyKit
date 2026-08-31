import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from routers.workspace_worlds import WorldCreateRequest, create_world, put_world, read_world
from services.runtime_paths import runtime_paths
from services.world_agent import attach_world_artifact, create_world_document
from services.world_store import (
    MAX_WORLD_BYTES,
    WorldStoreError,
    WorldTooLargeError,
    load_world,
    save_world,
    world_path,
)


class WorldStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-world-test-")
        self.workspace = Path(self._tmp.name)
        runtime_paths.update(workspace_dir=self.workspace)

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def _world(self, world_id: str = "demo") -> dict:
        timestamp = "2026-08-31T00:00:00+00:00"
        return {
            "schema_version": 2,
            "kind": "polykit.world",
            "id": world_id,
            "name": "Demo",
            "created_at": timestamp,
            "updated_at": timestamp,
            "runtime": {
                "version": 1,
                "intent": {"prompt": "A small playable world"},
                "build": {
                    "kind": "polykit.build-spec",
                    "version": 1,
                    "environment": None,
                    "buildings": [],
                },
                "scene": None,
                "compiled": {"instances": []},
                "game": {
                    "kind": "polykit.game-spec",
                    "version": 1,
                    "player": {
                        "controller": "walk",
                        "radius": 0.28,
                        "height": 1.7,
                        "move_speed": 2.8,
                        "spawn": {"mode": "auto"},
                    },
                    "collision": {"mode": "semantic-aabb"},
                    "interactions": [],
                    "objectives": [],
                },
                "quality": {
                    "construction": {"status": "pending", "issues": []},
                    "visual": {"status": "pending", "issues": []},
                    "gameplay": {"status": "pending", "issues": []},
                },
            },
            "artifacts": {},
        }

    def test_round_trip_uses_workspace_workflows_and_strict_metadata(self) -> None:
        saved = save_world("demo", self._world())

        self.assertEqual(saved["schema_version"], 2)
        self.assertEqual(saved["kind"], "polykit.world")
        self.assertEqual(world_path("demo"), self.workspace / "Workflows" / "demo.world.json")
        self.assertEqual(load_world("demo"), saved)
        self.assertEqual(list((self.workspace / "Workflows").glob("*.tmp")), [])

    def test_rejects_missing_old_mirrored_or_workflow_state_shapes(self) -> None:
        with self.assertRaises(WorldStoreError):
            save_world("missing", {"id": "missing"})

        old = self._world("old")
        old["schema_version"] = 1
        with self.assertRaises(WorldStoreError):
            save_world("old", old)

        mirrored = self._world("mirrored")
        mirrored["spec"] = {"seed": 1}
        with self.assertRaises(WorldStoreError):
            save_world("mirrored", mirrored)

        workflow_state = self._world("workflow-state")
        workflow_state["runtime"]["state"] = {"stages": []}
        with self.assertRaises(WorldStoreError):
            save_world("workflow-state", workflow_state)

    def test_allows_terrain_coordinate_arrays_named_path(self) -> None:
        body = self._world("terrain-path")
        body["runtime"]["build"]["environment"] = {
            "name": "River test",
            "logline": "River path is geometry, not an artifact path.",
            "seed": 1,
            "size": 100,
            "seaLevel": 0,
            "sky": {"top": "#000000", "horizon": "#ffffff", "sunDirection": [0, 1, 0], "sunStrength": 1},
            "regions": [],
            "rivers": [{"id": "river", "path": [[0.1, 0.2], [0.8, 0.7]], "width": 0.05, "depth": 0.1}],
            "assets": [],
            "relations": [],
        }

        saved = save_world("terrain-path", body)
        self.assertEqual(saved["runtime"]["build"]["environment"]["rivers"][0]["path"], [[0.1, 0.2], [0.8, 0.7]])

    def test_rejects_unsafe_world_ids_and_artifact_paths(self) -> None:
        for world_id in ("../escape", "/tmp/escape", r"C:\\escape", "nested/world"):
            with self.subTest(world_id=world_id):
                with self.assertRaises(WorldStoreError):
                    save_world(world_id, self._world())

        for artifact_path in ("../secret.glb", "/tmp/secret.glb", r"C:\\secret.glb"):
            with self.subTest(artifact_path=artifact_path):
                body = self._world("unsafe-artifact")
                body["artifacts"] = {
                    "hero": {
                        "mode": "workspace-mesh",
                        "mesh": {"kind": "mesh", "workspace_path": artifact_path},
                    }
                }
                with self.assertRaises(WorldStoreError):
                    save_world("unsafe-artifact", body)

    def test_rejects_oversized_document_before_writing(self) -> None:
        body = self._world("too-large")
        body["runtime"]["intent"]["prompt"] = "x" * MAX_WORLD_BYTES
        with self.assertRaises(WorldTooLargeError):
            save_world("too-large", body)
        self.assertFalse((self.workspace / "Workflows" / "too-large.world.json").exists())

    def test_artifact_binds_to_scene_without_mutating_task_progress(self) -> None:
        world = self._world()
        world["runtime"]["scene"] = {
            "kind": "polykit.scene-plan",
            "schema_version": 1,
            "objects": [{"id": "observatory", "name": "Observatory"}],
            "relations": [],
            "instances": [],
        }
        attached = attach_world_artifact(
            world,
            proto_id="observatory",
            workspace_path="Workflows/observatory.glb",
            workflow_id="image-to-trellis",
            run_id="run-123",
            concept_image="Workflows/observatory.png",
        )
        self.assertEqual(
            attached["artifacts"]["observatory"]["mesh"],
            {
                "kind": "mesh",
                "workspace_path": "Workflows/observatory.glb",
                "workflow_id": "image-to-trellis",
                "run_id": "run-123",
            },
        )
        self.assertEqual(
            attached["runtime"]["scene"]["objects"][0]["asset"]["workspacePath"],
            "Workflows/observatory.glb",
        )
        self.assertNotIn("state", attached["runtime"])

    def test_agent_helper_rejects_absolute_artifact(self) -> None:
        with self.assertRaises(WorldStoreError):
            attach_world_artifact(self._world(), proto_id="hero", workspace_path="/tmp/hero.glb")

    def test_new_scene_document_has_domain_state_but_no_workflow_progress(self) -> None:
        first = create_world_document(name="Harbor", prompt="A stylized harbor")
        second = create_world_document(name="Harbor", prompt="A stylized harbor")

        self.assertNotEqual(first["id"], second["id"])
        self.assertNotIn("world_id", first)
        self.assertNotIn("spec", first)
        self.assertNotIn("scene_plan", first)
        self.assertNotIn("agent_plan", first)
        self.assertEqual(
            first["runtime"]["build"],
            {"kind": "polykit.build-spec", "version": 1, "environment": None, "buildings": []},
        )
        self.assertIsNone(first["runtime"]["scene"])
        self.assertNotIn("state", first["runtime"])
        self.assertEqual(
            set(first["runtime"]["quality"]),
            {"construction", "visual", "gameplay"},
        )
        self.assertEqual(first["runtime"]["intent"]["prompt"], "A stylized harbor")


class WorkspaceWorldRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-world-route-test-")
        runtime_paths.update(workspace_dir=Path(self._tmp.name))

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    async def test_put_get_and_http_errors(self) -> None:
        body = WorldStoreTests._world(self, "route-demo")
        self.assertEqual(
            await put_world("route-demo", body),
            {
                "world_id": "route-demo",
                "workspace_path": "Workflows/route-demo.world.json",
                "url": "/workspace/Workflows/route-demo.world.json",
            },
        )
        self.assertEqual(await read_world("route-demo"), body)

        with self.assertRaises(HTTPException) as missing:
            await read_world("missing")
        self.assertEqual(missing.exception.status_code, 404)

        with self.assertRaises(HTTPException) as unsafe:
            await put_world("../unsafe", body)
        self.assertEqual(unsafe.exception.status_code, 400)

    async def test_create_allocates_a_new_scene_record(self) -> None:
        response = await create_world(WorldCreateRequest(name="Harbor", prompt="A stylized harbor"))
        self.assertTrue(response["world_id"].startswith("scene-"))
        self.assertEqual(response["world"]["schema_version"], 2)
        self.assertNotIn("state", response["world"]["runtime"])
        self.assertEqual(response["world"]["runtime"]["quality"]["construction"]["status"], "pending")
        self.assertEqual(await read_world(response["world_id"]), response["world"])


if __name__ == "__main__":
    unittest.main()
