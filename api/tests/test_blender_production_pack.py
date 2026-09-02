from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from schemas.workflow import WorkflowExecutionNode
from services.workflow_executor import _run_process_node


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = REPO_ROOT / "src" / "areas" / "workflows" / "nodes" / "blender-production"


def _load_processor():
    spec = importlib.util.spec_from_file_location("blender_production_processor", PACK_DIR / "processor.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BlenderProductionManifestTests(unittest.TestCase):
    def test_manifest_exposes_functional_operations(self) -> None:
        manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["type"], "process")
        self.assertEqual(manifest["entry"], "processor.py")
        nodes = {node["id"]: node for node in manifest["nodes"]}
        self.assertEqual(
            set(nodes),
            {
                "opening",
                "array-stairs",
                "curve-profile",
                "geometry-nodes",
                "assembly",
                "surface",
                "lighting",
                "deform",
                "simulation-setup",
                "npr",
                "geometry-report",
            },
        )
        self.assertEqual(nodes["geometry-report"]["output"], "mesh")
        self.assertEqual(nodes["surface"]["input"], "mesh")

    def test_script_generation_is_bounded_to_declared_operations(self) -> None:
        processor = _load_processor()
        for operation in ("opening", "array-stairs", "curve-profile", "geometry-nodes", "assembly", "surface", "lighting", "deform", "simulation-setup", "npr"):
            script = processor._scene_script(operation, {}, "/tmp/input.glb", "", "test_scene", False)
            compile(script, f"{operation}.py", "exec")
            self.assertIn(f"OPERATION = '{operation}'", script)
            self.assertIn("export_scene.gltf", script)
            self.assertIn("blenderVersion", script)
            if operation == "array-stairs":
                self.assertIn("index * rise + rail_height / 2.0", script)
                self.assertNotIn("index * rise / 2.0 + rail_height / 2.0", script)
                self.assertIn("tread_thickness / 2.0", script)
            if operation == "surface":
                self.assertIn("zlib.decompress", script)
                self.assertIn("ShaderNodeBsdfPrincipled", script)
            if operation == "npr":
                self.assertIn("ShaderNodeRaycast", script)
                self.assertIn("GeometryNodeExtrudeMesh", script)
                self.assertIn("GeometryNodeSwitch", script)
                self.assertIn("transform.vector_type = 'VECTOR'", script)
                self.assertIn("Outline Mask", script)
                self.assertIn("ShaderNodeBsdfToon", script)
                self.assertIn("polyKitNprToonColor", script)
                self.assertIn("line_mode must be 'silhouette', 'structure', or 'hybrid'", script)
                self.assertIn("preserved_with_toon_variant", script)
                self.assertIn("polyKitPresentationObjectCount", script)
                self.assertIn("metadata['renderEvidence']", script)
                self.assertIn("generic camera helper silently switch that run back to Eevee", script)
                self.assertIn("original_indices = [int(index) for index in indices]", script)
                render_index = script.index("bpy.ops.render.render(write_still=True)")
                restore_index = script.index("if OPERATION == 'npr' and not replace_material:")
                self.assertLess(render_index, restore_index)

        report = processor._report_script("/tmp/input.glb", "", {})
        compile(report, "geometry-report.py", "exec")
        self.assertIn("nonManifoldEdges", report)


class BlenderProductionDispatchTests(unittest.TestCase):
    def test_executor_passes_manifest_node_id_to_shared_processor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "result.glb"
            output.write_bytes(b"glb")
            process_tuple = (
                PACK_DIR,
                {"entry": "processor.py"},
                {"id": "opening", "output": "mesh"},
            )
            with patch("services.workflow_executor.process_node_pack", return_value=process_tuple), patch(
                "services.workflow_executor.run_processor",
                return_value={"filePath": str(output)},
            ) as run:
                async def execute():
                    return await _run_process_node(
                        asyncio.get_running_loop(),
                        WorkflowExecutionNode(class_type="blender-production/opening", inputs={"params": {}}),
                        lambda value: value,
                        Path(temp_dir),
                        Path(temp_dir),
                        None,
                        lambda *_args: None,
                    )

                result = asyncio.run(execute())
            self.assertEqual(result["mesh"], output)
            self.assertEqual(run.call_args.args[3]["_node_id"], "opening")

    def test_geometry_report_uses_dedicated_report_script(self) -> None:
        processor = _load_processor()
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.glb"
            input_path.write_bytes(b"glb")
            payload = {
                "workspaceDir": temp_dir,
                "input": {"filePath": str(input_path)},
                "params": {"_node_id": "geometry-report"},
            }
            response = {
                "glb_b64": base64.b64encode(b"glb-result").decode("ascii"),
                "report_b64": base64.b64encode(b"{}").decode("ascii"),
                "report_json": "{}",
            }
            events: list[dict] = []
            with patch.object(processor.sys, "stdin", io.StringIO(json.dumps(payload) + "\n")), patch.object(
                processor, "_report_script", return_value="report-script"
            ) as report_script, patch.object(processor, "_scene_script") as scene_script, patch.object(
                processor, "_send_blender_code", return_value=response
            ) as send_code, patch.object(processor, "emit", side_effect=events.append):
                processor.main()

            report_script.assert_called_once()
            scene_script.assert_not_called()
            self.assertEqual(send_code.call_args.args[2], "report-script")
            done = next(event for event in events if event.get("type") == "done")
            result = done["result"]
            self.assertTrue(Path(result["filePath"]).is_file())
            self.assertEqual(Path(result["sidecars"][0]).read_text(encoding="utf-8"), "{}")
            self.assertEqual(result["metadata"]["operation"], "geometry-report")


if __name__ == "__main__":
    unittest.main()
