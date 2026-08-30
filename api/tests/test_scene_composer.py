"""Integration coverage for the multi-mesh scene composition process node."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import trimesh

from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.runtime_paths import runtime_paths
from services.workflow_engine import ArtifactNodeOutputCache, WorkflowEngine


class SceneComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_paths = runtime_paths.snapshot()
        self.source_pack = Path(__file__).parents[2] / "src" / "areas" / "workflows" / "nodes" / "scene-composer"
        self.manifest = json.loads((self.source_pack / "manifest.json").read_text())
        self.node_manifest = next(item for item in self.manifest["nodes"] if item["id"] == "compose")

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original_paths.models,
            workspace_dir=self.original_paths.workspace,
            workflows_dir=self.original_paths.workflows,
            node_packs_dir=self.original_paths.node_packs,
        )

    def test_source_processor_merges_meshes_and_applies_placements(self) -> None:
        from services.process_runner import run_processor

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "lamp.glb"
            second = root / "table.glb"
            trimesh.creation.box(extents=(1, 1, 1)).export(first)
            trimesh.creation.box(extents=(2, 1, 1)).export(second)
            output = run_processor(
                self.source_pack,
                "processor.py",
                {"filePaths": [str(first), str(second)]},
                {
                    "output_name": "cabin",
                    "placements": json.dumps({
                        "lamp.glb": {"position": [2, 0, 0], "size": [2, 2, 2]},
                        "table.glb": {"position": [0, 0, 0], "size": [2, 1, 1]},
                    }),
                },
                str(root / "workspace"),
                str(root / "tmp"),
            )
            composed = Path(str(output["filePath"]))
            self.assertTrue(composed.is_file())
            scene = trimesh.load(composed, force="scene")
            self.assertEqual(len(scene.geometry), 2)
            self.assertEqual(scene.metadata["polyKit"]["sourceCount"], 2)
            lamp_nodes = [name for name in scene.graph.nodes_geometry if name.startswith("lamp/")]
            self.assertEqual(len(lamp_nodes), 1)
            transform, _ = scene.graph[lamp_nodes[0]]
            self.assertAlmostEqual(float(transform[0, 3]), 2.0, places=5)
            self.assertAlmostEqual(float(scene.bounds[0][1]), 0.0, places=5)

    def test_workflow_engine_fans_in_mesh_references_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            inputs = root / "Inputs"
            inputs.mkdir()
            first = inputs / "lamp.glb"
            second = inputs / "table.glb"
            trimesh.creation.box(extents=(1, 1, 1)).export(first)
            trimesh.creation.box(extents=(1, 1, 1)).export(second)

            request = WorkflowExecutionRequest(
                collection="Scenes",
                prompt={
                    "lamp": WorkflowExecutionNode(
                        class_type="polykit.mesh",
                        inputs={"mesh": {"kind": "workspace_path", "path": "Inputs/lamp.glb"}},
                    ),
                    "table": WorkflowExecutionNode(
                        class_type="polykit.mesh",
                        inputs={"mesh": {"kind": "workspace_path", "path": "Inputs/table.glb"}},
                    ),
                    "compose": WorkflowExecutionNode(
                        class_type="scene-composer/compose",
                        inputs={
                            "mesh": [["lamp", "mesh"], ["table", "mesh"]],
                            "params": {"output_name": "cabin"},
                        },
                    ),
                    "out": WorkflowExecutionNode(
                        class_type="polykit.output",
                        inputs={"mesh": ["compose", "mesh"]},
                    ),
                },
            )
            process_tuple = (self.source_pack, self.manifest, self.node_manifest)
            job = SimpleNamespace(progress=0, step="")
            loop = asyncio.new_event_loop()
            try:
                with mock.patch("services.workflow_engine.process_node_pack", return_value=process_tuple), mock.patch(
                    "services.workflow_executor.process_node_pack", return_value=process_tuple
                ):
                    result = loop.run_until_complete(
                        WorkflowEngine(node_cache=ArtifactNodeOutputCache(), cache_enabled=False).run(
                            job_id="compose-job",
                            request=request,
                            job=job,
                            persist=lambda: None,
                            cancel_event=threading.Event(),
                            is_cancelled=lambda: False,
                        )
                    )
            finally:
                loop.close()

            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result.is_file())
            scene = trimesh.load(result, force="scene")
            self.assertEqual(len(scene.geometry), 2)


if __name__ == "__main__":
    unittest.main()
