import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from schemas.generation import JobStatus
from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.run_store import RunStore
from services.runtime_paths import runtime_paths
from services.workflow_engine import ArtifactNodeOutputCache, WorkflowEngine, WorkflowWait
from services.workflow_execution import (
    current_waiting,
    execution_state,
    load_workflow_execution_request,
    submit_signal,
)


def _node(class_type: str, inputs: dict | None = None) -> WorkflowExecutionNode:
    return WorkflowExecutionNode(class_type=class_type, inputs=inputs or {})


class WorkflowInterruptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-workflow-interrupt-")
        self.root = Path(self._tmp.name)
        self.original_paths = runtime_paths.snapshot()
        runtime_paths.update(workspace_dir=self.root)
        (self.root / "Workflows").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original_paths.models,
            workspace_dir=self.original_paths.workspace,
            workflows_dir=self.original_paths.workflows,
            node_packs_dir=self.original_paths.node_packs,
        )
        self._tmp.cleanup()

    def _request(self, input_path: Path) -> WorkflowExecutionRequest:
        return WorkflowExecutionRequest(
            workflow_id="approval-test",
            collection="Workflows",
            output_node_id="out",
            prompt={
                "mesh": _node(
                    "polykit.mesh",
                    {"mesh": {"kind": "workspace_path", "path": input_path.relative_to(self.root).as_posix()}},
                ),
                "gate": _node(
                    "polykit.interrupt",
                    {
                        "after": ["mesh", "mesh"],
                        "params": {
                            "signal_name": "approve-geometry",
                            "prompt": "Approve the generated geometry before publication.",
                        },
                    },
                ),
                # The approval reference is intentionally an extra dependency.
                # The sink still consumes the original typed mesh, but cannot
                # become runnable until the interrupt node resolves.
                "out": _node(
                    "polykit.output",
                    {
                        "mesh": ["mesh", "mesh"],
                        "approval": ["gate", "signal"],
                    },
                ),
            },
        )

    async def _run(self, job: JobStatus, request: WorkflowExecutionRequest):
        engine = WorkflowEngine(node_cache=ArtifactNodeOutputCache(), cache_enabled=False)
        return await engine.run(
            job_id=job.job_id,
            request=request,
            job=job,
            persist=lambda: None,
            cancel_event=threading.Event(),
            is_cancelled=lambda: False,
        )

    def test_interrupt_releases_then_resumes_from_durable_checkpoint(self) -> None:
        source = self.root / "Workflows" / "source.glb"
        source.write_bytes(b"durable-mesh")
        request = self._request(source)
        job = JobStatus(job_id="run-1", status="running", meta={})

        loop = asyncio.new_event_loop()
        try:
            first = loop.run_until_complete(self._run(job, request))
            self.assertIsInstance(first, WorkflowWait)
            self.assertEqual(job.status, "waiting")
            self.assertEqual(current_waiting(job)["signal_name"], "approve-geometry")
            state = execution_state(job)
            self.assertEqual(state["steps"]["mesh"]["status"], "done")
            self.assertEqual(state["steps"]["mesh"]["attempt"], 1)
            self.assertEqual(state["steps"]["gate"]["status"], "waiting")

            # Simulate a process restart: only JSON-persisted JobStatus metadata
            # and run-owned checkpoint files survive. The original input is
            # deliberately removed so a fake replay from request inputs fails.
            persisted_meta = json.loads(json.dumps(job.meta))
            source.unlink()
            resumed = JobStatus(
                job_id="run-1",
                status="waiting",
                progress=job.progress,
                step=job.step,
                meta=persisted_meta,
            )
            loaded_request = load_workflow_execution_request(resumed, workspace_root=self.root)
            submit_signal(
                resumed,
                name="approve-geometry",
                payload={"decision": "pass", "judge": "test"},
            )

            second = loop.run_until_complete(self._run(resumed, loaded_request))
            self.assertIsInstance(second, Path)
            self.assertTrue(second.is_file())
            self.assertEqual(second.read_bytes(), b"durable-mesh")
            resumed_state = execution_state(resumed)
            self.assertEqual(resumed_state["steps"]["mesh"]["attempt"], 1)
            self.assertEqual(resumed_state["steps"]["gate"]["attempt"], 1)
            self.assertEqual(resumed_state["steps"]["out"]["attempt"], 1)
            self.assertIsNone(current_waiting(resumed))
            self.assertIsNotNone(resumed_state["steps"]["mesh"]["checkpoint"])
            signal = resumed_state["signals"][0]
            self.assertIsNotNone(signal["consumed_at"])
        finally:
            loop.close()

    def test_wrong_signal_name_is_rejected(self) -> None:
        source = self.root / "Workflows" / "source.glb"
        source.write_bytes(b"mesh")
        request = self._request(source)
        job = JobStatus(job_id="run-2", status="running", meta={})
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(job, request))
        finally:
            loop.close()
        with self.assertRaisesRegex(ValueError, "waiting for signal"):
            submit_signal(job, name="different-signal", payload={"decision": "pass"})


class RunStoreRestartTests(unittest.TestCase):
    def test_running_becomes_recoverable_interrupted_without_completion_timestamp(self) -> None:
        with tempfile.TemporaryDirectory(prefix="polykit-run-store-restart-") as temp_dir:
            db = Path(temp_dir) / "runs.sqlite3"
            store = RunStore(db)
            store.save(JobStatus(job_id="run-a", status="running", meta={"execution": {"version": 1}}))

            restarted = RunStore(db)
            job = restarted.load()["run-a"]
            self.assertEqual(job.status, "interrupted")
            self.assertNotIn("run-a", restarted.completed_times())

    def test_waiting_survives_service_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="polykit-run-store-waiting-") as temp_dir:
            db = Path(temp_dir) / "runs.sqlite3"
            store = RunStore(db)
            store.save(JobStatus(job_id="run-b", status="waiting", meta={"execution": {"version": 1}}))

            restarted = RunStore(db)
            self.assertEqual(restarted.load()["run-b"].status, "waiting")


if __name__ == "__main__":
    unittest.main()
