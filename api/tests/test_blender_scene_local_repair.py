from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = REPO_ROOT / "src" / "areas" / "workflows" / "nodes" / "blender-scene"
PROCESSOR_PATH = PACK_DIR / "processor.py"


class BlenderSceneLocalRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(PACK_DIR))
        spec = importlib.util.spec_from_file_location("polykit_blender_scene_processor", PROCESSOR_PATH)
        assert spec and spec.loader
        cls.processor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.processor)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            sys.path.remove(str(PACK_DIR))
        except ValueError:
            pass

    def test_full_build_exports_stable_part_provenance(self) -> None:
        config = self.processor.base._cabin_config({})
        building = self.processor.base._building_spec(config)
        script = self.processor._patched_scene_script(
            "cabin",
            "test",
            640,
            480,
            False,
            config,
            building,
        )
        self.assertIn("export_extras=True", script)
        self.assertIn("obj['polyKitPartId'] = part_id", script)
        self.assertIn("obj['polyKitProvenanceKind'] = 'build-part'", script)
        self.assertIn("obj.name = part_id", script)
        compile(script, "<blender-full-build>", "exec")

    def test_repair_script_is_bounded_to_explicit_part_ids(self) -> None:
        building = {
            "id": "cabin",
            "anchors": [
                {"id": "floor-left", "partId": "floor", "position": [0.0, 0.0, 0.0]},
                {"id": "wall-bottom", "partId": "left-wall", "position": [0.0, 0.0, 0.0]},
            ],
            "attachments": [
                {
                    "id": "wall-floor",
                    "from": "floor-left",
                    "to": "wall-bottom",
                    "mode": "support",
                    "tolerance": 0.03,
                }
            ],
        }
        script = self.processor._repair_script(
            source_path="/tmp/source.glb",
            scene_name="cabin_repair",
            building_spec=building,
            part_ids=["floor", "left-wall"],
            attachment_ids=["wall-floor"],
            render_preview=False,
        )
        self.assertIn("PART_IDS = set", script)
        self.assertIn("for part_id in sorted(PART_IDS)", script)
        self.assertIn("translation-anchor-snap-v1", script)
        self.assertIn("export_extras=True", script)
        self.assertNotIn("primitive_cube_add", script)
        compile(script, "<blender-part-repair>", "exec")


if __name__ == "__main__":
    unittest.main()
