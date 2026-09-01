import json
import tempfile
import unittest
from pathlib import Path

import trimesh
from trimesh.visual.material import PBRMaterial

from services.process_runner import run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/asset-evidence"


class AssetEvidenceProcessorTests(unittest.TestCase):
    def test_component_id_sheet_renders_distinct_component_colors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "ids.glb"
            scene = trimesh.Scene()
            scene.add_geometry(trimesh.creation.box(extents=(1, 1, 1)), geom_name="body", node_name="body")
            scene.add_geometry(
                trimesh.creation.icosphere(subdivisions=1, radius=0.3),
                geom_name="accent",
                node_name="accent",
                transform=trimesh.transformations.translation_matrix((0.9, 0, 0)),
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
                {"_node_id": "component-id-sheet", "views": 4, "image_size": 128},
                str(workspace),
                str(temp),
            )

            from PIL import Image

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (512, 128))
                colors = {color for _count, color in image.convert("RGB").getcolors(maxcolors=512 * 128) or []}
                self.assertGreaterEqual(len(colors - {(255, 255, 255)}), 2)
            self.assertEqual(report["kind"], "polykit.component-id-sheet")
            self.assertEqual(len(report["components"]), 2)
            self.assertNotEqual(report["components"][0]["color"], report["components"][1]["color"])
            self.assertEqual(result["metadata"]["component_count"], 2)

    def test_turntable_evidence_renders_contact_sheet_and_view_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "turntable.glb"
            scene = trimesh.Scene()
            scene.add_geometry(trimesh.creation.icosphere(subdivisions=1), geom_name="body", node_name="body")
            scene.add_geometry(
                trimesh.creation.box(extents=(0.4, 0.4, 0.4)),
                geom_name="accent",
                node_name="accent",
                transform=trimesh.transformations.translation_matrix((0.8, 0.0, 0.0)),
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
                {"_node_id": "turntable-evidence", "views": 4, "image_size": 256},
                str(workspace),
                str(temp),
            )

            from PIL import Image

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreaterEqual(image.width, 512)
                self.assertGreaterEqual(image.height, 256)
            self.assertEqual(report["kind"], "polykit.turntable-evidence")
            self.assertEqual(len(report["render"]["views"]), 4)
            self.assertEqual(report["render"]["views"][0]["azimuth"], 0.0)
            self.assertEqual(result["metadata"]["view_count"], 4)

    def test_normalize_mesh_scales_centers_and_grounds_asset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "offset.glb"
            scene = trimesh.Scene()
            scene.add_geometry(
                trimesh.creation.box(extents=(2, 4, 2)),
                geom_name="body",
                node_name="body",
                transform=trimesh.transformations.translation_matrix((5, 2, 10)),
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
                {
                    "_node_id": "normalize-mesh",
                    "target_size": 2,
                    "up_axis": "Y",
                    "center_horizontal": True,
                    "ground": True,
                },
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            normalized = trimesh.load(output, force="scene", process=False)
            self.assertTrue(output.is_file())
            self.assertAlmostEqual(float(normalized.bounds[0][0]), -0.5, places=5)
            self.assertAlmostEqual(float(normalized.bounds[1][0]), 0.5, places=5)
            self.assertAlmostEqual(float(normalized.bounds[0][1]), 0.0, places=5)
            self.assertAlmostEqual(float(normalized.bounds[1][1]), 2.0, places=5)
            self.assertAlmostEqual(float(normalized.bounds[0][2]), -0.5, places=5)
            self.assertAlmostEqual(float(normalized.bounds[1][2]), 0.5, places=5)
            self.assertAlmostEqual(report["checks"]["maxExtent"], 2.0, places=5)
            self.assertAlmostEqual(report["checks"]["groundCoordinate"], 0.0, places=5)
            self.assertEqual(result["metadata"]["evidence_kind"], "mesh-normalization")

    def test_material_audit_reports_pbr_channels_and_passes_gate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "painted.glb"
            mesh = trimesh.creation.box()
            mesh.visual.material = PBRMaterial(
                name="paint",
                baseColorFactor=[0.2, 0.3, 0.4, 1.0],
                metallicFactor=0.7,
                roughnessFactor=0.25,
                emissiveFactor=[0.1, 0.0, 0.0],
            )
            trimesh.Scene(mesh).export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "material-audit"},
                str(workspace),
                str(temp),
            )

            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(report["kind"], "polykit.material-audit")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(len(report["materials"]), 1)
            material = report["materials"][0]
            self.assertEqual(material["name"], "paint")
            self.assertEqual(material["missingRequiredChannels"], [])
            self.assertTrue(material["channels"]["baseColor"]["present"])
            self.assertEqual(material["channels"]["baseColor"]["value"], [0.2, 0.298039, 0.4, 1.0])
            self.assertEqual(material["channels"]["roughness"]["value"], 0.25)
            self.assertEqual(material["channels"]["emissive"]["source"], "material-emissive")

    def test_material_audit_flags_component_without_material(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "plain.glb"
            trimesh.creation.box().export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "material-audit"},
                str(workspace),
                str(temp),
            )

            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "needs_review")
            self.assertEqual(len(report["checks"]["missingMaterialComponents"]), 1)
            self.assertIn("baseColor", report["materials"][0]["missingRequiredChannels"])

    def test_component_audit_reports_components_footprints_and_near_relation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "assembly.glb"
            scene = trimesh.Scene()
            scene.add_geometry(trimesh.creation.box(extents=(1, 1, 1)), geom_name="base", node_name="base")
            scene.add_geometry(
                trimesh.creation.box(extents=(1, 1, 1)),
                geom_name="lid",
                node_name="lid",
                transform=trimesh.transformations.translation_matrix((1.01, 0, 0)),
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
                {"_node_id": "component-audit", "near_tolerance": 0.02},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report_path = Path(str(result["sidecars"][0]))
            self.assertTrue(output.is_file())
            self.assertEqual(output.read_bytes(), source.read_bytes())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["kind"], "polykit.component-audit")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["scene"]["componentCount"], 2)
            self.assertEqual({item["id"] for item in report["components"]}, {"base", "lid"})
            self.assertTrue(all("xz" in item["footprints"] for item in report["components"]))
            self.assertEqual(report["checks"]["nearCount"], 1)
            self.assertEqual(report["relations"][0]["relation"], "near")
            self.assertAlmostEqual(report["relations"][0]["gap"], 0.01, places=5)
            self.assertEqual(result["metadata"]["component_count"], 2)

    def test_component_audit_marks_zero_extent_mesh_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "plane.glb"
            plane = trimesh.Trimesh(
                vertices=[[0, 0, 0], [1, 0, 0], [0, 0, 1]],
                faces=[[0, 1, 2]],
                process=False,
            )
            plane.export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "component-audit"},
                str(workspace),
                str(temp),
            )

            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "needs_review")
            self.assertEqual(len(report["checks"]["zeroExtentComponents"]), 1)


if __name__ == "__main__":
    unittest.main()
