"""WorkflowEngine tests for caching, link validation, list mapping, and mesh inputs."""

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.model_runtime_registry import model_runtime_registry
from services.runtime_paths import runtime_paths
from services.workflow_engine import ArtifactNodeOutputCache, WorkflowEngine
from services.workflow_executor import (
    NodeOutputCache,
    WorkflowError,
    _is_deterministic,
    _run_model_node,
    validate_prompt_links,
)


def _node(class_type: str, inputs: dict | None = None) -> WorkflowExecutionNode:
    return WorkflowExecutionNode(class_type=class_type, inputs=inputs or {})


def _image_payload(data: str = "eA==") -> dict:
    return {"kind": "base64", "data": data}


class NodeOutputCacheTests(unittest.TestCase):
    def test_signature_is_stable_and_sensitive_to_inputs(self) -> None:
        cache = NodeOutputCache()
        inputs_a = {"image": ["img", "image"], "params": {"seed": 0, "steps": 25}}
        inputs_b = {"image": ["img", "image"], "params": {"seed": 0, "steps": 26}}
        refs = {"img": "SIG-IMG"}

        s1 = cache.signature("m/gen", inputs_a, refs)
        s2 = cache.signature("m/gen", inputs_a, refs)
        s3 = cache.signature("m/gen", inputs_b, refs)
        s4 = cache.signature("m/gen", inputs_a, {"img": "SIG-OTHER"})

        self.assertEqual(s1, s2)
        self.assertNotEqual(s1, s3)
        self.assertNotEqual(s1, s4)

    def test_get_drops_stale_mesh_path(self) -> None:
        cache = NodeOutputCache()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "m.glb"
            path.write_bytes(b"x")
            cache.set("sig", {"mesh": path})
            self.assertEqual(cache.get("sig")["mesh"], path)
            path.unlink()
            self.assertIsNone(cache.get("sig"))

    def test_is_deterministic_rejects_random_or_missing_seed(self) -> None:
        self.assertTrue(_is_deterministic({"seed": 0}))
        self.assertFalse(_is_deterministic({"seed": -1}))
        self.assertFalse(_is_deterministic({}))


class ValidatePromptLinksTests(unittest.TestCase):
    def test_wrong_link_type_rejected(self) -> None:
        request = WorkflowExecutionRequest(
            prompt={
                "img": _node("polykit.image", {"image": _image_payload()}),
                "gen": _node("trellis2/generate", {"image": ["img", "image"]}),
                "bad": _node("polykit.output", {"mesh": ["gen", "image"]}),
            }
        )

        with mock.patch("services.workflow_executor.get_node_definition") as get_definition:
            def fake_definition(class_type):
                if class_type in {"polykit.image", "trellis2/generate"}:
                    return SimpleNamespace(outputs=["image"])
                return SimpleNamespace(outputs=["mesh"])

            get_definition.side_effect = fake_definition
            with self.assertRaises(Exception):
                validate_prompt_links(request)

    def test_valid_links_pass(self) -> None:
        request = WorkflowExecutionRequest(
            prompt={
                "img": _node("polykit.image", {"image": _image_payload()}),
                "gen": _node("trellis2/generate", {"image": ["img", "image"]}),
                "out": _node("polykit.output", {"mesh": ["gen", "mesh"]}),
            }
        )

        with mock.patch("services.workflow_executor.get_node_definition") as get_definition:
            def fake_definition(class_type):
                if class_type == "polykit.image":
                    return SimpleNamespace(outputs=["image"])
                return SimpleNamespace(outputs=["mesh"])

            get_definition.side_effect = fake_definition
            validate_prompt_links(request)

    def test_batch_links_validate_each_nested_reference(self) -> None:
        request = WorkflowExecutionRequest(
            prompt={
                "mesh": _node("polykit.mesh", {"mesh": _image_payload()}),
                "image": _node("polykit.image", {"image": _image_payload()}),
                "compose": _node("scene-composer/compose", {"mesh": [["mesh", "mesh"], ["image", "image"]]}),
            }
        )

        with mock.patch("services.workflow_executor.get_node_definition") as get_definition:
            def fake_definition(class_type):
                if class_type == "polykit.image":
                    return SimpleNamespace(outputs=["image"])
                if class_type == "polykit.mesh":
                    return SimpleNamespace(outputs=["mesh"])
                return SimpleNamespace(outputs=["mesh"])

            get_definition.side_effect = fake_definition
            with self.assertRaises(WorkflowError):
                validate_prompt_links(request)


class _FakeGen:
    def __init__(self, outputs_dir: Path) -> None:
        self.outputs_dir = outputs_dir
        self.calls = 0

    def generate(self, image_bytes, params, phase_cb=None, cancel_event=None):
        self.calls += 1
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        output = self.outputs_dir / f"mesh-{self.calls}.glb"
        output.write_bytes(b"glb")
        return output


class ModelRuntimeMixin:
    def start_generator_patches(self, fake: _FakeGen) -> None:
        self._patchers = [
            mock.patch.object(model_runtime_registry, "get_generator", return_value=fake),
            mock.patch.object(model_runtime_registry, "get_manifest", return_value={"name": "Gen", "version": "1"}),
            mock.patch.object(model_runtime_registry, "switch_model", return_value=None),
            mock.patch.object(model_runtime_registry, "get_active", return_value=fake),
            mock.patch.object(
                model_runtime_registry,
                "active_status",
                return_value={"id": "trellis2/generate", "loaded": True, "downloaded": True, "name": "Gen"},
            ),
        ]
        for patcher in self._patchers:
            patcher.start()

    def stop_generator_patches(self) -> None:
        for patcher in reversed(self._patchers):
            patcher.stop()


class WorkflowEngineTests(ModelRuntimeMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.original_paths = runtime_paths.snapshot()
        runtime_paths.update(workspace_dir=self.root)
        self.fake = _FakeGen(self.root)
        self.start_generator_patches(self.fake)

    def tearDown(self) -> None:
        self.stop_generator_patches()
        runtime_paths.update(
            models_dir=self.original_paths.models,
            workspace_dir=self.original_paths.workspace,
            workflows_dir=self.original_paths.workflows,
            node_packs_dir=self.original_paths.node_packs,
        )
        self._tmp.cleanup()

    def _request(self, seed: int) -> WorkflowExecutionRequest:
        return WorkflowExecutionRequest(
            collection="Workflows",
            prompt={
                "img": _node("polykit.image", {"image": _image_payload()}),
                "gen": _node(
                    "trellis2/generate",
                    {"image": ["img", "image"], "params": {"seed": seed}},
                ),
                "out": _node("polykit.output", {"mesh": ["gen", "mesh"]}),
            },
        )

    async def _run(self, request, engine=None, job_id: str = "job-1") -> Path | None:
        engine = engine or WorkflowEngine(node_cache=ArtifactNodeOutputCache())
        job = SimpleNamespace(progress=0, step="")
        return await engine.run(
            job_id=job_id,
            request=request,
            job=job,
            persist=lambda: None,
            cancel_event=threading.Event(),
            is_cancelled=lambda: False,
        )

    def test_cache_hit_skips_second_run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            request = self._request(seed=0)
            engine = WorkflowEngine(node_cache=ArtifactNodeOutputCache())
            mesh1 = loop.run_until_complete(self._run(request, engine, "job-1"))
            mesh2 = loop.run_until_complete(self._run(request, engine, "job-2"))
            self.assertEqual(self.fake.calls, 1)
            self.assertIsNotNone(mesh1)
            self.assertIsNotNone(mesh2)
            self.assertTrue(mesh1.exists())  # type: ignore[union-attr]
            self.assertTrue(mesh2.exists())  # type: ignore[union-attr]
            self.assertEqual(mesh1.read_bytes(), mesh2.read_bytes())  # type: ignore[union-attr]
        finally:
            loop.close()

    def test_param_change_invalidates_cache(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            engine = WorkflowEngine(node_cache=ArtifactNodeOutputCache())
            loop.run_until_complete(self._run(self._request(seed=0), engine, "job-1"))
            loop.run_until_complete(self._run(self._request(seed=0), engine, "job-2"))
            self.assertEqual(self.fake.calls, 1)
            loop.run_until_complete(self._run(self._request(seed=1), engine, "job-3"))
            self.assertEqual(self.fake.calls, 2)
        finally:
            loop.close()

    def test_random_seed_disables_caching(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            engine = WorkflowEngine(node_cache=ArtifactNodeOutputCache())
            loop.run_until_complete(self._run(self._request(seed=-1), engine, "job-1"))
            loop.run_until_complete(self._run(self._request(seed=-1), engine, "job-2"))
            self.assertEqual(self.fake.calls, 2)
        finally:
            loop.close()

    def test_process_sidecars_are_published_with_primary_mesh(self) -> None:
        """Auxiliary Blender artifacts survive cleanup of the run directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            artifact_dir = root / ".artifacts" / "sidecar-job" / "process-workspace"
            artifact_dir.mkdir(parents=True)
            mesh = artifact_dir / "cabin.glb"
            blend = artifact_dir / "cabin.blend"
            preview = artifact_dir / "cabin.png"
            entry_view = artifact_dir / "cabin_view_entry.png"
            hearth_view = artifact_dir / "cabin_view_hearth.png"
            exterior_view = artifact_dir / "cabin_view_exterior.png"
            mesh.write_bytes(b"glb")
            blend.write_bytes(b"blend")
            preview.write_bytes(b"png")
            entry_view.write_bytes(b"entry")
            hearth_view.write_bytes(b"hearth")
            exterior_view.write_bytes(b"exterior")
            request = WorkflowExecutionRequest(
                collection="Workflows",
                prompt={
                    "brief": _node("polykit.text", {"text": "cabin"}),
                    "build": _node("blender-scene/build", {"text": ["brief", "text"]}),
                    "out": _node("polykit.output", {"mesh": ["build", "mesh"]}),
                },
            )
            process_tuple = (Path(temp_dir), {"entry": "processor.py"}, {"id": "build", "output": "mesh"})
            job = SimpleNamespace(progress=0, step="")
            loop = asyncio.new_event_loop()
            try:
                with mock.patch("services.workflow_engine.process_node_pack", return_value=process_tuple), mock.patch(
                    "services.workflow_engine._run_process_node",
                    return_value={"mesh": mesh, "sidecars": [blend, preview, entry_view, hearth_view, exterior_view]},
                ):
                    result = loop.run_until_complete(
                        WorkflowEngine(node_cache=ArtifactNodeOutputCache(), cache_enabled=False).run(
                            job_id="sidecar-job",
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
            self.assertTrue((root / "Workflows" / "cabin.glb").is_file())
            self.assertTrue((root / "Workflows" / "cabin.blend").is_file())
            self.assertTrue((root / "Workflows" / "cabin.png").is_file())
            self.assertTrue((root / "Workflows" / "cabin_view_entry.png").is_file())
            self.assertTrue((root / "Workflows" / "cabin_view_hearth.png").is_file())
            self.assertTrue((root / "Workflows" / "cabin_view_exterior.png").is_file())


class MapOverListTests(ModelRuntimeMixin, unittest.TestCase):
    def test_model_node_runs_per_list_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake = _FakeGen(root)
            self.start_generator_patches(fake)
            try:
                loop = asyncio.new_event_loop()
                try:
                    node = _node("trellis2/generate", {"image": ["src", "image"]})

                    def resolve(value):
                        return [b"img1", b"img2"] if value == ["src", "image"] else value

                    output = loop.run_until_complete(
                        _run_model_node(loop, node, resolve, root, None, lambda *args, **kwargs: None)
                    )
                    self.assertEqual(fake.calls, 2)
                    self.assertEqual(len(output["mesh"]), 2)
                finally:
                    loop.close()
            finally:
                self.stop_generator_patches()


class MeshSourceNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_paths = runtime_paths.snapshot()

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original_paths.models,
            workspace_dir=self.original_paths.workspace,
            workflows_dir=self.original_paths.workflows,
            node_packs_dir=self.original_paths.node_packs,
        )

    def test_mesh_workspace_path_resolves_to_server_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            collection = root / "Workflows"
            collection.mkdir(parents=True)
            mesh = collection / "input.glb"
            mesh.write_bytes(b"glb-data")

            request = WorkflowExecutionRequest(
                collection="Workflows",
                prompt={
                    "m": _node(
                        "polykit.mesh",
                        {"mesh": {"kind": "workspace_path", "path": "Workflows/input.glb"}},
                    ),
                    "out": _node("polykit.output", {"mesh": ["m", "mesh"]}),
                },
            )
            engine = WorkflowEngine(node_cache=ArtifactNodeOutputCache())
            job = SimpleNamespace(progress=0, step="")
            loop = asyncio.new_event_loop()
            try:
                final = loop.run_until_complete(
                    engine.run(
                        job_id="mesh-job",
                        request=request,
                        job=job,
                        persist=lambda: None,
                        cancel_event=threading.Event(),
                        is_cancelled=lambda: False,
                    )
                )
            finally:
                loop.close()
            self.assertEqual(final, mesh)

    def test_mesh_missing_file_fails_cleanly(self) -> None:
        from services.workflow_executor import _decode_mesh_payload

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            with self.assertRaises(WorkflowError):
                _decode_mesh_payload(
                    {"kind": "workspace_path", "path": "Workflows/nope.glb"},
                    root / "tmp",
                )


if __name__ == "__main__":
    unittest.main()
