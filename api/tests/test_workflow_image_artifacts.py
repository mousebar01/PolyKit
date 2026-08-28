import asyncio
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.model_runtime_registry import model_runtime_registry
from services.runtime_paths import runtime_paths
from services.workflow_engine import WorkflowEngine
from services.workflow_executor import WorkflowError, validate_prompt_links


class _FakeImageGenerator:
    def __init__(self, root: Path) -> None:
        self.outputs_dir = root
        self.calls = 0

    def generate(self, primary_input, params, progress_cb=None, cancel_event=None):
        assert primary_input is None
        self.calls += 1
        output = self.outputs_dir / f"image-{self.calls}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"png")
        return output


class ImageWorkflowTests(unittest.TestCase):
    def test_text_to_image_output_is_published(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_paths = runtime_paths.snapshot()
            runtime_paths.update(workspace_dir=root)
            generator = _FakeImageGenerator(root)
            patches = [
                mock.patch.object(model_runtime_registry, "get_generator", return_value=generator),
                mock.patch.object(
                    model_runtime_registry,
                    "get_manifest",
                    return_value={"name": "Anima", "output": "image", "version": "1"},
                ),
                mock.patch.object(model_runtime_registry, "switch_model", return_value=None),
                mock.patch.object(model_runtime_registry, "get_active", return_value=generator),
                mock.patch.object(
                    model_runtime_registry,
                    "active_status",
                    return_value={"id": "anima/generate", "loaded": True, "downloaded": True, "name": "Anima"},
                ),
            ]
            for patcher in patches:
                patcher.start()
            try:
                request = WorkflowExecutionRequest(
                    collection="Illustrations",
                    prompt={
                        "prompt": WorkflowExecutionNode(
                            class_type="polykit.text",
                            inputs={"text": "1girl, blue hair, cel shading"},
                        ),
                        "anima": WorkflowExecutionNode(
                            class_type="anima/generate",
                            inputs={"text": ["prompt", "text"], "params": {"seed": 7}},
                        ),
                        "output": WorkflowExecutionNode(
                            class_type="polykit.image_output",
                            inputs={"image": ["anima", "image"]},
                        ),
                    },
                    output_node_id="output",
                )
                job = SimpleNamespace(progress=0, step="", meta=None)
                loop = asyncio.new_event_loop()
                try:
                    final = loop.run_until_complete(
                        WorkflowEngine(cache_enabled=False).run(
                            job_id="image-job",
                            request=request,
                            job=job,
                            persist=lambda: None,
                            cancel_event=threading.Event(),
                            is_cancelled=lambda: False,
                        )
                    )
                finally:
                    loop.close()
                self.assertIsNotNone(final)
                self.assertEqual(job.meta["artifact_kind"], "image")
                self.assertEqual(generator.calls, 1)
                self.assertTrue(final.is_relative_to(root / "Illustrations"))
                self.assertEqual(final.read_bytes(), b"png")
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
                runtime_paths.update(
                    models_dir=old_paths.models,
                    workspace_dir=old_paths.workspace,
                    workflows_dir=old_paths.workflows,
                    node_packs_dir=old_paths.node_packs,
                )

    def test_image_output_sink_rejects_mesh_link(self) -> None:
        request = WorkflowExecutionRequest(
            prompt={
                "mesh": WorkflowExecutionNode(class_type="polykit.mesh", inputs={}),
                "output": WorkflowExecutionNode(
                    class_type="polykit.image_output",
                    inputs={"image": ["mesh", "mesh"]},
                ),
            }
        )
        with mock.patch("services.workflow_executor.get_node_definition") as get_definition:
            get_definition.side_effect = lambda class_type: SimpleNamespace(
                outputs=["mesh"] if class_type == "polykit.mesh" else ["image"]
            )
            with self.assertRaises(WorkflowError):
                validate_prompt_links(request)

    def test_image_preview_sink_publishes_a_workspace_image(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old_paths = runtime_paths.snapshot()
            runtime_paths.update(workspace_dir=root)
            source = root / "source.png"
            source.write_bytes(b"png")
            patches = [
                mock.patch("services.workflow_engine._run_process_node", return_value={"image": source}),
                mock.patch("services.workflow_engine.process_node_pack", return_value=(root, {"entry": "processor.py"}, {"id": "remove-background", "output": "image"})),
            ]
            for patcher in patches:
                patcher.start()
            try:
                request = WorkflowExecutionRequest(
                    collection="Illustrations",
                    prompt={
                        "remove": WorkflowExecutionNode(
                            class_type="image-background-remover/remove-background",
                            inputs={"image": {"kind": "base64", "data": "eA=="}, "params": {}},
                        ),
                        "preview": WorkflowExecutionNode(
                            class_type="polykit.preview",
                            inputs={"image": ["remove", "image"]},
                        ),
                    },
                )
                job = SimpleNamespace(progress=0, step="", meta=None)
                loop = asyncio.new_event_loop()
                try:
                    final = loop.run_until_complete(
                        WorkflowEngine(cache_enabled=False).run(
                            job_id="image-preview-job",
                            request=request,
                            job=job,
                            persist=lambda: None,
                            cancel_event=threading.Event(),
                            is_cancelled=lambda: False,
                        )
                    )
                finally:
                    loop.close()
                self.assertIsNotNone(final)
                self.assertEqual(job.meta["artifact_kind"], "image")
                self.assertTrue(final.is_relative_to(root / "Illustrations"))
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
                runtime_paths.update(
                    models_dir=old_paths.models,
                    workspace_dir=old_paths.workspace,
                    workflows_dir=old_paths.workflows,
                    node_packs_dir=old_paths.node_packs,
                )


if __name__ == "__main__":
    unittest.main()
