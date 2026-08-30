import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks

from routers.workspace_worlds import (
    SceneComposeRequest,
    ScenePlanCompileRequest,
    _build_scene_composition_workflow,
    compose_world_scene,
    compile_world_scene_plan,
)
from services.runtime_paths import runtime_paths
from services.scene_planner import (
    ScenePlanError,
    apply_scene_plan_to_world,
    compile_scene_plan,
    normalize_scene_plan,
)
from services.scene_assets import find_asset_candidates, resolve_scene_assets
from services.world_store import save_world


class ScenePlannerTests(unittest.TestCase):
    def _plan(self):
        return {
            "sceneKind": "indoor",
            "prompt": "A small cabin with a stove and a chair",
            "seed": 23,
            "bounds": {"width": 8, "depth": 8, "height": 3},
            "objects": [
                {"id": "room", "name": "Cabin room", "role": "room", "size": [6, 3, 6]},
                {"id": "stove", "name": "Wood stove", "role": "hero", "size": [1, 1.5, 1]},
                {"id": "kettle", "name": "Kettle", "role": "prop", "size": [0.4, 0.3, 0.4], "aliases": "tea kettle"},
                {"id": "chair", "name": "Chair", "role": "context", "size": [1, 1, 1]},
            ],
            "relations": [
                {"subject": "stove", "type": "floor", "object": "room"},
                {"subject": "kettle", "type": "on", "object": "stove"},
                {"subject": "chair", "type": "near", "object": "stove"},
            ],
        }

    def test_normalizes_embodiedgen_aliases_and_stable_ids(self):
        plan = normalize_scene_plan(
            {
                "assets": [{"id": "lamp", "name": "Desk lamp", "aliases": ["lamp", "灯"]}],
            }
        )
        self.assertEqual(plan.objects[0].aliases, ["lamp", "灯"])
        # Relations to unknown objects are rejected before a potentially
        # ambiguous layout.
        with self.assertRaises(ScenePlanError):
            compile_scene_plan(
                {
                    "objects": [{"id": "lamp", "name": "Desk lamp"}],
                    "relations": [{"subject": "lamp", "type": "floor", "object": "missing"}],
                }
            )

    def test_layout_is_deterministic_and_respects_on_relation(self):
        first = compile_scene_plan(self._plan())
        second = compile_scene_plan(self._plan())
        self.assertEqual(first["instances"], second["instances"])
        stove = next(item for item in first["instances"] if item["objectId"] == "stove")
        kettle = next(item for item in first["instances"] if item["objectId"] == "kettle")
        self.assertEqual(kettle["position"][0], stove["position"][0])
        self.assertEqual(kettle["position"][2], stove["position"][2])
        self.assertGreater(kettle["position"][1], stove["position"][1])

    def test_inside_relation_keeps_child_inside_container_volume(self):
        plan = compile_scene_plan(
            {
                "bounds": {"width": 8, "depth": 8, "height": 4},
                "objects": [
                    {"id": "room", "name": "Room", "role": "room", "size": [6, 4, 6]},
                    {"id": "cabinet", "name": "Cabinet", "role": "context", "size": [2, 2, 2]},
                    {"id": "box", "name": "Box", "role": "hero", "size": [0.5, 0.5, 0.5]},
                ],
                "relations": [
                    {"subject": "cabinet", "type": "floor", "object": "room"},
                    {"subject": "box", "type": "inside", "object": "cabinet"},
                ],
            }
        )
        cabinet = next(item for item in plan["instances"] if item["objectId"] == "cabinet")
        box = next(item for item in plan["instances"] if item["objectId"] == "box")
        for axis in (0, 2):
            self.assertLessEqual(abs(box["position"][axis] - cabinet["position"][axis]), 0.75)
        self.assertGreaterEqual(box["position"][1], cabinet["position"][1] - 0.75)
        self.assertLessEqual(box["position"][1], cabinet["position"][1] + 0.75)
        self.assertFalse(any(item.get("severity") == "warning" for item in plan["diagnostics"]))

    def test_object_cannot_have_two_support_relations(self):
        with self.assertRaises(ScenePlanError):
            compile_scene_plan(
                {
                    "objects": [
                        {"id": "room", "name": "Room", "role": "room"},
                        {"id": "lamp", "name": "Lamp"},
                    ],
                    "relations": [
                        {"subject": "lamp", "type": "floor", "object": "room"},
                        {"subject": "lamp", "type": "inside", "object": "room"},
                    ],
                }
            )

    def test_world_adapter_preserves_existing_instance_contract(self):
        world = {"schema_version": 1, "kind": "polykit.world", "id": "cabin", "spec": {}}
        updated = apply_scene_plan_to_world(world, self._plan())
        self.assertEqual(updated["scene_plan"]["kind"], "polykit.scene-plan")
        instance = next(item for item in updated["instances"] if item["protoId"] == "stove")
        self.assertIn("regionId", instance)
        self.assertEqual(updated["spec"]["scene_plan"]["sceneKind"], "indoor")

    def test_world_adapter_reuses_attached_mesh_on_recompile(self):
        world = {
            "schema_version": 1,
            "kind": "polykit.world",
            "id": "cabin",
            "spec": {},
            "artifacts": {
                "stove": {
                    "mesh": {
                        "workspace_path": "Workflows/stove.glb",
                        "run_id": "run-1",
                    }
                }
            },
        }
        updated = apply_scene_plan_to_world(world, self._plan())
        stove = next(item for item in updated["scene_plan"]["objects"] if item["id"] == "stove")
        self.assertEqual(stove["asset"]["workspacePath"], "Workflows/stove.glb")
        self.assertEqual(stove["asset"]["runId"], "run-1")

    def test_workspace_asset_resolution_uses_sidecar_aliases(self):
        with tempfile.TemporaryDirectory(prefix="polykit-scene-assets-") as td:
            workspace = Path(td)
            mesh = workspace / "Workflows" / "hero_prop.glb"
            mesh.parent.mkdir(parents=True)
            mesh.write_bytes(b"glb")
            mesh.with_name("hero_prop.asset.json").write_text(
                '{"assetId":"prop-hero","name":"Old wood stove","aliases":["炉子","stove"],"category":"prop"}',
                encoding="utf-8",
            )
            matches = find_asset_candidates("stove", workspace=workspace)
            self.assertEqual(matches[0]["asset_id"], "prop-hero")
            plan = normalize_scene_plan({"objects": [{"id": "stove", "name": "Stove", "category": "prop"}]})
            resolved = resolve_scene_assets(plan, workspace=workspace)
            self.assertEqual(resolved.objects[0].asset.workspace_path, "Workflows/hero_prop.glb")


class ScenePlanRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-scene-plan-")
        runtime_paths.update(workspace_dir=Path(self._tmp.name))

    def tearDown(self):
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    async def test_route_persists_compiled_plan_on_existing_world(self):
        save_world("cabin", {"schema_version": 1, "kind": "polykit.world", "id": "cabin", "spec": {}})
        request = ScenePlanCompileRequest(
            objects=[{"id": "room", "name": "Cabin", "role": "room", "size": [4, 3, 4]}],
            relations=[],
        )
        response = await compile_world_scene_plan("cabin", request)
        self.assertEqual(response["world_id"], "cabin")
        self.assertEqual(response["world"]["scene_plan"]["sceneId"], "cabin")
        self.assertEqual(response["world"]["instances"][0]["protoId"], "room")

    async def test_composition_builder_preserves_plan_instances(self):
        mesh = Path(self._tmp.name) / "Workflows" / "lamp.glb"
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b"glb")
        world = {
            "schema_version": 1,
            "kind": "polykit.world",
            "id": "cabin",
            "artifacts": {},
            "scene_plan": {
                "objects": [
                    {
                        "id": "lamp",
                        "name": "Lamp",
                        "asset": {"workspacePath": "Workflows/lamp.glb"},
                    }
                ],
                "instances": [
                    {
                        "id": "instance_lamp",
                        "objectId": "lamp",
                        "position": [1, 0, 2],
                        "rotation": [0, 45, 0],
                        "scale": 1.5,
                    }
                ],
            },
        }
        request = _build_scene_composition_workflow(
            world,
            world_id="cabin",
            collection="Scenes",
            output_name="cabin",
            allow_missing=False,
        )
        self.assertEqual(request.prompt["compose"].class_type, "scene-composer/compose")
        self.assertEqual(request.prompt["compose"].inputs["mesh"], [["asset_lamp", "mesh"]])
        self.assertEqual(request.prompt["compose"].inputs["params"]["placements"][0]["position"], [1, 0, 2])
        self.assertEqual(request.metadata["world_id"], "cabin")

    async def test_composition_builder_omits_unmeshed_room_container(self):
        mesh = Path(self._tmp.name) / "Workflows" / "lamp.glb"
        mesh.parent.mkdir(parents=True, exist_ok=True)
        mesh.write_bytes(b"glb")
        world = {
            "schema_version": 1,
            "kind": "polykit.world",
            "id": "cabin",
            "scene_plan": {
                "objects": [
                    {"id": "room", "name": "Cabin", "role": "room", "size": [4, 3, 4]},
                    {"id": "lamp", "name": "Lamp", "asset": {"workspacePath": "Workflows/lamp.glb"}},
                ],
                "instances": [
                    {"id": "instance_room", "objectId": "room", "position": [0, 0, 0]},
                    {"id": "instance_lamp", "objectId": "lamp", "position": [0, 0, 0]},
                ],
            },
        }
        request = _build_scene_composition_workflow(
            world,
            world_id="cabin",
            collection="Scenes",
            output_name="cabin",
            allow_missing=False,
        )
        self.assertEqual(request.prompt["compose"].inputs["mesh"], [["asset_lamp", "mesh"]])
        self.assertEqual(request.metadata["missing_objects"], [])

    async def test_compose_route_delegates_to_canonical_workflow_run(self):
        mesh = Path(self._tmp.name) / "Workflows" / "lamp.glb"
        mesh.parent.mkdir(parents=True, exist_ok=True)
        mesh.write_bytes(b"glb")
        save_world(
            "cabin",
            {
                "schema_version": 1,
                "kind": "polykit.world",
                "id": "cabin",
                "scene_plan": {
                    "objects": [{"id": "lamp", "name": "Lamp", "asset": {"workspacePath": "Workflows/lamp.glb"}}],
                    "instances": [{"id": "instance_lamp", "objectId": "lamp", "position": [0, 0, 0]}],
                },
            },
        )
        with patch("routers.workflow_runs.execute_workflow", autospec=True) as execute:
            execute.return_value = {"run_id": "run-1", "status": "pending"}
            response = await compose_world_scene(
                "cabin",
                SceneComposeRequest(output_name="cabin"),
                BackgroundTasks(),
            )
        self.assertEqual(response["run_id"], "run-1")
        submitted = execute.await_args.args[0]
        self.assertEqual(submitted.metadata["world_id"], "cabin")
        self.assertEqual(submitted.prompt["compose"].class_type, "scene-composer/compose")


if __name__ == "__main__":
    unittest.main()
