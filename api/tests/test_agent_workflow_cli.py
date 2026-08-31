import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from services.runtime_paths import runtime_paths
from tools.agent_workflow import main


class AgentWorkflowCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-agent-workflow-cli-")
        runtime_paths.update(workspace_dir=Path(self._tmp.name))

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def _call(self, argv: list[str]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
        out = json.loads(stdout.getvalue()) if stdout.getvalue().strip() else None
        err = json.loads(stderr.getvalue()) if stderr.getvalue().strip() else None
        return code, out, err

    def test_cli_uses_the_same_durable_runtime_and_read_only_next(self) -> None:
        code, started, error = self._call([
            "start",
            "world-builder",
            "world",
            "scene-cli",
            "--metadata-json",
            '{"chat_session_id":"chat-cli"}',
        ])
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        session_id = started["id"]

        _, before, _ = self._call(["get", session_id])
        _, action, _ = self._call(["next", session_id])
        _, after, _ = self._call(["get", session_id])
        self.assertEqual(before, after)
        self.assertEqual(action["step"]["id"], "intent")
        self.assertEqual(action["action"], "execute")

        self.assertEqual(self._call(["begin", session_id])[0], 0)
        code, completed, error = self._call([
            "complete",
            session_id,
            "continue",
            "--evidence",
            "world-intent=world://scene-cli/intent",
        ])
        self.assertEqual(code, 0)
        self.assertIsNone(error)
        self.assertEqual(completed["current_step"], "spec")

    def test_cli_reports_missing_required_evidence_as_json_error(self) -> None:
        _, started, _ = self._call(["start", "world-builder", "world", "scene-cli-error"])
        session_id = started["id"]
        self._call(["begin", session_id])
        code, output, error = self._call(["complete", session_id, "continue"])
        self.assertEqual(code, 2)
        self.assertIsNone(output)
        self.assertEqual(error["error"], "AgentWorkflowStateError")
        self.assertIn("world-intent", error["message"])


if __name__ == "__main__":
    unittest.main()
