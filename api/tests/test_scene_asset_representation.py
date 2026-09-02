import tempfile
import unittest
from pathlib import Path

from application.world import ResolveWorldAssetsCommand, compile_world_asset_resolution
from services.runtime_paths import runtime_paths
from services.scene_assets import infer_asset_representation, resolve_scene_asset_slots
from services.scene_planner import SceneObject, ScenePlan
from services.world_plans import compile_scene_composition_plan


class SceneAssetRepresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-scene-assets-")
        self.workspace = Path(self._tmp.name)
        (self.workspace / "Workflows").mkdir(parents=True)
        runtime_paths.update(workspace_dir=self.workspace)

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def test_representation_defaults_keep_structure_light_and_hero_expressive(self) -> None:
        building = SceneObject(id="building", name="Research building", role="context", category="structure")
        hero = SceneObject(id="terminal", name="Communications terminal", role="hero", category="prop")
        pine = SceneObject(
            id="pine",
            name="Cold pine",
            role="context",
            category="vegetation",
            constraints={"proceduralHint": "pine"},
        )

        self.assertEqual(infer_asset_representation(building), "procedural")
        self.assertEqual(infer_asset_representation(hero), "mesh_required")
        self.assertEqual(infer_asset_representation(pine), "mesh_preferred")

    def test_representation_can_be_overridden_without_schema_change(self) -> None:
        rock = SceneObject(
            id="rock",
            name="Background rock",
            role="context",
            category="rock",
            constraints={"representation": "procedural"},
        )
        self.assertEqual(infer_asset_representation(rock), "procedural")

    def test_mesh_preferred_context_is_generation_eligible_without_global_context_switch(self) -> None:
        plan = ScenePlan(
            objects=[
                SceneObject(
                    id="pine",
                    name="Cold pine",
                    role="context",
                    category="vegetation",
                    constraints={"proceduralHint": "pine"},
                )
            ]
        )
        _, decisions = resolve_scene_asset_slots(plan, workspace=self.workspace, include_context=False)
        self.assertEqual(decisions[0]["mode"], "generate")
        self.assertEqual(decisions[0]["representation"], "mesh_preferred")
        self.assertEqual(decisions[0]["status"], "preview")

    def test_world_resolution_uses_small_mesh_budgets_for_non_hero_assets(self) -> None:
        world = {
            "runtime": {
                "intent": {"prompt": "Cold abandoned research outpost"},
                "scene": {
                    "objects": [
                        {
                            "id": "terminal",
                            "name": "Communications terminal",
                            "role": "hero",
                            "category": "prop",
                            "size": [2.0, 3.0, 1.5],
                        },
                        {
                            "id": "pine",
                            "name": "Cold pine",
                            "role": "context",
                            "category": "vegetation",
                            "size": [2.5, 6.0, 2.5],
                            "constraints": {"proceduralHint": "pine"},
                        },
                        {
                            "id": "building",
                            "name": "Research building",
                            "role": "context",
                            "category": "structure",
                            "size": [8.0, 4.0, 6.0],
                        },
                    ],
                    "relations": [],
                },
            }
        }
        compiled = compile_world_asset_resolution(
            world,
            world_id="cold-valley",
            command=ResolveWorldAssetsCommand(),
        )
        by_id = {item["object_id"]: item for item in compiled.decisions}

        self.assertEqual(by_id["terminal"]["target_faces"], 100_000)
        self.assertEqual(by_id["pine"]["target_faces"], 30_000)
        self.assertEqual(by_id["building"]["mode"], "procedural")
        self.assertEqual(len(compiled.generation_plans), 2)
        budgets = {
            plan.metadata["proto_id"]: plan.prompt["optimize"].inputs["params"]["target_faces"]
            for plan in compiled.generation_plans
        }
        self.assertEqual(budgets, {"terminal": 100_000, "pine": 30_000})

    def test_scene_composer_preserves_repeated_mesh_placements(self) -> None:
        mesh_path = self.workspace / "Workflows" / "pine.glb"
        mesh_path.write_bytes(b"placeholder")
        world = {
            "runtime": {
                "version": 1,
                "scene": {
                    "objects": [
                        {
                            "id": "pine",
                            "name": "Cold pine",
                            "role": "context",
                            "size": [2.5, 6.0, 2.5],
                            "asset": {"workspacePath": "Workflows/pine.glb"},
                        }
                    ],
                    "instances": [
                        {"objectId": "pine", "position": [-4, 0, 2], "rotation": [0, 0, 0], "scale": 0.9},
                        {"objectId": "pine", "position": [5, 0, 3], "rotation": [0, 1.2, 0], "scale": 1.1},
                    ],
                    "metadata": {"layoutQuality": {"status": "pass"}},
                },
            }
        }
        plan = compile_scene_composition_plan(world, world_id="cold-valley")
        compose = plan.prompt["compose"]

        self.assertEqual(compose.inputs["mesh"], [["asset_pine", "mesh"], ["asset_pine", "mesh"]])
        placements = compose.inputs["params"]["placements"]
        self.assertEqual(len(placements), 2)
        self.assertEqual(placements[0]["position"], [-4, 0, 2])
        self.assertEqual(placements[1]["position"], [5, 0, 3])


if __name__ == "__main__":
    unittest.main()
