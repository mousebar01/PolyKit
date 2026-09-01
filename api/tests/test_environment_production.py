import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import trimesh

from services.process_runner import ProcessExecutionError, run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/environment-production"


class TerrainMeshProcessorTests(unittest.TestCase):
    def _run(self, root: Path, descriptor: dict, *, include_water: bool = True, node_id: str = "terrain-mesh") -> dict:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        temp = root / "tmp"
        temp.mkdir(exist_ok=True)
        return run_processor(
            PACK_DIR,
            "processor.py",
            {"text": json.dumps(descriptor)},
            {"_node_id": node_id, "resolution": 18, "include_water": include_water},
            str(workspace),
            str(temp),
        )

    def test_builds_terrain_and_water_glb_with_deterministic_report(self) -> None:
        descriptor = {
            "seed": 42,
            "size": 24,
            "seaLevel": 0,
            "regions": [
                {"id": "ridge", "kind": "mountain", "center": [0.48, 0.42], "radius": 0.42, "amplitude": 5, "roughness": 0.7},
                {"id": "valley", "kind": "plains", "center": [0.55, 0.68], "radius": 0.3, "amplitude": 1.2, "roughness": 0.35},
            ],
            "rivers": [{"id": "main", "path": [[0.08, 0.2], [0.45, 0.55], [0.92, 0.82]], "width": 0.35, "depth": 1.1}],
        }
        with tempfile.TemporaryDirectory() as first_td, tempfile.TemporaryDirectory() as second_td:
            first = self._run(Path(first_td), descriptor)
            second = self._run(Path(second_td), descriptor)
            first_output = Path(str(first["filePath"]))
            first_report = json.loads(Path(str(first["sidecars"][0])).read_text(encoding="utf-8"))
            second_report = json.loads(Path(str(second["sidecars"][0])).read_text(encoding="utf-8"))
            scene = trimesh.load(first_output, force="scene", process=False)
            terrain = scene.geometry["terrain"]
            self.assertTrue(first_output.is_file())
            self.assertIsInstance(scene, trimesh.Scene)
            self.assertEqual(set(scene.geometry), {"terrain", "water"})
            self.assertEqual(len(terrain.vertices), 18 * 18)
            self.assertEqual(len(terrain.faces), 2 * 17 * 17)
            self.assertGreater(float(terrain.bounds[1][1]), float(terrain.bounds[0][1]))
            self.assertEqual(first_report["kind"], "polykit.terrain-mesh")
            self.assertEqual(first_report["source"]["riverCount"], 1)
            self.assertTrue(first_report["water"]["included"])
            self.assertEqual(first_report["terrain"]["vertexHash"], second_report["terrain"]["vertexHash"])
            self.assertEqual(first["metadata"]["vertex_hash"], hashlib.sha256(terrain.vertices.astype("float32").tobytes()).hexdigest())

    def test_can_disable_water_and_reject_invalid_river_path(self) -> None:
        descriptor = {"seed": 3, "size": 8, "regions": []}
        with tempfile.TemporaryDirectory() as td:
            result = self._run(Path(td), descriptor, include_water=False)
            scene = trimesh.load(Path(str(result["filePath"])), force="scene", process=False)
            self.assertEqual(set(scene.geometry), {"terrain"})
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ProcessExecutionError):
                self._run(Path(td), {"size": 8, "rivers": [{"path": [[0.1, 0.2]], "width": 0.2}]})

    def test_city_blockout_exports_separate_roads_and_buildings_deterministically(self) -> None:
        descriptor = {
            "seed": 17,
            "width": 30,
            "depth": 24,
            "rows": 2,
            "columns": 3,
            "roadWidth": 2.5,
            "setback": 0.5,
            "buildingHeight": [5, 14],
            "buildingDensity": 1.0,
        }
        with tempfile.TemporaryDirectory() as first_td, tempfile.TemporaryDirectory() as second_td:
            first = self._run(Path(first_td), descriptor, node_id="city-blockout")
            second = self._run(Path(second_td), descriptor, node_id="city-blockout")
            first_output = Path(str(first["filePath"]))
            first_report = json.loads(Path(str(first["sidecars"][0])).read_text(encoding="utf-8"))
            second_report = json.loads(Path(str(second["sidecars"][0])).read_text(encoding="utf-8"))
            scene = trimesh.load(first_output, force="scene", process=False)
            building_names = [name for name in scene.geometry if name.startswith("building-")]
            road_names = [name for name in scene.geometry if name.startswith("road-")]
            self.assertEqual(len(building_names), 6)
            self.assertEqual(len(road_names), 7)
            self.assertEqual(first_report["summary"]["buildingCount"], 6)
            self.assertEqual(first_report["summary"]["roadCount"], 7)
            self.assertEqual(first_report["summary"]["layoutHash"], second_report["summary"]["layoutHash"])
            self.assertEqual(first["metadata"]["layout_hash"], first_report["summary"]["layoutHash"])

    def test_city_blockout_rejects_lots_consumed_by_roads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ProcessExecutionError):
                self._run(Path(td), {"width": 8, "depth": 8, "rows": 2, "columns": 2, "roadWidth": 3.0, "setback": 1.0}, node_id="city-blockout")


if __name__ == "__main__":
    unittest.main()
