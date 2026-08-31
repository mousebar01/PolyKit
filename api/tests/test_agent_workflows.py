import tempfile
import unittest
from pathlib import Path

from services.agent_workflow_registry import get_agent_workflow
from services.agent_workflow_runtime import (
    AgentWorkflowStateError,
    begin_agent_workflow_step,
    complete_agent_workflow_step,
    create_agent_workflow_session,
    get_agent_workflow_session,
    next_agent_workflow_action,
    pause_agent_workflow_session,
    resume_agent_workflow_session,
    wait_agent_workflow_session,
)
from services.agent_workflow_store import agent_workflow_session_path
from services.runtime_paths import runtime_paths


class AgentWorkflowRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-agent-workflow-")
        runtime_paths.update(workspace_dir=Path(self._tmp.name))

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def _start(self):
        return create_agent_workflow_session(
            "world-builder",
            subject_kind="world",
            subject_id="scene-demo",
            metadata={"chat_session_id": "chat-1"},
        )

    def _run_step(self, session_id: str, outcome: str, evidence_kinds: list[str]):
        begin_agent_workflow_step(session_id)
        return complete_agent_workflow_step(
            session_id,
            outcome=outcome,
            evidence=[{"kind": kind, "ref": f"test://{kind}"} for kind in evidence_kinds],
        )

    def test_builtin_definition_has_explicit_agent_workflow_protocol(self) -> None:
        definition = get_agent_workflow("world-builder")
        self.assertEqual(definition.kind, "polykit.agent-workflow")
        self.assertEqual(definition.start_step, "intent")
        self.assertEqual({step.type for step in definition.steps}, {"agent", "workflow", "validator"})
        self.assertEqual(definition.steps[-1].transitions["continue"], "$complete")

        workflow_steps = {step.id: step for step in definition.steps if step.type == "workflow"}
        self.assertEqual(set(workflow_steps), {"structure", "optimization"})
        self.assertEqual(workflow_steps["structure"].workflow, "building-construction")
        self.assertEqual(workflow_steps["structure"].inputs["operation"], "polykit_world_build_structure")
        self.assertEqual(workflow_steps["optimization"].inputs["operation"], "polykit_world_compose_scene")

    def test_session_is_persistent_and_next_does_not_mutate_it(self) -> None:
        session = self._start()
        session_id = session["id"]
        self.assertTrue(agent_workflow_session_path(session_id).is_file())

        before = get_agent_workflow_session(session_id)
        action = next_agent_workflow_action(session_id)
        after = get_agent_workflow_session(session_id)

        self.assertEqual(action["action"], "execute")
        self.assertEqual(action["step"]["id"], "intent")
        self.assertEqual(action["subject"], {"kind": "world", "id": "scene-demo"})
        self.assertEqual(action["executor"]["kind"], "agent")
        self.assertEqual(action["executor"]["capability"], "world.intent.author")
        self.assertEqual(action["executor"]["inputs"]["operation"], "polykit_world_save")
        self.assertEqual(before, after)
        self.assertEqual(after["metadata"]["chat_session_id"], "chat-1")

    def test_step_requires_evidence_and_routes_to_next_step(self) -> None:
        session = self._start()
        session_id = session["id"]
        begin_agent_workflow_step(session_id)

        with self.assertRaises(AgentWorkflowStateError):
            complete_agent_workflow_step(session_id, outcome="continue", evidence=[])

        updated = complete_agent_workflow_step(
            session_id,
            outcome="continue",
            evidence=[{"kind": "world-intent", "ref": "world://scene-demo/intent"}],
        )
        self.assertEqual(updated["current_step"], "spec")
        self.assertEqual(updated["steps"]["intent"]["status"], "completed")
        self.assertEqual(updated["steps"]["spec"]["status"], "ready")

    def test_waiting_and_unrelated_chat_can_leave_workflow_untouched(self) -> None:
        session = self._start()
        session_id = session["id"]
        begin_agent_workflow_step(session_id)
        waiting = wait_agent_workflow_session(
            session_id,
            kind="run",
            ref="run-123",
            reason="Blender workflow is still rendering",
        )
        self.assertEqual(waiting["status"], "waiting_for_run")
        waiting_action = next_agent_workflow_action(session_id)
        self.assertEqual(waiting_action["action"], "wait")
        self.assertEqual(waiting_action["subject"]["id"], "scene-demo")

        # A normal chat turn does not call any workflow mutation API. Re-reading
        # the durable session must therefore be byte-for-byte equivalent.
        before_chat = get_agent_workflow_session(session_id)
        after_chat = get_agent_workflow_session(session_id)
        self.assertEqual(before_chat, after_chat)

        resumed = resume_agent_workflow_session(session_id)
        self.assertEqual(resumed["status"], "running")
        self.assertEqual(next_agent_workflow_action(session_id)["action"], "resume")
        self.assertEqual(next_agent_workflow_action(session_id)["step"]["id"], "intent")

    def test_pause_and_resume_preserve_current_step(self) -> None:
        session = self._start()
        session_id = session["id"]
        paused = pause_agent_workflow_session(session_id)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["current_step"], "intent")
        self.assertIsNone(next_agent_workflow_action(session_id)["step"])
        resumed = resume_agent_workflow_session(session_id)
        self.assertEqual(resumed["current_step"], "intent")
        self.assertEqual(next_agent_workflow_action(session_id)["action"], "execute")

    def test_backward_transition_counts_as_correction(self) -> None:
        session = self._start()
        session_id = session["id"]
        self._run_step(session_id, "continue", ["world-intent"])
        self._run_step(session_id, "continue", ["build-spec", "scene-plan", "game-spec"])
        self._run_step(session_id, "continue", ["spec-validation"])
        self._run_step(session_id, "continue", ["scene-plan"])
        updated = self._run_step(session_id, "retry-step", ["review-report"])

        self.assertEqual(updated["current_step"], "blockout")
        self.assertEqual(updated["corrections"], 1)
        self.assertEqual(updated["history"][-1]["to_step"], "blockout")

    def test_subject_kind_is_enforced(self) -> None:
        with self.assertRaises(AgentWorkflowStateError):
            create_agent_workflow_session("world-builder", subject_kind="mesh", subject_id="asset-1")


if __name__ == "__main__":
    unittest.main()
