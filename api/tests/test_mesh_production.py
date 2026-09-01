import json
import tempfile
import unittest
from pathlib import Path

import trimesh

from services.process_runner import run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/mesh-production"


class MeshProductionProcessorTests(unittest.TestCase):
    def test_collision_mesh_builds_convex_proxy_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "assembly.glb"
            scene = trimesh.Scene()
            scene.add_geometry(trimesh.creation.box(extents=(2, 1, 1)), geom_name="body", node_name="body")
            scene.add_geometry(
                trimesh.creation.icosphere(subdivisions=1, radius=0.4),
                geom_name="cap",
                node_name="cap",
                transform=trimesh.transformations.translation_matrix((1.1, 0.0, 0.0)),
            )
            scene.export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "collision-mesh", "method": "convex_hull"},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            collider = trimesh.load(output, force="mesh", process=False)
            self.assertTrue(output.is_file())
            self.assertIsInstance(collider, trimesh.Trimesh)
            self.assertGreater(len(collider.faces), 0)
            self.assertEqual(report["kind"], "polykit.collision-mesh")
            self.assertEqual(report["method"]["used"], "convex_hull")
            self.assertEqual(report["source"]["componentCount"], 2)
            self.assertEqual(result["metadata"]["evidence_kind"], "collision-mesh")

    def test_lod_generate_writes_three_levels_with_reduced_faces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "dense.glb"
            scene = trimesh.Scene()
            scene.add_geometry(
                trimesh.creation.icosphere(subdivisions=3, radius=1.0),
                geom_name="body",
                node_name="body",
            )
            scene.export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "lod-generate", "lod1_ratio": 0.5, "lod2_ratio": 0.2, "min_faces": 20},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            self.assertEqual(report["kind"], "polykit.lod-generation")
            self.assertEqual([level["level"] for level in report["levels"]], ["LOD0", "LOD1", "LOD2"])
            self.assertGreater(report["levels"][0]["faces"], report["levels"][1]["faces"])
            self.assertGreater(report["levels"][1]["faces"], report["levels"][2]["faces"])
            self.assertEqual(len(result["sidecars"]), 3)
            for path in result["sidecars"][1:]:
                self.assertTrue(Path(str(path)).is_file())
                self.assertGreater(len(trimesh.load(path, force="mesh", process=False).faces), 0)
            self.assertEqual(result["metadata"]["level_count"], 3)


if __name__ == "__main__":
    unittest.main()
