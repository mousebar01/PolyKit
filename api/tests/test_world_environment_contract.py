import tempfile
import unittest
from pathlib import Path

from services.runtime_paths import runtime_paths
from services.world_domain import create_world_document
from services.world_store import WorldStoreError, save_world


def _environment(regions: list[dict]) -> dict:
    return {
        "name": "Terrain contract test",
        "logline": "A small authored terrain contract.",
        "seed": 42,
        "size": 120,
        "seaLevel": 0,
        "sky": {},
        "regions": regions,
        "rivers": [],
        "assets": [],
        "relations": [],
    }


def _region(**overrides) -> dict:
    value = {
        "id": "ridge",
        "name": "Ridge",
        "center": [0.5, 0.5],
        "radius": 0.35,
        "irregularity": 0.4,
        "baseElevation": 2,
        "amplitude": 18,
        "roughness": 0.6,
        "material": {
            "name": "snow rock",
            "texturePrompt": "snow over dark rock",
            "color": "#d8dfe3",
            "tiling": 6,
        },
    }
    value.update(overrides)
    return value


class WorldEnvironmentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-world-environment-test-")
        runtime_paths.update(workspace_dir=Path(self._tmp.name))

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def test_new_worlds_record_current_terrain_authoring_generation(self) -> None:
        world = create_world_document(name="New terrain world")
        self.assertEqual(world["authoring"], {"terrain_version": 2})
        self.assertIsNone(world["runtime"]["build"]["environment"])

    def test_new_world_environment_compiles_landform_and_surface_into_v2_contract(self) -> None:
        world = create_world_document(name="Snow mountain")
        world["runtime"]["build"]["environment"] = _environment([
            _region(landform="MOUNTAIN", surface="SNOW"),
            _region(id="wood", name="Wood", surface="forest"),
        ])

        saved = save_world(world["id"], world)
        environment = saved["runtime"]["build"]["environment"]
        self.assertEqual(environment["terrainVersion"], 2)

        ridge = environment["regions"][0]
        self.assertEqual(ridge["landform"], "mountain")
        self.assertEqual(ridge["surface"], "snow")
        self.assertEqual(ridge["kind"], "mountain")
        self.assertEqual(ridge["coverage"], "local")

        # A surface does not imply geometry. A new region with no landform uses
        # the conservative plains profile rather than treating forest as shape.
        wood = environment["regions"][1]
        self.assertEqual(wood["surface"], "forest")
        self.assertEqual(wood["kind"], "plains")
        self.assertEqual(wood["coverage"], "local")

    def test_single_region_new_world_defaults_to_world_coverage(self) -> None:
        world = create_world_document(name="One complete terrain")
        world["runtime"]["build"]["environment"] = _environment([
            _region(landform="hills", surface="grass"),
        ])

        saved = save_world(world["id"], world)
        region = saved["runtime"]["build"]["environment"]["regions"][0]
        self.assertEqual(region["coverage"], "world")
        self.assertEqual(region["kind"], "hills")
        self.assertEqual(region["surface"], "grass")

    def test_single_region_can_explicitly_remain_local(self) -> None:
        world = create_world_document(name="One local volcano")
        world["runtime"]["build"]["environment"] = _environment([
            _region(landform="volcanic", surface="rock", coverage="LOCAL"),
        ])

        saved = save_world(world["id"], world)
        region = saved["runtime"]["build"]["environment"]["regions"][0]
        self.assertEqual(region["coverage"], "local")

    def test_explicit_landform_wins_over_legacy_kind_for_new_worlds(self) -> None:
        world = create_world_document(name="Transition contract")
        world["runtime"]["build"]["environment"] = _environment([
            _region(kind="snow", landform="mountain", surface="snow"),
        ])

        saved = save_world(world["id"], world)
        region = saved["runtime"]["build"]["environment"]["regions"][0]
        self.assertEqual(region["kind"], "mountain")
        self.assertEqual(region["surface"], "snow")
        self.assertEqual(region["coverage"], "world")

    def test_new_world_rejects_multiple_world_coverage_regions(self) -> None:
        world = create_world_document(name="Invalid world bases")
        world["runtime"]["build"]["environment"] = _environment([
            _region(coverage="world"),
            _region(id="other", name="Other", coverage="world"),
        ])
        with self.assertRaisesRegex(WorldStoreError, "at most one coverage=world"):
            save_world(world["id"], world)

    def test_legacy_worlds_remain_versionless_and_keep_existing_kind(self) -> None:
        world = create_world_document(name="Legacy terrain")
        world.pop("authoring")
        environment = _environment([_region(kind="forest")])
        world["runtime"]["build"]["environment"] = environment

        saved = save_world(world["id"], world)
        saved_environment = saved["runtime"]["build"]["environment"]
        self.assertNotIn("terrainVersion", saved_environment)
        self.assertEqual(saved_environment["regions"][0]["kind"], "forest")
        self.assertNotIn("landform", saved_environment["regions"][0])
        self.assertNotIn("surface", saved_environment["regions"][0])
        self.assertNotIn("coverage", saved_environment["regions"][0])

    def test_new_world_rejects_unknown_explicit_landform(self) -> None:
        world = create_world_document(name="Invalid terrain")
        world["runtime"]["build"]["environment"] = _environment([
            _region(landform="floating-islands"),
        ])
        with self.assertRaisesRegex(WorldStoreError, "regions\\[0\\]\\.landform"):
            save_world(world["id"], world)


if __name__ == "__main__":
    unittest.main()
