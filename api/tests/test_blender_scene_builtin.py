import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSOR_PATH = REPO_ROOT / "src" / "areas" / "workflows" / "nodes" / "blender-scene" / "processor_v2.py"


def _load_processor():
    spec = importlib.util.spec_from_file_location("polykit_blender_scene_v2", PROCESSOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {PROCESSOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BlenderSceneBuiltinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.processor = _load_processor()

    def test_cabin_build_spec_uses_shared_contact_anchors(self) -> None:
        config = self.processor._cabin_config({})
        building = self.processor._building_spec(config)
        anchors = {item["id"]: item for item in building["anchors"]}

        self.assertEqual(building["generator"], "blender-parametric")
        self.assertEqual(anchors["left-wall-top"]["position"], anchors["left-roof-bearing"]["position"])
        self.assertEqual(anchors["right-wall-top"]["position"], anchors["right-roof-bearing"]["position"])
        self.assertEqual(anchors["floor-left"]["position"], anchors["left-wall-bottom"]["position"])
        self.assertTrue(all(item["mode"] == "support" for item in building["attachments"]))

    def test_dimensions_are_clamped_before_build_spec_generation(self) -> None:
        config = self.processor._cabin_config({
            "cabin_width": 999,
            "wall_height": 0.1,
            "roof_pitch_deg": 90,
        })
        self.assertEqual(config["width"], 30.0)
        self.assertEqual(config["wall_height"], 2.2)
        self.assertEqual(config["roof_pitch_deg"], 65.0)

    def test_generated_blender_script_is_syntactically_valid_and_geometry_backed(self) -> None:
        config = self.processor._cabin_config({"cabin_width": 9.0, "roof_pitch_deg": 42.0})
        building = self.processor._building_spec(config)
        script = self.processor._scene_script(
            "cabin_test",
            "A compact cabin",
            640,
            480,
            False,
            config,
            building,
        )
        compile(script, "<blender-cabin-v2>", "exec")
        self.assertIn("closest_distance", script)
        self.assertIn("interior_outside_floor", script)
        self.assertIn("polyKitZone", script)
        self.assertIn("ROOF_CENTER_Z", script)
        self.assertIn("construction validation failed", script)
        self.assertIn("polyKitBuildSpec", script)
        self.assertIn("blender_version", script)
        self.assertIn("polyKitBlenderVersion", script)

    def test_generated_blender_script_declares_inspection_views(self) -> None:
        config = self.processor._cabin_config({})
        building = self.processor._building_spec(config)
        script = self.processor._scene_script(
            "cabin_views_test",
            "A compact cabin",
            640,
            360,
            True,
            config,
            building,
        )
        compile(script, "<blender-cabin-views>", "exec")
        self.assertIn("InspectionCameraHearth", script)
        self.assertIn("InspectionCameraExterior", script)
        self.assertIn("InspectionCameraTop", script)
        self.assertIn("InspectionCameraSide", script)
        self.assertIn("render_evidence['validation']", script)
        self.assertIn("visualValidation", script)
        self.assertIn("preserved_with_toon_variant", script)
        self.assertIn("restore_authored_materials_for_glb", script)
        self.assertIn("original_indices = [int(index) for index in indices]", script)
        self.assertIn("configure_toon_profile", script)
        self.assertIn("_view_' + view_name + '.png", script)
        self.assertIn("preview_views_b64", script)

    def test_subway_reference_prompt_and_build_spec_are_integrated(self) -> None:
        prompt = self.processor.SUBWAY_REFERENCE_PROMPT
        config = self.processor._subway_config({"station_length": 200, "station_width": 4})
        building = self.processor._subway_building_spec(config)
        script = self.processor._scene_script("subway_reference_test", prompt, 640, 400, False, config, building)

        self.assertEqual(config["length"], 120.0)
        self.assertEqual(config["width"], 12.0)
        self.assertAlmostEqual(config["tactile_width"], 4.6 * 0.16)
        self.assertAlmostEqual(config["tactile_inset"], 4.6 * 0.04)
        self.assertEqual(config["tactile_width_ratio"], 0.16)
        self.assertEqual(config["tactile_inset_ratio"], 0.04)
        self.assertEqual(building["parameters"]["preset"], "subway_station")
        self.assertEqual(building["parameters"]["tactileWidthRatio"], 0.16)
        self.assertEqual(building["parameters"]["tactileInsetRatio"], 0.04)
        self.assertEqual(building["generator"], "blender-parametric")
        self.assertEqual(prompt in script, True)
        self.assertIn("Foreground_Occluding_Column", script)
        self.assertIn("Near_Rail_1", script)
        self.assertIn("Left_Tactile_Strip", script)
        self.assertIn("Right_Tactile_Strip", script)
        self.assertIn("Right_Platform", script)
        self.assertIn("Tactile_Dot_Row", script)
        self.assertIn("TACTILE_INSET", script)
        self.assertIn("TACTILE_H = 0.025", script)
        self.assertIn("flush with the platform slabs", script)
        self.assertIn("open platform edges remain unobstructed", script)
        self.assertIn("Left_Platform", script)
        self.assertIn("LEFT_PLATFORM_WIDTH", script)
        self.assertIn("leftPlatformContinuity", script)
        self.assertNotIn("CORRIDOR_X0", script)
        self.assertNotIn("Left_Corridor", script)
        self.assertNotIn("Platform_Railing", script)
        self.assertIn("Tile Grid and Grout", script)
        self.assertIn("Ceiling_Light_Recess_Array", script)
        self.assertIn("Ceiling_Light_Recess_Cutter", script)
        self.assertIn("Recessed_Light_Glass", script)
        self.assertIn("LED_Bead_Source", script)
        self.assertIn("InspectionCameraCeilingFixture", script)
        self.assertNotIn("InspectionCameraLeftCorridor", script)
        self.assertIn("light-glass-panel", script)
        self.assertIn("ceiling-recess-cutter", script)
        self.assertIn("bpy.ops.object.modifier_apply", script)
        self.assertIn("bpy.data.objects.remove(cutter, do_unlink=True)", script)
        self.assertIn("recessed_fixture_assembly", script)
        self.assertIn("Transmission Weight", script)
        self.assertIn("lightingValidation", script)
        self.assertIn("surfaceValidation", script)
        self.assertIn("subway_station_v1", script)
        compile(script, "<blender-subway-reference>", "exec")

    def test_processor_refuses_to_write_without_server_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="polykit-blender-no-workspace-") as temp_dir:
            completed = subprocess.run(
                [sys.executable, str(PROCESSOR_PATH)],
                cwd=temp_dir,
                input=json.dumps({"params": {"preset": "subway_station"}}) + "\n",
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("workspaceDir is required", completed.stdout)
            self.assertFalse((Path(temp_dir) / "Workflows").exists())

    @unittest.skipUnless(shutil.which("blender"), "Blender executable is not available")
    def test_subway_reference_script_builds_a_real_glb_in_blender(self) -> None:
        config = self.processor._subway_config({"station_length": 32, "column_spacing": 8})
        building = self.processor._subway_building_spec(config)
        script = self.processor._scene_script(
            "subway_real_build_test",
            self.processor.SUBWAY_REFERENCE_PROMPT,
            480,
            270,
            True,
            config,
            building,
        )
        with tempfile.TemporaryDirectory(prefix="polykit-subway-blender-") as temp_dir:
            root = Path(temp_dir)
            script_path = root / "scene.py"
            result_path = root / "result.json"
            script_path.write_text(
                script
                + "\nimport os\n"
                + "pathlib.Path(os.environ['POLYKIT_SUBWAY_TEST_RESULT']).write_text(json.dumps({"
                + "'preset': result['preset'], 'object_count': result['object_count'], "
                + "'glb_present': bool(result.get('glb_b64')), 'validation': result['construction_validation'], "
                + "'entry_dimensions': result['render_evidence']['passes'][0]['metrics'], "
                + "'lighting': result['render_evidence']['lightingValidation'], "
                + "'surface': result['render_evidence']['surfaceValidation'], "
                + "'spatial': result['render_evidence']['validation']['spatialConnectivity'], "
                + "'preview_view_ids': [item['id'] for item in result['render_evidence']['passes']], "
                + "'railing_object_count': sum(1 for obj in bpy.context.scene.objects if 'Railing' in obj.name), "
                + "'glb_cutter_object_count': sum(1 for obj in bpy.context.scene.objects if 'Cutter' in obj.name), "
                + "'export_validation': result['render_evidence']['exportValidation'], "
                + "'fixture_assembly': result['render_evidence']['lightingValidation']['fixtureAssembly']"
                + "}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["POLYKIT_SUBWAY_TEST_RESULT"] = str(result_path)
            completed = subprocess.run(
                [shutil.which("blender") or "blender", "--background", "--python", str(script_path)],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr[-4000:])
            self.assertTrue(result_path.is_file(), completed.stdout[-4000:])
            result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["preset"], "subway_station_v1")
        self.assertGreaterEqual(result["object_count"], 20)
        self.assertEqual(result["railing_object_count"], 0)
        self.assertEqual(result["glb_cutter_object_count"], 0)
        self.assertTrue(result["export_validation"]["booleanRecessesApplied"])
        self.assertTrue(result["export_validation"]["constructionCuttersRemoved"])
        self.assertEqual(result["fixture_assembly"]["booleanRecesses"], 2)
        self.assertEqual(result["fixture_assembly"]["glassPanels"], 2)
        self.assertEqual(result["fixture_assembly"]["ledSources"], 2)
        self.assertEqual(result["spatial"]["status"], "pass")
        self.assertTrue(result["spatial"]["leftPlatform"]["leftPlatformWallContact"])
        self.assertTrue(result["spatial"]["leftPlatform"]["leftPlatformTrackContact"])
        self.assertTrue(result["spatial"]["leftPlatform"]["leftPlatformContinuousLength"])
        self.assertEqual(result["spatial"]["leftPlatform"]["leftPlatforms"], 1)
        self.assertNotIn("left-corridor", result["preview_view_ids"])
        self.assertTrue(result["glb_present"])
        self.assertEqual(result["validation"]["status"], "pass")
        self.assertEqual(result["entry_dimensions"]["width"] / result["entry_dimensions"]["height"], 16 / 9)
        self.assertEqual(result["lighting"]["status"], "pass")
        self.assertEqual(result["surface"]["status"], "pass")
        self.assertTrue(result["surface"]["securityBandFlush"])
        self.assertTrue(all(thickness <= 0.035 for thickness in result["surface"]["securityBandThicknessM"]))


if __name__ == "__main__":
    unittest.main()
