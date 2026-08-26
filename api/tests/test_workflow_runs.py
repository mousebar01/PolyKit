import unittest
from pathlib import Path
from unittest.mock import patch

from routers.workflow_runs import _require_known_class_type, list_runs
from schemas.generation import JobStatus
from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.run_coordinator import run_coordinator
from services.workflow_executor import WorkflowError, topological_order
from services.workspace_paths import normalize_collection, resolve_workspace_path


class CollectionTests(unittest.TestCase):
    def test_preserves_safe_collection(self) -> None:
        self.assertEqual(normalize_collection(" HeadlessSmoke "), "HeadlessSmoke")

    def test_replaces_empty_or_path_like_collection(self) -> None:
        for value in ("", "../escape", "nested/path", r"nested\\path"):
            with self.subTest(value=value):
                self.assertEqual(normalize_collection(value), "Default")

    def test_rejects_dot_collections(self) -> None:
        self.assertEqual(normalize_collection("."), "Default")
        self.assertEqual(normalize_collection(".."), "Default")


class WorkspacePathTests(unittest.TestCase):
    def test_resolves_relative_path_inside_workspace(self) -> None:
        root = Path("/tmp/polykit-workspace").resolve()
        self.assertEqual(resolve_workspace_path(root, "Agent/model.glb"), root / "Agent/model.glb")

    def test_rejects_traversal_and_absolute_paths(self) -> None:
        root = Path("/tmp/polykit-workspace").resolve()
        for value in ("../secret.glb", "/tmp/secret.glb"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    resolve_workspace_path(root, value)


def _texture_pair_request() -> WorkflowExecutionRequest:
    return WorkflowExecutionRequest(
        workflow_id="wf-texture",
        prompt={
            "image": {
                "class_type": "polykit.image",
                "inputs": {"image": {"kind": "base64", "data": "aW1hZ2U="}},
            },
            "generate": {
                "class_type": "trellis2/generate",
                "inputs": {
                    "image": ["image", "image"],
                    "params": {"remesh": "none"},
                },
            },
            "texture": {
                "class_type": "trellis2/refine",
                "inputs": {
                    "image": ["image", "image"],
                    "mesh": ["generate", "mesh"],
                    "params": {"texture_resolution": 768},
                },
            },
            "output": {
                "class_type": "polykit.output",
                "inputs": {"mesh": ["texture", "mesh"]},
            },
        },
        output_node_id="output",
    )


class ServerWorkflowValidationTests(unittest.TestCase):
    def test_texture_pair_is_a_valid_acyclic_dag(self) -> None:
        request = _texture_pair_request()
        order = topological_order(request.prompt)
        self.assertEqual(set(order), set(request.prompt))
        self.assertEqual(order[0], "image")
        self.assertLess(order.index("generate"), order.index("texture"))
        self.assertEqual(order[-1], "output")

    def test_rejects_cyclic_graph(self) -> None:
        request = WorkflowExecutionRequest(
            prompt={
                "a": WorkflowExecutionNode(class_type="polykit.text", inputs={"text": "x"}),
                "b": WorkflowExecutionNode(class_type="polykit.text", inputs={"text": ["a", "text"]}),
                "a-again": WorkflowExecutionNode(class_type="polykit.text", inputs={"text": ["b", "text"]}),
            }
        )
        request.prompt["a"].inputs["text"] = ["b", "text"]
        with self.assertRaises(WorkflowError):
            topological_order(request.prompt)

    def test_rejects_missing_reference_target(self) -> None:
        request = WorkflowExecutionRequest(
            prompt={
                "a": WorkflowExecutionNode(class_type="polykit.text", inputs={"text": "x"}),
                "b": WorkflowExecutionNode(class_type="polykit.text", inputs={"text": ["missing", "text"]}),
            }
        )
        with self.assertRaises(WorkflowError):
            topological_order(request.prompt)

    def test_requires_known_class_types(self) -> None:
        with patch("routers.workflow_runs.is_known", return_value=False):
            with self.assertRaises(ValueError):
                _require_known_class_type("nobody/nowhere")
        with patch("routers.workflow_runs.is_known", return_value=True):
            _require_known_class_type("polykit.image")
            _require_known_class_type("polykit.output")


class ServerWorkflowListTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_filter_excludes_legacy_runs_without_matching_metadata(self) -> None:
        jobs = {
            "matching": JobStatus(
                job_id="matching",
                status="done",
                progress=100,
                meta={"workflow_id": "wf-a"},
            ),
            "legacy": JobStatus(
                job_id="legacy",
                status="done",
                progress=100,
                meta=None,
            ),
            "other": JobStatus(
                job_id="other",
                status="done",
                progress=100,
                meta={"workflow_id": "wf-b"},
            ),
        }
        with patch.dict(run_coordinator.jobs, jobs, clear=True):
            result = await list_runs(workflow_id="wf-a")

        self.assertEqual([run.run_id for run in result], ["matching"])


if __name__ == "__main__":
    unittest.main()
