import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from services.process_runner import run_processor


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = REPO_ROOT / "src/areas/workflows/nodes/environment-production"
FIXTURE_PATH = REPO_ROOT / "fixtures/terrain/compiler-v2.json"
SURFACE_FIXTURE_PATH = REPO_ROOT / "fixtures/terrain/surface-fields-v1.json"


def _load_module(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, PACK_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_compiler():
    return _load_module("terrain_fields", "terrain_fields.py")


def _load_coverage_compiler():
    return _load_module("polykit_test_terrain_coverage", "terrain_coverage.py")


def _load_surface_compiler():
    return _load_module("polykit_test_surface_fields", "surface_fields.py")


class TerrainCompilerV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.surface_fixture = json.loads(SURFACE_FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.compiler = _load_compiler()
        cls.coverage_compiler = _load_coverage_compiler()
        cls.surface_compiler = _load_surface_compiler()

    def _run(self, root: Path, descriptor: dict, *, entry: str = "processor_v2.py", include_water: bool = False) -> dict:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        temp = root / "tmp"
        temp.mkdir(exist_ok=True)
        return run_processor(
            PACK_DIR,
            entry,
            {"text": json.dumps(descriptor)},
            {"_node_id": "terrain-mesh", "resolution": self.fixture["resolution"], "include_water": include_water},
            str(workspace),
            str(temp),
        )

    def test_python_fields_match_shared_browser_fixture(self) -> None:
        program = self.compiler.parse_program(self.fixture["spec"], resolution=self.fixture["resolution"])
        fields = self.compiler.compile_fields(program)
        for sample in self.fixture["samples"]:
            actual = self.compiler.grid_sample(fields, *sample["grid"])
            expected = sample["expected"]
            self.assertAlmostEqual(actual["height"], expected["height"], places=6)
            self.assertEqual(actual["dominant"], expected["dominant"])
            self.assertEqual(len(actual["weights"]), len(expected["weights"]))
            for actual_weight, expected_weight in zip(actual["weights"], expected["weights"]):
                self.assertAlmostEqual(actual_weight, expected_weight, places=6)

    def test_surface_fields_match_shared_browser_fixture(self) -> None:
        fixture = self.surface_fixture
        self.assertEqual(list(self.surface_compiler.SURFACE_KINDS), fixture["surfaceOrder"])
        surface_weights, dominant_surface = self.surface_compiler.compile_surface_fields(
            heights=np.asarray(fixture["heights"], dtype=np.float32),
            region_weights=[np.asarray(weights, dtype=np.float32) for weights in fixture["regionWeights"]],
            region_surfaces=fixture["regionSurfaces"],
            resolution=fixture["resolution"],
            size=fixture["size"],
            sea_level=fixture["seaLevel"],
        )
        for sample in fixture["samples"]:
            actual = self.surface_compiler.grid_surface_sample(
                surface_weights,
                dominant_surface,
                *sample["grid"],
                fixture["resolution"],
            )
            expected = sample["expected"]
            self.assertEqual(actual["dominantSurface"], expected["dominantSurface"])
            self.assertEqual(len(actual["surfaceWeights"]), len(expected["surfaceWeights"]))
            for actual_weight, expected_weight in zip(actual["surfaceWeights"], expected["surfaceWeights"]):
                self.assertAlmostEqual(actual_weight, expected_weight, places=6)

    def test_world_coverage_fills_the_domain_and_local_regions_consume_base_weight(self) -> None:
        base_region = copy.deepcopy(self.fixture["spec"]["regions"][2])
        base_region.update({
            "id": "base",
            "kind": "plains",
            "coverage": "world",
            "center": [0.5, 0.5],
            "radius": 0.3,
            "irregularity": 0,
        })
        local_region = copy.deepcopy(self.fixture["spec"]["regions"][0])
        local_region.update({
            "id": "local",
            "coverage": "local",
            "center": [0.5, 0.5],
            "radius": 0.2,
            "irregularity": 0,
        })
        descriptor = copy.deepcopy(self.fixture["spec"])
        descriptor["regions"] = [base_region, local_region]
        descriptor["rivers"] = []

        program = self.coverage_compiler.parse_program(descriptor, resolution=self.fixture["resolution"])
        fields = self.coverage_compiler.compile_fields(program)
        res = self.fixture["resolution"]
        center = (res // 2) * res + res // 2
        corner = 0
        self.assertAlmostEqual(float(fields.region_weights[0][corner]), 1.0, places=6)
        self.assertAlmostEqual(float(fields.region_weights[1][corner]), 0.0, places=6)
        self.assertAlmostEqual(float(fields.region_weights[0][center]), 0.0, places=6)
        self.assertAlmostEqual(float(fields.region_weights[1][center]), 1.0, places=6)
        for index in range(res * res):
            self.assertAlmostEqual(sum(float(weights[index]) for weights in fields.region_weights), 1.0, places=6)
        self.assertFalse(np.any(fields.dominant < 0))

        descriptor["regions"] = [base_region]
        single_program = self.coverage_compiler.parse_program(descriptor, resolution=self.fixture["resolution"])
        single_fields = self.coverage_compiler.compile_fields(single_program)
        self.assertTrue(np.allclose(single_fields.region_weights[0], 1.0))
        self.assertTrue(np.all(single_fields.dominant == 0))

    def test_v2_processor_exports_region_blended_terrain(self) -> None:
        descriptor = copy.deepcopy(self.fixture["spec"])
        descriptor["regions"][0]["surface"] = "snow"
        descriptor["regions"][1]["surface"] = "forest"
        descriptor["regions"][2]["surface"] = "grass"
        with tempfile.TemporaryDirectory() as td:
            result = self._run(Path(td), descriptor)
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            scene = trimesh.load(Path(str(result["filePath"])), force="scene", process=False)
            terrain = scene.geometry["terrain"]
            colors = np.asarray(terrain.visual.vertex_colors)[:, :3]
            self.assertEqual(result["metadata"]["terrain_version"], 2)
            self.assertEqual(result["metadata"]["compiler_version"], 2)
            self.assertEqual(result["metadata"]["surface_field_mode"], "region-altitude-slope")
            self.assertEqual(report["compiler"]["version"], 2)
            self.assertEqual(report["surface"]["materialMode"], "region-vertex-blend")
            self.assertEqual(report["surface"]["fieldMode"], "region-altitude-slope")
            self.assertEqual(report["surface"]["channels"], list(self.surface_compiler.SURFACE_KINDS))
            self.assertEqual(report["source"]["terrainVersion"], 2)
            self.assertEqual(report["regions"][0]["surface"], "snow")
            self.assertEqual(report["regions"][0]["coverage"], "local")
            self.assertEqual(sum(report["surface"]["dominantSurfaceCounts"].values()), self.fixture["resolution"] ** 2)
            self.assertEqual(report["surface"]["fieldHash"], result["metadata"]["surface_field_hash"])
            self.assertGreater(len(np.unique(colors, axis=0)), 3)
            self.assertEqual(len(terrain.vertices), self.fixture["resolution"] ** 2)

    def test_v2_irregularity_changes_the_compiled_field(self) -> None:
        base = copy.deepcopy(self.fixture["spec"])
        flat_masks = copy.deepcopy(base)
        for region in flat_masks["regions"]:
            region["irregularity"] = 0
        base_fields = self.compiler.compile_fields(self.compiler.parse_program(base, resolution=self.fixture["resolution"]))
        flat_fields = self.compiler.compile_fields(self.compiler.parse_program(flat_masks, resolution=self.fixture["resolution"]))
        self.assertFalse(np.array_equal(base_fields.heights, flat_fields.heights))
        self.assertTrue(any(
            not np.array_equal(base_weights, flat_weights)
            for base_weights, flat_weights in zip(base_fields.region_weights, flat_fields.region_weights)
        ))

    def test_versioned_entry_preserves_legacy_terrain_without_version(self) -> None:
        descriptor = {
            "seed": 11,
            "size": 18,
            "seaLevel": 0,
            "regions": [
                {"id": "ridge", "kind": "mountain", "center": [0.5, 0.5], "radius": 0.42, "amplitude": 4, "roughness": 0.6}
            ],
            "rivers": [],
        }
        with tempfile.TemporaryDirectory() as legacy_td, tempfile.TemporaryDirectory() as versioned_td:
            legacy_result = self._run(Path(legacy_td), descriptor, entry="processor.py")
            versioned_result = self._run(Path(versioned_td), descriptor, entry="processor_v2.py")
            legacy_report = json.loads(Path(str(legacy_result["sidecars"][0])).read_text(encoding="utf-8"))
            versioned_report = json.loads(Path(str(versioned_result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(legacy_report["terrain"]["vertexHash"], versioned_report["terrain"]["vertexHash"])
            self.assertEqual(legacy_report["schemaVersion"], 1)
            self.assertEqual(versioned_report["schemaVersion"], 1)


if __name__ == "__main__":
    unittest.main()
