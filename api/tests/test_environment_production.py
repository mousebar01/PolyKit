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

    def test_room_blockout_exports_shell_openings_and_stable_layout(self) -> None:
        descriptor = {
            "width": 6,
            "depth": 5,
            "height": 3,
            "wallThickness": 0.2,
            "doors": [{"id": "entry", "wall": "front", "offset": 2.0, "width": 1.0, "height": 2.1}],
            "windows": [{"id": "view", "wall": "back", "offset": 1.3, "width": 1.6, "height": 1.1, "sill": 1.0}],
        }
        with tempfile.TemporaryDirectory() as first_td, tempfile.TemporaryDirectory() as second_td:
            first = self._run(Path(first_td), descriptor, node_id="room-blockout")
            second = self._run(Path(second_td), descriptor, node_id="room-blockout")
            output = Path(str(first["filePath"]))
            report = json.loads(Path(str(first["sidecars"][0])).read_text(encoding="utf-8"))
            second_report = json.loads(Path(str(second["sidecars"][0])).read_text(encoding="utf-8"))
            scene = trimesh.load(output, force="scene", process=False)
            names = set(scene.geometry)
            self.assertTrue(output.is_file())
            self.assertIn("floor", names)
            self.assertIn("ceiling", names)
            self.assertTrue(any(name.startswith("front-wall-segment-") for name in names))
            self.assertTrue(any(name.startswith("door-entry-front") for name in names))
            self.assertTrue(any(name.startswith("window-view-back") for name in names))
            self.assertEqual(report["kind"], "polykit.room-blockout")
            self.assertEqual(report["summary"]["openingCount"], 2)
            self.assertEqual(report["summary"]["layoutHash"], second_report["summary"]["layoutHash"])
            self.assertEqual(first["metadata"]["opening_count"], 2)
            self.assertGreater(float(scene.bounds[1][1]), 3.0)
        with tempfile.TemporaryDirectory() as variant_td:
            variant = self._run(Path(variant_td), {**descriptor, "includeCeiling": False}, node_id="room-blockout")
            variant_report = json.loads(Path(str(variant["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertNotEqual(report["summary"]["layoutHash"], variant_report["summary"]["layoutHash"])

    def test_room_blockout_rejects_overlapping_openings(self) -> None:
        descriptor = {
            "width": 4,
            "depth": 4,
            "height": 3,
            "doors": [
                {"wall": "front", "offset": 0.8, "width": 1.5},
                {"wall": "front", "offset": 1.9, "width": 1.0},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ProcessExecutionError):
                self._run(Path(td), descriptor, node_id="room-blockout")

    def test_multi_room_blockout_preserves_room_identity_and_layout(self) -> None:
        descriptor = {
            "wallThickness": 0.18,
            "includeCeiling": False,
            "rooms": [
                {"id": "living", "width": 5.0, "depth": 4.0, "height": 3.0, "position": [0.0, 0.0], "doors": [{"wall": "right", "offset": 1.2, "width": 0.9, "height": 2.1}]},
                {"id": "studio", "width": 3.5, "depth": 3.0, "height": 2.8, "position": [4.4, 0.0], "windows": [{"wall": "left", "offset": 0.8, "width": 1.2, "height": 1.0, "sill": 1.0}]},
            ],
        }
        with tempfile.TemporaryDirectory() as first_td, tempfile.TemporaryDirectory() as second_td:
            first = self._run(Path(first_td), descriptor, node_id="multi-room-blockout")
            second = self._run(Path(second_td), descriptor, node_id="multi-room-blockout")
            output = Path(str(first["filePath"]))
            report = json.loads(Path(str(first["sidecars"][0])).read_text(encoding="utf-8"))
            second_report = json.loads(Path(str(second["sidecars"][0])).read_text(encoding="utf-8"))
            scene = trimesh.load(output, force="scene", process=False)
            names = set(scene.geometry)
            self.assertTrue(output.is_file())
            self.assertIsInstance(scene, trimesh.Scene)
            self.assertIn("living-floor", names)
            self.assertIn("studio-floor", names)
            self.assertTrue(any(name.startswith("living-door-") for name in names))
            self.assertTrue(any(name.startswith("studio-window-") for name in names))
            self.assertEqual(report["kind"], "polykit.multi-room-blockout")
            self.assertEqual(report["summary"]["roomCount"], 2)
            self.assertEqual(report["summary"]["layoutHash"], second_report["summary"]["layoutHash"])
            self.assertEqual(first["metadata"]["room_count"], 2)
            self.assertGreater(float(scene.bounds[1][0]), 5.0)

    def test_multi_room_blockout_rejects_duplicate_room_ids(self) -> None:
        descriptor = {"rooms": [{"id": "same"}, {"id": "same"}]}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ProcessExecutionError):
                self._run(Path(td), descriptor, node_id="multi-room-blockout")

    def test_vegetation_scatter_exports_spaced_instances_with_stable_layout(self) -> None:
        descriptor = {"seed": 5, "size": 18, "count": 8, "types": ["tree", "pine", "rock", "grass"], "minDistance": 1.2, "relief": 1.5}
        with tempfile.TemporaryDirectory() as first_td, tempfile.TemporaryDirectory() as second_td:
            first = self._run(Path(first_td), descriptor, node_id="vegetation-scatter")
            second = self._run(Path(second_td), descriptor, node_id="vegetation-scatter")
            output = Path(str(first["filePath"]))
            report = json.loads(Path(str(first["sidecars"][0])).read_text(encoding="utf-8"))
            second_report = json.loads(Path(str(second["sidecars"][0])).read_text(encoding="utf-8"))
            scene = trimesh.load(output, force="scene", process=False)
            positions = [tuple(instance["position"]) for instance in report["instances"]]
            self.assertEqual(len(positions), 8)
            self.assertTrue(all(len(mesh.faces) > 0 for mesh in scene.geometry.values()))
            self.assertGreaterEqual(len(scene.geometry), 8)
            trunk = scene.geometry["tree-1-trunk"]
            trunk_extent = trunk.bounds[1] - trunk.bounds[0]
            self.assertGreater(float(trunk_extent[1]), float(trunk_extent[0]) * 2.0)
            self.assertTrue(all(
                ((positions[left][0] - positions[right][0]) ** 2 + (positions[left][2] - positions[right][2]) ** 2) ** 0.5 >= 1.2
                for left in range(len(positions))
                for right in range(left)
            ))
            self.assertGreater(len({round(position[1], 5) for position in positions}), 1)
            self.assertEqual(report["summary"]["layoutHash"], second_report["summary"]["layoutHash"])
            self.assertEqual(first["metadata"]["instance_count"], 8)

    def test_vegetation_scatter_rejects_unknown_type(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ProcessExecutionError):
                self._run(Path(td), {"size": 8, "types": ["tree", "unknown"]}, node_id="vegetation-scatter")


if __name__ == "__main__":
    unittest.main()
