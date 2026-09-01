import base64
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from services.process_runner import run_processor


PACK_DIR = Path(__file__).resolve().parents[2] / "src/areas/workflows/nodes/reference-evidence"


class ReferenceEvidenceProcessorTests(unittest.TestCase):
    def test_camera_guide_emits_aspect_aware_reference_camera(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "portrait.png"
            Image.new("RGB", (100, 200), (90, 110, 130)).save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "camera-guide", "distance": 4.0, "pitch": 8},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as overlay:
                self.assertEqual(overlay.size, (100, 200))
                self.assertEqual(overlay.mode, "RGBA")
            camera = report["referenceCamera"]
            self.assertEqual(camera["fovDegrees"]["value"], 38.0)
            self.assertEqual(camera["fovDegrees"]["source"], "default-guess")
            self.assertEqual(camera["position"]["distance"]["value"], 4.0)
            self.assertEqual(camera["orientation"]["pitchDegrees"]["value"], 8.0)
            self.assertEqual(result["metadata"]["evidence_kind"], "reference-camera")

    def test_camera_fit_reduces_landmark_reprojection_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "camera.png"
            Image.new("RGB", (200, 120), (80, 90, 100)).save(source)
            # These points are generated from a known camera, then fitted from
            # a deliberately offset initial FOV/distance.
            true_camera = [42.0, 3.0, -2.0, 1.0, 0.1, -0.05, 4.0]

            def project(point: tuple[float, float, float]) -> tuple[float, float]:
                fov, yaw, pitch, roll, px, py, pz = true_camera
                focal = (120 / 2.0) / math.tan(math.radians(fov) / 2.0)
                tx, ty, tz = point[0] - px, point[1] - py, pz - point[2]
                pitch_r = math.radians(pitch)
                pitched_y = ty * math.cos(pitch_r) - tz * math.sin(pitch_r)
                pitched_z = ty * math.sin(pitch_r) + tz * math.cos(pitch_r)
                yaw_r = math.radians(yaw)
                yawed_x = tx * math.cos(yaw_r) + pitched_z * math.sin(yaw_r)
                yawed_z = -tx * math.sin(yaw_r) + pitched_z * math.cos(yaw_r)
                yawed_y = pitched_y
                roll_r = math.radians(roll)
                rolled_x = yawed_x * math.cos(roll_r) - yawed_y * math.sin(roll_r)
                rolled_y = yawed_x * math.sin(roll_r) + yawed_y * math.cos(roll_r)
                return 100.0 + focal * rolled_x / yawed_z, 60.0 - focal * rolled_y / yawed_z

            worlds = [(-1, -1, 0), (1, -1, 0), (-1, 1, 0), (1, 1, 0), (0, 0, 1), (0.4, -0.6, 1.3), (-0.7, 0.3, 0.8), (0.8, 0.8, 0.4)]
            correspondences = [
                {"name": f"p{index}", "world": list(point), "observed": list(project(point))}
                for index, point in enumerate(worlds)
            ]
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source), "text": json.dumps(correspondences)},
                {
                    "_node_id": "camera-fit",
                    "initial_fov_degrees": 35.0,
                    "initial_position_z": 3.7,
                    "maximum_iterations": 60,
                    "max_rms_pixels": 0.1,
                },
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as overlay:
                self.assertEqual(overlay.size, (200, 120))
                self.assertEqual(overlay.mode, "RGBA")
            self.assertEqual(report["status"], "pass")
            self.assertGreater(report["solver"]["initialReprojectionErrorPixels"], report["solver"]["finalReprojectionErrorPixels"])
            self.assertAlmostEqual(report["camera"]["fovDegrees"], 42.0, places=2)
            self.assertEqual(result["metadata"]["evidence_kind"], "reference-camera-fit")

    def test_projection_plan_validates_runtime_bake_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "reference.png"
            Image.new("RGB", (80, 60), (70, 90, 120)).save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {
                    "_node_id": "projection-plan",
                    "mesh_id": "hero-body",
                    "projection_mode": "orthographic-front-projection",
                    "texture_size": 2048,
                    "unseen_strategy": "request-additional-view",
                },
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as overlay:
                self.assertEqual(overlay.size, (80, 60))
                self.assertEqual(overlay.mode, "RGBA")
            self.assertEqual(report["kind"], "polykit.projected-texture-plan")
            self.assertEqual(report["status"], "needs_runtime_bake")
            self.assertEqual(report["targetMeshId"], "hero-body")
            self.assertEqual(report["textureSize"], 2048)
            self.assertEqual(report["unseenRegionStrategy"]["confidence"], 0.0)
            self.assertEqual(result["metadata"]["projection_mode"], "orthographic-front-projection")

    def test_reference_compare_emits_heatmap_and_difference_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reference = root / "reference.png"
            candidate = root / "candidate.png"
            Image.new("RGB", (40, 30), (20, 30, 40)).save(reference)
            Image.new("RGB", (20, 15), (80, 90, 100)).save(candidate)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePaths": [str(reference), str(candidate)]},
                {"_node_id": "reference-compare", "pixel_threshold": 4},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as sheet:
                self.assertEqual(sheet.format, "PNG")
                self.assertEqual(sheet.size, (120, 30))
            self.assertEqual(report["kind"], "polykit.reference-compare")
            self.assertTrue(report["candidate"]["resizedToReference"])
            self.assertGreater(report["metrics"]["changedPixelRatio"], 0.99)
            self.assertEqual(result["metadata"]["evidence_kind"], "reference-compare")

    def test_divine_eye_passes_matching_alpha_silhouette_and_fails_shifted_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reference = root / "reference.png"
            matching = root / "matching.png"
            shifted = root / "shifted.png"

            def write_shape(path: Path, offset: int) -> None:
                image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                for x in range(16 + offset, 48 + offset):
                    for y in range(16, 48):
                        if 0 <= x < image.width:
                            image.putpixel((x, y), (220, 60, 40, 255))
                image.save(path)

            write_shape(reference, 0)
            write_shape(matching, 0)
            write_shape(shifted, 14)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            passed = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePaths": [str(reference), str(matching)]},
                {"_node_id": "divine-eye"},
                str(workspace),
                str(temp),
            )
            pass_report = json.loads(Path(str(passed["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(pass_report["status"], "pass")
            self.assertEqual(pass_report["signals"]["silhouetteIoU"], 1.0)
            self.assertEqual(pass_report["signals"]["globalSSIM"], 1.0)
            self.assertEqual(passed["metadata"]["evidence_kind"], "divine-eye")

            failed = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePaths": [str(reference), str(shifted)]},
                {"_node_id": "divine-eye"},
                str(workspace),
                str(temp),
            )
            fail_report = json.loads(Path(str(failed["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(fail_report["status"], "fail")
            self.assertLess(fail_report["signals"]["silhouetteIoU"], 0.85)
            self.assertTrue(fail_report["gates"]["silhouetteGateAuthoritative"])

    def test_multi_view_evidence_normalizes_three_reference_views(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            views = []
            for index, size, color in ((1, (40, 20), (220, 60, 40)), (2, (20, 40), (50, 180, 80)), (3, (30, 30), (50, 80, 220))):
                path = root / f"view-{index}.png"
                Image.new("RGB", size, color).save(path)
                views.append(path)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePaths": [str(path) for path in views]},
                {"_node_id": "multi-view-evidence", "columns": 2, "cell_height": 64},
                str(workspace),
                str(temp),
            )
            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as sheet:
                self.assertEqual(sheet.size, (256, (64 + 24) * 2))
            self.assertEqual(report["kind"], "polykit.multi-view-evidence")
            self.assertEqual(report["viewCount"], 3)
            self.assertEqual(report["layout"]["columns"], 2)
            self.assertEqual(result["metadata"]["evidence_kind"], "multi-view-evidence")

    def test_material_region_emits_localized_palette_and_pbr_gates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "materials.png"
            image = Image.new("RGB", (100, 80), (24, 30, 40))
            for x in range(25, 75):
                for y in range(20, 60):
                    image.putpixel((x, y), (180, 80, 40))
            image.save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "material-region", "material_id": "paint", "x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5, "colors": 3},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as crop:
                self.assertEqual(crop.size, (50, 40))
            self.assertEqual(report["kind"], "polykit.material-region")
            self.assertEqual(report["materialId"], "paint")
            self.assertGreater(report["metrics"]["saturationMean"], 0.3)
            self.assertGreaterEqual(len(report["palette"]), 1)
            self.assertEqual(report["pbrEvidence"]["roughness"]["confidence"], 0.0)
            self.assertEqual(result["metadata"]["evidence_kind"], "material-region")

    def test_gradient_stops_follow_reference_axis_and_flag_hue_risk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "gradient.png"
            image = Image.new("RGB", (120, 20))
            for x in range(image.width):
                ratio = x / max(1, image.width - 1)
                color = (round(255 * (1.0 - ratio)), 12, round(255 * ratio))
                for y in range(image.height):
                    image.putpixel((x, y), color)
            image.save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "gradient-stops", "axis": "u", "stops": 6},
                str(workspace),
                str(temp),
            )
            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as board:
                self.assertEqual(board.size, (256, 96))
            self.assertEqual(report["kind"], "polykit.gradient-stops")
            self.assertEqual(report["axis"], "u")
            self.assertEqual(len(report["stops"]), 6)
            self.assertEqual(report["stops"][0]["hueName"], "red")
            self.assertEqual(report["stops"][-1]["hueName"], "blue")
            self.assertGreaterEqual(len(report["riskFlags"]), 1)
            self.assertEqual(result["metadata"]["evidence_kind"], "gradient-stops")

    def test_pbr_evidence_writes_five_reviewable_map_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "surface.png"
            image = Image.new("RGB", (80, 60))
            for x in range(image.width):
                for y in range(image.height):
                    image.putpixel((x, y), ((x * 3) % 256, (y * 4) % 256, ((x + y) * 2) % 256))
            image.save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "pbr-evidence", "max_dimension": 64},
                str(workspace),
                str(temp),
            )
            output = Path(str(result["filePath"]))
            report_path = next(Path(str(path)) for path in result["sidecars"] if str(path).endswith("pbr-evidence.json"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as sheet:
                self.assertEqual(sheet.size, (64 * 4, 48))
            for key, suffix, mode in (("albedo", "_albedo.png", "RGB"), ("roughness", "_roughness.png", "L"), ("height", "_height.png", "L"), ("normal", "_normal.png", "RGB"), ("ambientOcclusion", "_ao.png", "L")):
                path = next(Path(str(value)) for value in result["sidecars"] if str(value).endswith(suffix))
                with Image.open(path) as map_image:
                    self.assertEqual(map_image.size, (64, 48))
                    self.assertEqual(map_image.mode, mode)
                self.assertIn(key, report["maps"])
            self.assertEqual(report["kind"], "polykit.pbr-evidence")
            self.assertEqual(result["metadata"]["evidence_kind"], "pbr-evidence")

    def test_delight_albedo_writes_a_corrected_image_and_method_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "lit-reference.png"
            image = Image.new("RGB", (64, 32))
            for x in range(64):
                value = 20 + x * 3
                for y in range(32):
                    image.putpixel((x, y), (value, value, value))
            image.save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "delight-albedo", "blur_radius": 8, "strength": 1},
                str(workspace),
                str(temp),
            )

            from PIL import Image as PILImage

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with PILImage.open(output) as corrected:
                self.assertEqual(corrected.format, "PNG")
                self.assertEqual(corrected.size, (64, 32))
                self.assertNotEqual(corrected.getpixel((0, 16)), corrected.getpixel((63, 16)))
            self.assertEqual(report["kind"], "polykit.delighted-albedo")
            self.assertEqual(report["settings"]["method"], "low-frequency-division")
            self.assertEqual(result["metadata"]["strength"], 1.0)

    def test_landmark_guide_outputs_overlay_and_unreviewed_skeleton(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "portrait.png"
            Image.new("RGB", (100, 80), (120, 160, 200)).save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "landmark-guide", "subject_type": "character"},
                str(workspace),
                str(temp),
            )

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with Image.open(output) as image:
                self.assertEqual(image.size, (100, 80))
                self.assertEqual(image.mode, "RGBA")
            self.assertEqual(report["kind"], "polykit.landmark-guide")
            self.assertEqual(report["status"], "needs_visual_review")
            self.assertEqual(len(report["guide"]["landmarks"]), 11)
            self.assertTrue(all(item["x"] is None and item["y"] is None for item in report["guide"]["landmarks"]))
            self.assertEqual(result["metadata"]["landmark_count"], 11)

    def test_material_palette_extracts_dominant_colors_and_board(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "materials.png"
            image = Image.new("RGB", (120, 80), (180, 40, 40))
            for x in range(40, 80):
                for y in range(80):
                    image.putpixel((x, y), (40, 100, 180))
            image.save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "material-palette", "colors": 4, "sample_size": 128},
                str(workspace),
                str(temp),
            )

            from PIL import Image as PILImage

            output = Path(str(result["filePath"]))
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(output.is_file())
            with PILImage.open(output) as board:
                self.assertEqual(board.format, "PNG")
                self.assertEqual(board.width, 384)
                self.assertEqual(board.height, 176)
            self.assertEqual(report["kind"], "polykit.material-palette")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(len(report["palette"]), 2)
            self.assertAlmostEqual(sum(item["share"] for item in report["palette"]), 1.0, places=5)
            self.assertEqual(report["palette"][0]["hex"], "#b42828")
            self.assertEqual(result["metadata"]["color_count"], 2)

    def test_reference_quality_flags_small_low_contrast_input(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "small.png"
            Image.new("RGB", (64, 32), (120, 120, 120)).save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {
                    "_node_id": "reference-quality",
                    "min_width": 128,
                    "min_height": 128,
                    "min_contrast": 10,
                },
                str(workspace),
                str(temp),
            )

            overlay = Path(str(result["filePath"]))
            report_path = Path(str(result["sidecars"][0]))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(overlay.is_file())
            self.assertEqual(report["kind"], "polykit.reference-quality")
            self.assertEqual(report["status"], "insufficient_reference_resolution")
            self.assertEqual({issue["code"] for issue in report["issues"]}, {
                "insufficient_reference_resolution",
                "low_reference_contrast",
                "low_reference_dynamic_range",
            })
            self.assertEqual(result["metadata"]["status"], "insufficient_reference_resolution")

    def test_reference_quality_passes_detailed_reference(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "detailed.png"
            image = Image.new("RGB", (128, 128), (255, 255, 255))
            for x in range(128):
                for y in range(128):
                    image.putpixel((x, y), (x * 2, y * 2, (x + y) % 256))
            image.save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"_node_id": "reference-quality", "min_width": 64, "min_height": 64, "min_contrast": 5},
                str(workspace),
                str(temp),
            )

            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["sourceImage"]["width"], 128)
            self.assertGreater(report["metrics"]["edgeMean"], 0)

    def test_detail_inventory_writes_overlay_report_and_crops(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "chair.png"
            Image.new("RGB", (11, 7), (30, 60, 90)).save(source)
            workspace = root / "process-workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"grid_size": 2, "subject": "Test chair", "target_min_details": 5},
                str(workspace),
                str(temp),
            )

            overlay = Path(str(result["filePath"]))
            sidecars = [Path(str(path)) for path in result["sidecars"]]
            report_path = next(path for path in sidecars if path.name.endswith("detail-inventory.json"))
            self.assertTrue(overlay.is_file())
            self.assertEqual(len(sidecars), 5)  # report plus 2 × 2 region crops
            self.assertTrue(all(path.is_file() for path in sidecars))
            with Image.open(overlay) as image:
                self.assertEqual(image.size, (11, 7))

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["kind"], "polykit.detail-inventory")
            self.assertEqual(report["status"], "needs_visual_review")
            self.assertEqual(report["subject"], "Test chair")
            self.assertEqual(report["sourceImage"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(len(report["scan"]["regions"]), 4)
            self.assertEqual(len(report["detailChecklist"]), 15)
            self.assertTrue(all(item["present"] is None for item in report["detailChecklist"]))
            self.assertEqual(result["metadata"]["region_count"], 4)

    def test_include_crops_false_keeps_report_references_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "reference.png"
            Image.new("RGB", (4, 4), (255, 255, 255)).save(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.py",
                {"filePath": str(source)},
                {"grid_size": 3, "include_crops": False},
                str(workspace),
                str(temp),
            )

            self.assertEqual(len(result["sidecars"]), 1)
            report = json.loads(Path(str(result["sidecars"][0])).read_text(encoding="utf-8"))
            self.assertTrue(all(region["crop"] is None for region in report["scan"]["regions"]))
            self.assertEqual(result["metadata"]["region_count"], 9)

    def test_workflow_publishes_sidecars_with_image_output(self) -> None:
        import asyncio
        import threading
        from types import SimpleNamespace
        from unittest import mock

        from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
        from services.runtime_paths import runtime_paths
        from services.workflow_engine import WorkflowEngine

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_paths = runtime_paths.snapshot()
            runtime_paths.update(workspace_dir=root)
            source = root / "source.png"
            Image.new("RGB", (8, 8), (80, 120, 160)).save(source)
            payload = base64.b64encode(source.read_bytes()).decode("ascii")
            manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
            node_manifest = manifest["nodes"][0]
            process_tuple = (PACK_DIR, manifest, node_manifest)
            request = WorkflowExecutionRequest(
                collection="Evidence",
                prompt={
                    "source": WorkflowExecutionNode(
                        class_type="polykit.image",
                        inputs={"image": {"kind": "base64", "data": payload}},
                    ),
                    "inventory": WorkflowExecutionNode(
                        class_type="reference-evidence/detail-inventory",
                        inputs={"image": ["source", "image"], "params": {"grid_size": 2}},
                    ),
                    "output": WorkflowExecutionNode(
                        class_type="polykit.image_output",
                        inputs={"image": ["inventory", "image"]},
                    ),
                },
                output_node_id="output",
            )
            job = SimpleNamespace(job_id="reference-evidence-workflow", progress=0, step="", meta=None)
            loop = asyncio.new_event_loop()
            try:
                with mock.patch("services.workflow_engine.process_node_pack", return_value=process_tuple), mock.patch(
                    "services.workflow_executor.process_node_pack", return_value=process_tuple
                ):
                    final = loop.run_until_complete(
                        WorkflowEngine(cache_enabled=False).run(
                            job_id=job.job_id,
                            request=request,
                            job=job,
                            persist=lambda: None,
                            cancel_event=threading.Event(),
                            is_cancelled=lambda: False,
                        )
                    )
            finally:
                loop.close()
                runtime_paths.update(
                    models_dir=old_paths.models,
                    workspace_dir=old_paths.workspace,
                    workflows_dir=old_paths.workflows,
                    node_packs_dir=old_paths.node_packs,
                )

            self.assertIsNotNone(final)
            assert final is not None
            self.assertTrue(final.is_relative_to(root / "Evidence"))
            reports = list((root / "Evidence").glob("*_detail-inventory.json"))
            crops = list((root / "Evidence").glob("*_r*.png"))
            self.assertEqual(len(reports), 1)
            self.assertEqual(len(crops), 4)
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(report["scan"]["method"], "grid-2x2")
            self.assertEqual(job.meta["process_metadata"]["inventory"]["evidence_kind"], "detail-inventory")


if __name__ == "__main__":
    unittest.main()
