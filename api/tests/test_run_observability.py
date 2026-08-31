import tempfile
import unittest
from pathlib import Path

from schemas.generation import JobStatus
from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.run_observability import (
    finalize_workflow_run,
    init_workflow_observability,
    inspect_workflow_run,
    mark_workflow_run_started,
    observe_workflow_checkpoint,
)
from services.run_store import RunStore


def _request() -> WorkflowExecutionRequest:
    return WorkflowExecutionRequest(
        workflow_id="observable-workflow",
        prompt={
            "text": WorkflowExecutionNode(class_type="polykit.text", inputs={"text": "hello"}),
            "build": WorkflowExecutionNode(
                class_type="fake/build",
                inputs={"text": ["text", "text"]},
            ),
            "output": WorkflowExecutionNode(
                class_type="polykit.output",
                inputs={"mesh": ["build", "mesh"]},
            ),
        },
        output_node_id="output",
        collection="Workflows",
    )


class RunObservabilityTests(unittest.TestCase):
    def test_records_node_timeline_artifact_and_evidence(self) -> None:
        request = _request()
        order = ["text", "build", "output"]
        job = JobStatus(
            job_id="run-observe",
            status="pending",
            progress=0,
            meta={"workflow_id": request.workflow_id, "collection": "Workflows"},
        )
        init_workflow_observability(job, request, request.prompt, order)
        mark_workflow_run_started(job)

        job.status = "running"
        job.step = "Text input (1/3)"
        observe_workflow_checkpoint(job)
        self.assertEqual(job.meta["observability"]["nodes"]["text"]["status"], "done")

        job.progress = 30
        job.step = "Running Fake Build (2/3)"
        observe_workflow_checkpoint(job)
        self.assertEqual(job.meta["observability"]["current_node_id"], "build")
        self.assertEqual(job.meta["observability"]["nodes"]["build"]["status"], "running")

        job.progress = 45
        job.step = "Generating geometry"
        observe_workflow_checkpoint(job)
        self.assertEqual(job.meta["observability"]["nodes"]["build"]["phase"], "Generating geometry")

        job.progress = 70
        job.step = "Publishing output (3/3)"
        observe_workflow_checkpoint(job)
        self.assertEqual(job.meta["observability"]["nodes"]["build"]["status"], "done")
        self.assertEqual(job.meta["observability"]["nodes"]["output"]["status"], "running")

        job.status = "done"
        job.progress = 100
        job.step = "Workflow complete"
        job.output_url = "/workspace/Workflows/cabin.glb"
        job.meta["artifact_kind"] = "mesh"
        finalize_workflow_run(job, status="done", output_url=job.output_url)

        inspection = inspect_workflow_run(job)
        self.assertEqual(inspection["status"], "done")
        self.assertEqual(inspection["nodes"]["output"]["status"], "done")
        self.assertEqual(inspection["artifacts"][0]["workspace_path"], "Workflows/cabin.glb")
        self.assertEqual(inspection["evidence"][0]["kind"], "workflow-output")
        event_types = [event["type"] for event in inspection["events"]]
        self.assertIn("run.queued", event_types)
        self.assertIn("run.started", event_types)
        self.assertIn("node.started", event_types)
        self.assertIn("node.phase", event_types)
        self.assertIn("node.completed", event_types)
        self.assertEqual(event_types[-1], "run.completed")
        self.assertEqual(
            [event["seq"] for event in inspection["events"]],
            list(range(1, len(inspection["events"]) + 1)),
        )

    def test_failure_marks_current_node_and_preserves_pending_nodes(self) -> None:
        request = _request()
        job = JobStatus(job_id="run-fail", status="running", progress=30, meta={})
        init_workflow_observability(job, request, request.prompt, ["text", "build", "output"])
        job.step = "Running Fake Build (2/3)"
        observe_workflow_checkpoint(job)
        job.status = "error"
        job.error = "build failed"
        finalize_workflow_run(job, status="error", error=job.error)

        inspection = inspect_workflow_run(job)
        self.assertEqual(inspection["nodes"]["build"]["status"], "failed")
        self.assertEqual(inspection["nodes"]["output"]["status"], "pending")
        self.assertEqual(inspection["events"][-1]["type"], "run.failed")

    def test_observability_round_trips_through_existing_run_store(self) -> None:
        request = _request()
        job = JobStatus(job_id="run-persist", status="pending", progress=0, meta={})
        init_workflow_observability(job, request, request.prompt, ["text", "build", "output"])

        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp) / "runs.sqlite3")
            store.save(job)
            loaded = store.load()["run-persist"]

        inspection = inspect_workflow_run(loaded)
        self.assertEqual(inspection["workflow_id"], "observable-workflow")
        self.assertEqual(inspection["order"], ["text", "build", "output"])
        self.assertEqual(inspection["events"][0]["type"], "run.queued")


if __name__ == "__main__":
    unittest.main()
