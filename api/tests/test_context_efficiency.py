import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import application.run_control as run_control
import routers.execution_runs as execution_runs
import services.agent_skills as agent_skills
from services.agent_skills import AgentSkillError


class RunContextEfficiencyTests(unittest.TestCase):
    def _inspection(self, count: int = 30) -> dict:
        return {
            "run_id": "run-1",
            "events": [
                {"seq": seq, "type": "node.phase", "phase": f"phase-{seq}"}
                for seq in range(1, count + 1)
            ],
            "nodes": {"n": {"status": "running"}},
            "evidence": [{"kind": "preview", "ref": "x"}],
        }

    def test_default_inspect_page_returns_latest_events_and_backward_cursor(self) -> None:
        result = run_control._project_inspection_events(
            self._inspection(),
            since_seq=None,
            before_seq=None,
            events_limit=5,
            include_events=True,
        )
        self.assertEqual([event["seq"] for event in result["events"]], [26, 27, 28, 29, 30])
        self.assertEqual(result["next_event_seq"], 30)
        self.assertEqual(result["previous_event_seq"], 26)
        self.assertEqual(result["latest_event_seq"], 30)
        self.assertTrue(result["has_older_events"])
        self.assertTrue(result["events_truncated_before"])
        self.assertEqual(result["nodes"]["n"]["status"], "running")

    def test_inspect_supports_forward_and_backward_continuation(self) -> None:
        forward = run_control._project_inspection_events(
            self._inspection(),
            since_seq=27,
            before_seq=None,
            events_limit=2,
            include_events=True,
        )
        self.assertEqual([event["seq"] for event in forward["events"]], [28, 29])
        self.assertEqual(forward["next_event_seq"], 29)
        self.assertTrue(forward["has_more_events"])

        backward = run_control._project_inspection_events(
            self._inspection(),
            since_seq=None,
            before_seq=26,
            events_limit=5,
            include_events=True,
        )
        self.assertEqual([event["seq"] for event in backward["events"]], [21, 22, 23, 24, 25])
        self.assertEqual(backward["previous_event_seq"], 21)
        self.assertTrue(backward["has_older_events"])

    def test_inspect_without_events_returns_only_live_forward_cursor(self) -> None:
        result = run_control._project_inspection_events(
            self._inspection(),
            since_seq=None,
            before_seq=None,
            events_limit=5,
            include_events=False,
        )
        self.assertEqual(result["events"], [])
        self.assertEqual(result["next_event_seq"], 30)
        self.assertEqual(result["previous_event_seq"], 0)
        self.assertFalse(result["has_more_events"])
        self.assertFalse(result["has_older_events"])
        self.assertTrue(result["events_truncated_before"])

    def test_compact_run_status_omits_durable_meta_but_keeps_small_routing_fields(self) -> None:
        job = SimpleNamespace(
            job_id="run-1",
            status="waiting",
            progress=55,
            step="Await approval",
            output_url=None,
            error=None,
            meta={
                "workflow_id": "wf-1",
                "collection": "Scenes",
                "artifact_kind": "scene",
                "large": {"payload": "x" * 1000},
                "execution": {"waiting": {"name": "approve", "node_id": "gate"}},
            },
        )
        coordinator = SimpleNamespace(jobs={"run-1": job}, purge_old_jobs=lambda: None)
        with patch.object(execution_runs, "run_coordinator", coordinator):
            result = asyncio.run(execution_runs.get_run("run-1", compact=True))
        self.assertIsNone(result.meta)
        self.assertEqual(result.workflow_id, "wf-1")
        self.assertEqual(result.collection, "Scenes")
        self.assertEqual(result.artifact_kind, "scene")
        self.assertEqual(result.waiting, {"name": "approve", "node_id": "gate"})


class AgentSkillContextEfficiencyTests(unittest.TestCase):
    def _write_skill(self, root: Path, name: str = "scene-review") -> Path:
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Review a scene without loading unnecessary context.\n"
            "---\n\n"
            + ("Very long instruction body.\n" * 100),
            encoding="utf-8",
        )
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "guide.md").write_text("0123456789abcdefghijklmnopqrstuvwxyz", encoding="utf-8")
        return skill_dir

    def test_skill_catalog_does_not_read_full_skill_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root)
            with patch.object(Path, "read_text", side_effect=AssertionError("full body read")):
                skills = agent_skills.list_agent_skills(root)
            self.assertEqual([skill["name"] for skill in skills], ["scene-review"])
            self.assertNotIn("instructions", skills[0])

    def test_resource_read_uses_frontmatter_identity_and_returns_requested_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_skill(root)
            with patch.object(agent_skills, "get_agent_skill", side_effect=AssertionError("must not load full SKILL.md")):
                resource = agent_skills.read_agent_skill_resource(
                    "scene-review",
                    "references/guide.md",
                    root,
                    offset=10,
                    limit=5,
                )
            self.assertEqual(resource["content"], "abcde")
            self.assertEqual(resource["offset"], 10)
            self.assertEqual(resource["next_offset"], 15)
            self.assertEqual(resource["total_chars"], 36)
            self.assertTrue(resource["truncated"])

    def test_resource_read_still_rejects_mismatched_skill_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = self._write_skill(root)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: other-skill\ndescription: mismatch\n---\n\nbody\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(AgentSkillError, "must match directory"):
                agent_skills.read_agent_skill_resource(
                    "scene-review",
                    "references/guide.md",
                    root,
                    limit=5,
                )


if __name__ == "__main__":
    unittest.main()
