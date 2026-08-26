import asyncio
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.model_runtime_registry import model_runtime_registry
from services.mesh_artifacts import (
    COORDINATE_SPACE_CANONICAL,
    COORDINATE_SPACE_UNKNOWN,
    MeshArtifact,
    publish_mesh_value,
)
from services.runtime_paths import runtime_paths
from services.workflow_engine import WorkflowEngine
import services.workflow_engine as workflow_engine_module


def _node(class_type: str, inputs: dict) -> WorkflowExecutionNode:
    return WorkflowExecutionNode(class_type=class_type, inputs=inputs)


def _job():
    return SimpleNamespace(progress=0, step="")


class MeshArtifactTests(unittest.TestCase):
    def test_publish_copies_intermediate_and_preserves_metadata(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / ".artifacts" / "run" / "mesh.glb"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"mesh")
            artifact = MeshArtifact(
                path=source,
                coordinate_space=COORDINATE_SPACE_CANONICAL,
                persistent=False,
                origin="model",
            )

            published = publish_mesh_value(artifact, root / "Workflows")

            self.assertIsInstance(published, MeshArtifact)
            self.assertTrue(published.persistent)
            self.assertEqual(published.coordinate_space, COORDINATE_SPACE_CANONICAL)
            self.assertEqual(published.path.read_bytes(), b"mesh")
            self.assertTrue(source.exists())
            self.assertEqual(published.path.parent, root / "Workflows")


class WorkflowEngineArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_paths = runtime_paths.snapshot()

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original_paths.models,
            workspace_dir=self.original_paths.workspace,
            workflows_dir=self.original_paths.workflows,
            node_packs_dir=self.original_paths.node_packs,
        )

    def _run(self, request: WorkflowExecutionRequest, fake_model):
        with TemporaryDirectory() as td:
            workspace = Path(td)
            runtime_paths.update(workspace_dir=workspace)
            job = _job()
            with (
                patch.object(workflow_engine_module, "process_node_pack", return_value=None),
                patch.object(workflow_engine_module, "run_model_node", new=fake_model),
                patch.object(model_runtime_registry, "get_manifest", return_value={"name": "Fake Model"}),
            ):
                result = asyncio.run(
                    WorkflowEngine(cache_enabled=False).run(
                        job_id="job-1",
                        request=request,
                        job=job,
                        persist=lambda: None,
                        cancel_event=None,
                        is_cancelled=lambda: False,
                    )
                )
                result_bytes = result.read_bytes() if result and result.exists() else None
                result_path = result
                artifact_exists = (workspace / ".artifacts" / "job-1").exists()
                published = (
                    list((workspace / "Workflows").glob("*.glb"))
                    if (workspace / "Workflows").exists()
                    else []
                )
                return (
                    workspace,
                    result_path,
                    result_bytes,
                    artifact_exists,
                    [p.name for p in published],
                )

    def test_output_node_is_the_publication_boundary(self) -> None:
        async def fake_model(loop, node, resolve, coll_dir, cancel_event, phase_cb):
            self.assertIn(".artifacts", coll_dir.parts)
            self.assertNotIn("Workflows", coll_dir.parts)
            coll_dir.mkdir(parents=True, exist_ok=True)
            path = coll_dir / "generated.glb"
            path.write_bytes(b"generated")
            return {"mesh": path}

        request = WorkflowExecutionRequest(
            collection="Workflows",
            prompt={
                "image": _node(
                    "polykit.image",
                    {"image": {"kind": "base64", "data": "eA=="}},
                ),
                "generate": _node("fake/generate", {"image": ["image", "image"]}),
                "output": _node("polykit.output", {"mesh": ["generate", "mesh"]}),
            },
            output_node_id="output",
        )

        _, result, data, artifact_exists, published = self._run(request, fake_model)
        self.assertIsNotNone(result)
        self.assertEqual(data, b"generated")
        self.assertEqual(result.parent.name, "Workflows")
        self.assertEqual(published, ["generated.glb"])
        self.assertFalse(artifact_exists)

    def test_downstream_model_receives_path_while_dag_carries_artifact(self) -> None:
        seen_mesh_inputs: list[Path] = []

        async def fake_model(loop, node, resolve, coll_dir, cancel_event, phase_cb):
            if "mesh" in node.inputs:
                mesh_input = resolve(node.inputs["mesh"])
                self.assertIsInstance(mesh_input, Path)
                self.assertTrue(mesh_input.exists())
                seen_mesh_inputs.append(mesh_input)
            coll_dir.mkdir(parents=True, exist_ok=True)
            path = coll_dir / ("textured.glb" if "mesh" in node.inputs else "geometry.glb")
            path.write_bytes(b"textured" if "mesh" in node.inputs else b"geometry")
            return {"mesh": path}

        request = WorkflowExecutionRequest(
            collection="Workflows",
            prompt={
                "image": _node(
                    "polykit.image",
                    {"image": {"kind": "base64", "data": "eA=="}},
                ),
                "generate": _node("fake/generate", {"image": ["image", "image"]}),
                "texture": _node(
                    "fake/refine",
                    {"image": ["image", "image"], "mesh": ["generate", "mesh"]},
                ),
                "output": _node("polykit.output", {"mesh": ["texture", "mesh"]}),
            },
            output_node_id="output",
        )

        _, result, data, artifact_exists, published = self._run(request, fake_model)
        self.assertEqual(len(seen_mesh_inputs), 1)
        self.assertIn(".artifacts", seen_mesh_inputs[0].parts)
        self.assertEqual(data, b"textured")
        self.assertEqual(result.name, "textured.glb")
        self.assertEqual(published, ["textured.glb"])
        self.assertFalse(artifact_exists)

    def test_mesh_primary_model_receives_path_and_preserves_input_space(self) -> None:
        seen_mesh: list[Path] = []

        async def fake_model(loop, node, resolve, coll_dir, cancel_event, phase_cb):
            mesh_input = resolve(node.inputs["mesh"])
            self.assertIsInstance(mesh_input, Path)
            seen_mesh.append(mesh_input)
            coll_dir.mkdir(parents=True, exist_ok=True)
            output = coll_dir / "parts.glb"
            output.write_bytes(b"parts")
            return {"mesh": output}

        with TemporaryDirectory() as td:
            workspace = Path(td)
            runtime_paths.update(workspace_dir=workspace)
            source = workspace / "Workflows" / "source.glb"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            job = _job()
            captured_spaces: list[str] = []
            real_wrap = workflow_engine_module.wrap_mesh_value

            def capture_wrap(value, **kwargs):
                if kwargs.get("origin") == "model":
                    captured_spaces.append(kwargs.get("coordinate_space"))
                return real_wrap(value, **kwargs)

            request = WorkflowExecutionRequest(
                collection="Workflows",
                prompt={
                    "mesh": _node(
                        "polykit.mesh",
                        {"mesh": {"kind": "workspace_path", "path": "Workflows/source.glb"}},
                    ),
                    "segment": _node("fake/segment", {"mesh": ["mesh", "mesh"], "params": {}}),
                    "output": _node("polykit.output", {"mesh": ["segment", "mesh"]}),
                },
                output_node_id="output",
            )

            with (
                patch.object(workflow_engine_module, "process_node_pack", return_value=None),
                patch.object(workflow_engine_module, "run_model_node", new=fake_model),
                patch.object(workflow_engine_module, "wrap_mesh_value", side_effect=capture_wrap),
                patch.object(model_runtime_registry, "get_manifest", return_value={"name": "Segment"}),
            ):
                result = asyncio.run(
                    WorkflowEngine(cache_enabled=False).run(
                        job_id="job-mesh-primary",
                        request=request,
                        job=job,
                        persist=lambda: None,
                        cancel_event=None,
                        is_cancelled=lambda: False,
                    )
                )

            self.assertEqual(seen_mesh, [source])
            self.assertEqual(captured_spaces, [COORDINATE_SPACE_UNKNOWN])
            self.assertEqual(result.read_bytes(), b"parts")

    def test_preview_keeps_intermediate_artifact_without_publishing(self) -> None:
        async def fake_model(loop, node, resolve, coll_dir, cancel_event, phase_cb):
            coll_dir.mkdir(parents=True, exist_ok=True)
            path = coll_dir / "preview.glb"
            path.write_bytes(b"preview")
            return {"mesh": path}

        request = WorkflowExecutionRequest(
            collection="Workflows",
            prompt={
                "image": _node(
                    "polykit.image",
                    {"image": {"kind": "base64", "data": "eA=="}},
                ),
                "generate": _node("fake/generate", {"image": ["image", "image"]}),
                "preview": _node("polykit.preview", {"mesh": ["generate", "mesh"]}),
            },
            output_node_id="preview",
        )

        _, result, data, artifact_exists, published = self._run(request, fake_model)
        self.assertEqual(data, b"preview")
        self.assertIn(".artifacts", result.parts)
        self.assertEqual(published, [])
        self.assertTrue(artifact_exists)


if __name__ == "__main__":
    unittest.main()
