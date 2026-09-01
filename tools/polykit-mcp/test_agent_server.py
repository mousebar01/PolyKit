from __future__ import annotations

import asyncio
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


SERVER_PATH = Path(__file__).with_name("agent_server.py")
SPEC = importlib.util.spec_from_file_location("polykit_mcp_agent_server_test", SERVER_PATH)
assert SPEC and SPEC.loader
agent_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_server)


class PolyKitAgentServerEfficiencyTests(unittest.TestCase):
    def test_status_projection_omits_large_meta_but_keeps_waiting_gate(self) -> None:
        response = {
            "run_id": "run-1",
            "status": "waiting",
            "progress": 65,
            "step": "Waiting",
            "output_url": None,
            "error": None,
            "meta": {
                "workflow_id": "workflow-1",
                "collection": "Scenes",
                "observability": {"events": [{"seq": index} for index in range(100)]},
                "execution": {
                    "steps": {"node": {"checkpoint": {"large": "payload"}}},
                    "waiting": {"signal_name": "approve", "node_id": "gate"},
                },
            },
        }
        with patch.object(agent_server, "_BASE_DISPATCH", AsyncMock(return_value=response)):
            result = asyncio.run(agent_server._dispatch("polykit_workflow_status", {"run_id": "run-1"}))
        self.assertNotIn("meta", result)
        self.assertEqual(result["workflow_id"], "workflow-1")
        self.assertEqual(result["collection"], "Scenes")
        self.assertEqual(result["waiting"]["signal_name"], "approve")

    def test_inspect_cursor_returns_only_new_bounded_events(self) -> None:
        response = {
            "run_id": "run-1",
            "status": "running",
            "events": [{"seq": index, "type": "node.phase"} for index in range(1, 7)],
            "nodes": {"a": {"status": "running"}},
        }
        with patch.object(agent_server, "_BASE_DISPATCH", AsyncMock(return_value=response)):
            result = asyncio.run(agent_server._dispatch(
                "polykit_workflow_inspect",
                {"run_id": "run-1", "since_seq": 2, "events_limit": 2},
            ))
        self.assertEqual([event["seq"] for event in result["events"]], [3, 4])
        self.assertEqual(result["next_event_seq"], 4)
        self.assertEqual(result["previous_event_seq"], 3)
        self.assertEqual(result["latest_event_seq"], 6)
        self.assertTrue(result["has_more_events"])
        self.assertTrue(result["has_older_events"])
        self.assertFalse(result["events_truncated_before"])
        self.assertEqual(result["nodes"]["a"]["status"], "running")

    def test_inspect_without_cursor_returns_recent_window_and_backward_cursor(self) -> None:
        response = {"events": [{"seq": index} for index in range(1, 31)]}
        with patch.object(agent_server, "_BASE_DISPATCH", AsyncMock(return_value=response)):
            result = asyncio.run(agent_server._dispatch("polykit_workflow_inspect", {"run_id": "run-1"}))
        self.assertEqual(len(result["events"]), agent_server._DEFAULT_EVENT_LIMIT)
        self.assertEqual(result["events"][0]["seq"], 11)
        self.assertEqual(result["previous_event_seq"], 11)
        self.assertEqual(result["next_event_seq"], 30)
        self.assertTrue(result["has_older_events"])
        self.assertTrue(result["events_truncated_before"])

    def test_inspect_before_cursor_recovers_older_history(self) -> None:
        response = {"events": [{"seq": index} for index in range(1, 31)]}
        with patch.object(agent_server, "_BASE_DISPATCH", AsyncMock(return_value=response)):
            result = asyncio.run(agent_server._dispatch(
                "polykit_workflow_inspect",
                {"run_id": "run-1", "before_seq": 11, "events_limit": 5},
            ))
        self.assertEqual([event["seq"] for event in result["events"]], [6, 7, 8, 9, 10])
        self.assertEqual(result["previous_event_seq"], 6)
        self.assertEqual(result["next_event_seq"], 30)
        self.assertTrue(result["has_older_events"])
        self.assertTrue(result["events_truncated_before"])

    def test_inspect_without_events_returns_only_live_forward_cursor(self) -> None:
        response = {"events": [{"seq": index} for index in range(1, 31)], "nodes": {"a": {"status": "done"}}}
        with patch.object(agent_server, "_BASE_DISPATCH", AsyncMock(return_value=response)):
            result = asyncio.run(agent_server._dispatch(
                "polykit_workflow_inspect",
                {"run_id": "run-1", "include_events": False},
            ))
        self.assertEqual(result["events"], [])
        self.assertEqual(result["next_event_seq"], 30)
        self.assertEqual(result["previous_event_seq"], 0)
        self.assertFalse(result["has_more_events"])
        self.assertFalse(result["has_older_events"])
        self.assertTrue(result["events_truncated_before"])
        self.assertEqual(result["nodes"]["a"]["status"], "done")

    def test_inspect_rejects_conflicting_forward_and_backward_cursors(self) -> None:
        response = {"events": [{"seq": 1}]}
        with patch.object(agent_server, "_BASE_DISPATCH", AsyncMock(return_value=response)):
            with self.assertRaisesRegex(ValueError, "mutually exclusive"):
                asyncio.run(agent_server._dispatch(
                    "polykit_workflow_inspect",
                    {"run_id": "run-1", "since_seq": 1, "before_seq": 2},
                ))

    def test_skill_resource_is_chunked_with_continuation_offset(self) -> None:
        response = {
            "skill": "scene-review",
            "path": "references/guide.md",
            "content": "abcdefghijklmnopqrstuvwxyz",
        }
        with patch.object(agent_server, "_BASE_DISPATCH", AsyncMock(return_value=response)):
            result = asyncio.run(agent_server._dispatch(
                "polykit_skill_read_resource",
                {"name": "scene-review", "path": "references/guide.md", "offset": 5, "limit": 7},
            ))
        self.assertEqual(result["content"], "fghijkl")
        self.assertEqual(result["offset"], 5)
        self.assertEqual(result["next_offset"], 12)
        self.assertEqual(result["total_chars"], 26)
        self.assertTrue(result["truncated"])

    def test_agent_tool_schemas_expose_reversible_cursor_and_chunk_controls(self) -> None:
        with patch.dict(agent_server.os.environ, {"POLYKIT_MCP_PROFILE": "all"}):
            tools = {tool.name: tool for tool in asyncio.run(agent_server.list_tools())}
        status_description = tools["polykit_workflow_status"].description or ""
        inspect_properties = tools["polykit_workflow_inspect"].input_schema["properties"]
        resource_properties = tools["polykit_skill_read_resource"].input_schema["properties"]
        self.assertIn("Lightweight", status_description)
        self.assertIn("since_seq", inspect_properties)
        self.assertIn("before_seq", inspect_properties)
        self.assertIn("events_limit", inspect_properties)
        self.assertIn("include_events", inspect_properties)
        self.assertIn("offset", resource_properties)
        self.assertIn("limit", resource_properties)

    def test_default_all_profile_does_not_hide_tools(self) -> None:
        with patch.dict(agent_server.os.environ, {}, clear=True):
            projected = asyncio.run(agent_server.list_tools())
        canonical = asyncio.run(agent_server._BASE_LIST_TOOLS())
        self.assertEqual({tool.name for tool in projected}, {tool.name for tool in canonical})

    def test_core_profile_only_filters_tool_discovery(self) -> None:
        with patch.dict(agent_server.os.environ, {"POLYKIT_MCP_PROFILE": "core"}):
            names = {tool.name for tool in asyncio.run(agent_server.list_tools())}
        self.assertIn("polykit_workflow_status", names)
        self.assertIn("polykit_skill_get", names)
        self.assertNotIn("polykit_asset_from_text", names)
        self.assertNotIn("polykit_world_create", names)

    def test_unknown_profile_falls_back_to_all(self) -> None:
        with patch.dict(agent_server.os.environ, {"POLYKIT_MCP_PROFILE": "typo"}):
            projected = asyncio.run(agent_server.list_tools())
        canonical = asyncio.run(agent_server._BASE_LIST_TOOLS())
        self.assertEqual({tool.name for tool in projected}, {tool.name for tool in canonical})

    def test_json_output_is_compact_machine_readable_text(self) -> None:
        text = agent_server._json_text({"ok": True, "result": {"status": "running"}})
        self.assertEqual(text, '{"ok":true,"result":{"status":"running"}}')
        self.assertNotIn("\n", text)


if __name__ == "__main__":
    unittest.main()
