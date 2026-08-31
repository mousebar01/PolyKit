import importlib.util
from pathlib import Path
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


class BlenderCabinV2Tests(unittest.TestCase):
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
        self.assertIn("ROOF_CENTER_Z", script)
        self.assertIn("construction validation failed", script)
        self.assertIn("polyKitBuildSpec", script)

    def test_generated_blender_script_declares_inspection_views(self) -> None:
        config = self.processor._cabin_config({})
        building = self.processor._building_spec(config)
        script = self.processor._scene_script(
            "cabin_views_test",
            "A compact cabin",
            640,
            480,
            True,
            config,
            building,
        )
        compile(script, "<blender-cabin-views>", "exec")
        self.assertIn("InspectionCameraHearth", script)
        self.assertIn("InspectionCameraExterior", script)
        self.assertIn("_view_' + view_name + '.png", script)
        self.assertIn("preview_views_b64", script)


if __name__ == "__main__":
    unittest.main()
