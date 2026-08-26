import asyncio
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from schemas.workflow import WorkflowExecutionNode
from services.model_node_executor import run_model_node
import services.model_node_executor as model_executor
from services.model_pack_subprocess import ModelPackSubprocess
from services.workflow_executor import WorkflowError


class _FakeGenerator:
    def __init__(self, output: object) -> None:
        self.output = output
        self.outputs_dir = None
        self.calls = []

    def generate(self, primary_input, params, progress_cb, cancel_event=None):
        self.calls.append((primary_input, dict(params), cancel_event))
        return self.output


async def _execute(node, root: Path):
    return await run_model_node(
        asyncio.get_running_loop(),
        node,
        lambda value: value,
        root,
        None,
        lambda pct, step="": None,
    )


class MeshPrimaryModelExecutorTests(unittest.TestCase):
    def _patch_generator(self, fake):
        def execute_model(model_id, primary_input, params, output_dir, progress_cb=None, cancel_event=None):
            previous = fake.outputs_dir
            fake.outputs_dir = output_dir
            try:
                return fake.generate(primary_input, params, progress_cb, cancel_event)
            finally:
                fake.outputs_dir = previous

        return (
            patch.object(model_executor.model_runtime_registry, "get_generator", return_value=fake),
            patch.object(
                model_executor.model_runtime_registry,
                "get_manifest",
                return_value={"name": "Hunyuan3D-Part"},
            ),
            patch.object(model_executor.model_runtime_registry, "switch_model"),
            patch.object(model_executor, "_load_model", new=AsyncMock(return_value=fake)),
            patch.object(model_executor, "execute_model", side_effect=execute_model),
        )

    def test_mesh_only_model_uses_path_primary_input_without_enabling_texture(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.glb"
            output = root / "segmented.glb"
            source.write_bytes(b"source")
            output.write_bytes(b"parts")
            fake = _FakeGenerator(str(output))
            node = WorkflowExecutionNode(
                class_type="hunyuan3d-part/decompose-mesh",
                inputs={"mesh": source, "params": {"max_parts": 24}},
            )

            patchers = self._patch_generator(fake)
            with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
                result = asyncio.run(_execute(node, root))

            result_path = result["mesh"]
            self.assertIsInstance(result_path, Path)
            self.assertTrue(result_path.is_file())
            self.assertEqual(result_path.read_bytes(), b"parts")
            self.assertEqual(len(fake.calls), 1)
            primary, params, _ = fake.calls[0]
            self.assertEqual(primary, source)
            self.assertEqual(params["mesh_path"], str(source))
            self.assertEqual(params["max_parts"], 24)
            self.assertFalse(params["enable_texture"])

    def test_mesh_only_model_accepts_mapping_mesh_result(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.glb"
            output = root / "result.glb"
            source.write_bytes(b"source")
            output.write_bytes(b"result")
            fake = _FakeGenerator({"primary_mesh": str(output)})
            node = WorkflowExecutionNode(
                class_type="provider/segment",
                inputs={"mesh": source, "params": {}},
            )

            patchers = self._patch_generator(fake)
            with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
                result = asyncio.run(_execute(node, root))
            self.assertEqual(result["mesh"].read_bytes(), b"result")

    def test_relative_result_is_resolved_inside_model_output_directory(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.glb"
            output = root / "relative.glb"
            source.write_bytes(b"source")
            output.write_bytes(b"relative")
            fake = _FakeGenerator("relative.glb")
            node = WorkflowExecutionNode(
                class_type="provider/segment",
                inputs={"mesh": source, "params": {}},
            )

            patchers = self._patch_generator(fake)
            with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
                result = asyncio.run(_execute(node, root))
            self.assertEqual(result["mesh"].read_bytes(), b"relative")

    def test_missing_result_fails_before_publication(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.glb"
            source.write_bytes(b"source")
            fake = _FakeGenerator("does-not-exist.glb")
            node = WorkflowExecutionNode(
                class_type="provider/segment",
                inputs={"mesh": source, "params": {}},
            )

            patchers = self._patch_generator(fake)
            with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
                with self.assertRaisesRegex(WorkflowError, "returned a missing mesh file"):
                    asyncio.run(_execute(node, root))


class ModelPackSubprocessPrimaryInputTests(unittest.TestCase):
    def test_path_primary_input_uses_generic_wire_envelope(self) -> None:
        proc = ModelPackSubprocess(Path("/tmp/example-pack"), {"id": "example"})
        sent = []
        proc._send = lambda message: sent.append(message)  # type: ignore[method-assign]
        proc._queue = Queue()
        proc._queue.put({"type": "done", "output_path": "/tmp/result.glb"})

        result = proc.generate(Path("/tmp/input.glb"), {"max_parts": 16})

        self.assertEqual(result, Path("/tmp/result.glb"))
        self.assertEqual(len(sent), 1)
        self.assertNotIn("image_b64", sent[0])
        self.assertEqual(
            sent[0]["primary_input"],
            {"kind": "path", "path": "/tmp/input.glb"},
        )
        self.assertEqual(sent[0]["params"], {"max_parts": 16})

    def test_image_bytes_keep_legacy_image_b64_protocol(self) -> None:
        proc = ModelPackSubprocess(Path("/tmp/example-pack"), {"id": "example"})
        sent = []
        proc._send = lambda message: sent.append(message)  # type: ignore[method-assign]
        proc._queue = Queue()
        proc._queue.put({"type": "done", "output_path": "/tmp/result.glb"})

        proc.generate(b"image", {})

        self.assertIn("image_b64", sent[0])
        self.assertNotIn("primary_input", sent[0])


if __name__ == "__main__":
    unittest.main()
