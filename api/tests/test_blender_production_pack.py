from __future__ import annotations

import asyncio
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
