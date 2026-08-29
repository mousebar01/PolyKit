import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from routers.workspace_worlds import put_world, read_world
from services.runtime_paths import runtime_paths
from services.world_agent import attach_world_artifact, update_world_stage
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

    def _world(self) -> dict:
        return {
            "schema_version": 1,
            "kind": "polykit.world",
            "world_id": "demo",
            "spec": {"name": "Demo", "seed": 7},
            "artifacts": [{"id": "hero", "workspace_path": "Workflows/hero.glb"}],
        }

    def test_round_trip_uses_workspace_workflows_and_fixed_metadata(self) -> None:
        saved = save_world("demo", self._world())

        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["kind"], "polykit.world")
        self.assertEqual(world_path("demo"), self.workspace / "Workflows" / "demo.world.json")
        self.assertEqual(load_world("demo"), saved)
        self.assertEqual(list((self.workspace / "Workflows").glob("*.tmp")), [])

    def test_missing_metadata_is_filled_but_wrong_metadata_is_rejected(self) -> None:
        saved = save_world("minimal", {"spec": {"name": "Minimal"}})
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["kind"], "polykit.world")

        with self.assertRaises(WorldStoreError):
            save_world("bad-version", {"schema_version": 2})
        with self.assertRaises(WorldStoreError):
            save_world("bad-kind", {"kind": "scene"})

    def test_allows_terrain_coordinate_arrays_named_path(self) -> None:
        body = self._world()
        body["spec"]["rivers"] = [
            {
                "id": "river",
                "path": [[0.1, 0.2], [0.8, 0.7]],
                "width": 0.05,
                "depth": 0.1,
            }
        ]

        saved = save_world("terrain-path", body)
        self.assertEqual(saved["spec"]["rivers"][0]["path"], [[0.1, 0.2], [0.8, 0.7]])

    def test_rejects_unsafe_world_ids_and_artifact_paths(self) -> None:
        for world_id in ("../escape", "/tmp/escape", r"C:\\escape", "nested/world"):
            with self.subTest(world_id=world_id):
                with self.assertRaises(WorldStoreError):
                    save_world(world_id, self._world())

        for artifact_path in ("../secret.glb", "/tmp/secret.glb", r"C:\\secret.glb"):
            with self.subTest(artifact_path=artifact_path):
                body = self._world()
                body["artifacts"] = [{"workspace_path": artifact_path}]
                with self.assertRaises(WorldStoreError):
                    save_world("unsafe-artifact", body)

    def test_rejects_oversized_document_before_writing(self) -> None:
        body = self._world()
        body["world_id"] = "too-large"
        body["spec"] = {"large": "x" * MAX_WORLD_BYTES}
        with self.assertRaises(WorldTooLargeError):
            save_world("too-large", body)
        self.assertFalse((self.workspace / "Workflows" / "too-large.world.json").exists())

    def test_agent_helpers_preserve_paper_stages_and_attach_workspace_mesh(self) -> None:
        world = self._world()
        planned = update_world_stage(
            world,
            stage_id="plan",
            status="done",
            prompt="A volcanic island with a ruined observatory",
            note="Regions and hero prototypes are explicit.",
        )
        self.assertEqual(planned["agent_plan"]["source"], "worldclaw-paper")
        self.assertEqual(
            next(stage for stage in planned["agent_plan"]["stages"] if stage["id"] == "plan")["status"],
            "done",
        )

        attached = attach_world_artifact(
            planned,
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

    def test_agent_helpers_reject_unknown_stage_and_absolute_artifact(self) -> None:
        with self.assertRaises(WorldStoreError):
            update_world_stage(self._world(), stage_id="render", status="done")
        with self.assertRaises(WorldStoreError):
            attach_world_artifact(
                self._world(),
                proto_id="hero",
                workspace_path="/tmp/hero.glb",
            )


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
        body = {"schema_version": 1, "kind": "polykit.world", "spec": {"seed": 1}}
        expected = body | {"schema_version": 1, "kind": "polykit.world"}
        self.assertEqual(
            await put_world("route-demo", body),
            {
                "world_id": "route-demo",
                "workspace_path": "Workflows/route-demo.world.json",
                "url": "/workspace/Workflows/route-demo.world.json",
            },
        )
        self.assertEqual(await read_world("route-demo"), expected)

        with self.assertRaises(HTTPException) as missing:
            await read_world("missing")
        self.assertEqual(missing.exception.status_code, 404)

        with self.assertRaises(HTTPException) as unsafe:
            await put_world("../unsafe", body)
        self.assertEqual(unsafe.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
