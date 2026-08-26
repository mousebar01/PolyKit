import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import services.image_generation as image_generation
import services.model_runtime as model_runtime
from schemas.generation import JobStatus
from services.image_generation import texture_refiner_id
from services.run_coordinator import run_coordinator
from services.runtime_paths import runtime_paths


class _FakeGenerator:
    def __init__(self, name: str, root: Path) -> None:
        self.name = name
        self.root = root
        self.outputs_dir = root
        self.calls: list[dict] = []

    def is_loaded(self) -> bool:
        return True

    def generate(self, image_bytes, params, progress_cb=None, cancel_event=None):
        self.calls.append(dict(params))
        suffix = "textured.glb" if self.name.endswith("refine") else "geometry.glb"
        path = self.outputs_dir / suffix
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"glb")
        if progress_cb:
            progress_cb(100, "Done")
        return path


class _FakeRegistry:
    def __init__(self, root: Path) -> None:
        self.active_id = "trellis2/generate"
        self.generators = {
            "trellis2/generate": _FakeGenerator("trellis2/generate", root),
            "trellis2/refine": _FakeGenerator("trellis2/refine", root),
        }
        self.switches: list[str] = []
        self.ended: list[str] = []

    def get_manifest(self, model_id: str) -> dict:
        if model_id == "trellis2/refine":
            return {"inputs": ["image", "mesh"], "output": "mesh"}
        if model_id == "trellis2/generate":
            return {"inputs": ["image"], "output": "mesh"}
        raise KeyError(model_id)

    def switch_model(self, model_id: str, *, allow_during_generation: bool = False) -> None:
        self.switches.append(model_id)
        self.active_id = model_id

    def active_status(self) -> dict:
        return {
            "id": self.active_id,
            "name": self.active_id,
            "downloaded": True,
            "loaded": True,
        }

    def get_active(self):
        return self.generators[self.active_id]

    def end_generation(self, job_id: str) -> None:
        self.ended.append(job_id)


class TexturePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_paths = runtime_paths.snapshot()

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original_paths.models,
            workspace_dir=self.original_paths.workspace,
            workflows_dir=self.original_paths.workflows,
            node_packs_dir=self.original_paths.node_packs,
        )

    def test_finds_image_mesh_refiner_sibling(self) -> None:
        manifest = {"inputs": ["image", "mesh"], "output": "mesh"}
        with patch.object(image_generation.model_runtime_registry, "get_manifest", return_value=manifest):
            self.assertEqual(texture_refiner_id("trellis2/generate"), "trellis2/refine")

    def test_rejects_non_generate_or_missing_refiner(self) -> None:
        self.assertIsNone(texture_refiner_id("trellis2/refine"))
        with patch.object(image_generation.model_runtime_registry, "get_manifest", side_effect=KeyError("missing")):
            self.assertIsNone(texture_refiner_id("unknown/generate"))

    def test_rejects_refiner_without_both_inputs(self) -> None:
        manifest = {"inputs": ["image"], "output": "mesh"}
        with patch.object(image_generation.model_runtime_registry, "get_manifest", return_value=manifest):
            self.assertIsNone(texture_refiner_id("trellis2/generate"))

    def test_texture_request_runs_generate_then_refine_and_returns_final_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            fake_registry = _FakeRegistry(root)
            job_id = "texture-test"
            run_coordinator.jobs[job_id] = JobStatus(
                job_id=job_id,
                status="pending",
                meta={"image_name": "my_robot.png"},
            )
            run_coordinator.cancel_events[job_id] = threading.Event()
            run_coordinator.cancelled.discard(job_id)
            try:
                with patch.object(image_generation, "model_runtime_registry", fake_registry), \
                     patch.object(model_runtime, "model_runtime_registry", fake_registry), \
                     patch.object(run_coordinator, "persist"):
                    asyncio.run(
                        image_generation.run_generation(
                            job_id,
                            b"image",
                            {"enable_texture": True},
                            "Default",
                            "trellis2/generate",
                        )
                    )

                job = run_coordinator.jobs[job_id]
                self.assertEqual(job.status, "done")
                self.assertEqual(job.progress, 100)
                self.assertRegex(
                    job.output_url,
                    r"^/workspace/Default/my_robot_\d{8}-\d{6}_[0-9a-f]{8}_textured\.glb$",
                )
                self.assertEqual(
                    fake_registry.switches,
                    ["trellis2/generate", "trellis2/generate", "trellis2/refine", "trellis2/refine"],
                )
                self.assertFalse((root / "Default" / "geometry.glb").exists())
                intermediate = root / ".artifacts" / job_id / "models" / "geometry.glb"
                textured = list((root / "Default").glob("my_robot_*_textured.glb"))
                self.assertEqual(len(textured), 1)
                self.assertTrue(textured[0].is_file())
                self.assertEqual(
                    fake_registry.generators["trellis2/refine"].calls[0]["mesh_path"],
                    str(intermediate),
                )
            finally:
                run_coordinator.jobs.pop(job_id, None)
                run_coordinator.cancel_events.pop(job_id, None)
                run_coordinator.completed_at.pop(job_id, None)
                run_coordinator.cancelled.discard(job_id)
                run_coordinator.clear_active(job_id)


if __name__ == "__main__":
    unittest.main()
