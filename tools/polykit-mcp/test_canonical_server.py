from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import canonical_server


class CanonicalMcpRoutingTests(unittest.TestCase):
    def test_asset_from_text_uses_shared_application_command_with_agent_initiator(self) -> None:
        request = AsyncMock(return_value={"run_id": "run-asset", "status": "pending"})
        with patch.object(canonical_server._base, "_request_json", request):
            result = asyncio.run(
                canonical_server._dispatch(
                    "polykit_asset_from_text",
                    {"prompt": "single wooden chair", "enable_texture": False},
                )
            )
        self.assertEqual(result["run_id"], "run-asset")
        method, path, payload = request.await_args.args
        self.assertEqual((method, path), ("POST", "/commands/generate-asset"))
        self.assertEqual(payload["prompt"], "single wooden chair")
        self.assertFalse(payload["enable_texture"])
        self.assertEqual(
            payload["initiator"],
            {"type": "agent", "surface": "mcp.asset-from-text"},
        )

    def test_workflow_execute_submits_generic_run_and_preserves_workflow_source(self) -> None:
        request = AsyncMock(return_value={"run_id": "run-workflow", "status": "pending"})
        workflow = {
            "workflow_id": "wf-1",
            "prompt": {"out": {"class_type": "polykit.output", "inputs": {}}},
        }
        with patch.object(canonical_server._base, "_request_json", request):
            asyncio.run(
                canonical_server._dispatch(
                    "polykit_workflow_execute",
                    {"request": workflow},
                )
            )
        method, path, payload = request.await_args.args
        self.assertEqual((method, path), ("POST", "/runs"))
        self.assertEqual(payload["plan"]["source"], {"kind": "workflow", "id": "wf-1"})
        self.assertEqual(
            payload["initiator"],
            {"type": "agent", "surface": "mcp.workflow-execute"},
        )

    def test_run_control_tools_use_compact_canonical_run_routes(self) -> None:
        request = AsyncMock(return_value={"run_id": "run 1", "status": "pending"})
        with patch.object(canonical_server._base, "_request_json", request):
            asyncio.run(canonical_server._dispatch("polykit_workflow_status", {"run_id": "run 1"}))
            asyncio.run(canonical_server._dispatch("polykit_workflow_inspect", {"run_id": "run 1"}))
            asyncio.run(
                canonical_server._dispatch(
                    "polykit_workflow_signal",
                    {"run_id": "run 1", "name": "approve", "payload": {"ok": True}},
                )
            )
            asyncio.run(canonical_server._dispatch("polykit_workflow_retry", {"run_id": "run 1"}))
            asyncio.run(canonical_server._dispatch("polykit_workflow_cancel", {"run_id": "run 1"}))

        calls = [call.args for call in request.await_args_list]
        self.assertEqual(calls[0], ("GET", "/runs/run%201?compact=true"))
        self.assertEqual(
            calls[1],
            ("GET", "/runs/run%201/inspect?events_limit=20&include_events=true"),
        )
        self.assertEqual(
            calls[2],
            ("POST", "/runs/run%201/signals", {"name": "approve", "payload": {"ok": True}}),
        )
        self.assertEqual(calls[3], ("POST", "/runs/run%201/retry"))
        self.assertEqual(calls[4], ("DELETE", "/runs/run%201"))

    def test_inspect_passes_reversible_cursor_options_to_source_api(self) -> None:
        request = AsyncMock(return_value={"events": [], "next_event_seq": 15})
        with patch.object(canonical_server._base, "_request_json", request):
            asyncio.run(
                canonical_server._dispatch(
                    "polykit_workflow_inspect",
                    {"run_id": "r", "since_seq": 10, "events_limit": 5},
                )
            )
        self.assertEqual(
            request.await_args.args,
            ("GET", "/runs/r/inspect?events_limit=5&include_events=true&since_seq=10"),
        )

        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            asyncio.run(
                canonical_server._dispatch(
                    "polykit_workflow_inspect",
                    {"run_id": "r", "since_seq": 10, "before_seq": 20},
                )
            )

    def test_skill_resource_uses_bounded_source_chunk(self) -> None:
        request = AsyncMock(return_value={"content": "abcd", "next_offset": 36, "truncated": True})
        with patch.object(canonical_server._base, "_request_json", request):
            asyncio.run(
                canonical_server._dispatch(
                    "polykit_skill_read_resource",
                    {"name": "scene-review", "path": "references/guide.md", "offset": 32, "limit": 4},
                )
            )
        self.assertEqual(
            request.await_args.args,
            ("GET", "/agent-skills/scene-review/resources/references/guide.md?offset=32&limit=4"),
        )

    def test_tool_catalog_exposes_efficiency_controls(self) -> None:
        with patch.dict(os.environ, {"POLYKIT_MCP_PROFILE": "all"}):
            tools = asyncio.run(canonical_server.list_tools())
        by_name = {tool.name: tool for tool in tools}
        inspect_schema = by_name["polykit_workflow_inspect"].input_schema["properties"]
        resource_schema = by_name["polykit_skill_read_resource"].input_schema["properties"]
        self.assertIn("since_seq", inspect_schema)
        self.assertIn("before_seq", inspect_schema)
        self.assertEqual(inspect_schema["events_limit"]["maximum"], 200)
        self.assertIn("offset", resource_schema)
        self.assertEqual(resource_schema["limit"]["maximum"], 32 * 1024)
        self.assertIn("do not use it for polling", by_name["polykit_workflow_inspect"].description)

    def test_discovery_profiles_are_opt_in_and_prefix_based(self) -> None:
        with patch.dict(os.environ, {"POLYKIT_MCP_PROFILE": "all"}):
            all_names = {tool.name for tool in asyncio.run(canonical_server.list_tools())}
        with patch.dict(os.environ, {"POLYKIT_MCP_PROFILE": "core"}):
            core_names = {tool.name for tool in asyncio.run(canonical_server.list_tools())}
        with patch.dict(os.environ, {"POLYKIT_MCP_PROFILE": "asset"}):
            asset_names = {tool.name for tool in asyncio.run(canonical_server.list_tools())}
        with patch.dict(os.environ, {"POLYKIT_MCP_PROFILE": "world"}):
            world_names = {tool.name for tool in asyncio.run(canonical_server.list_tools())}
        with patch.dict(os.environ, {"POLYKIT_MCP_PROFILE": "typo-safe-fallback"}):
            fallback_names = {tool.name for tool in asyncio.run(canonical_server.list_tools())}

        self.assertIn("polykit_workflow_status", core_names)
        self.assertNotIn("polykit_asset_from_text", core_names)
        self.assertNotIn("polykit_world_get", core_names)
        self.assertIn("polykit_asset_from_text", asset_names)
        self.assertNotIn("polykit_world_get", asset_names)
        self.assertIn("polykit_world_get", world_names)
        self.assertNotIn("polykit_asset_from_text", world_names)
        self.assertEqual(fallback_names, all_names)

    def test_compact_json_serializer_omits_formatting_whitespace(self) -> None:
        text = canonical_server._json_text({"ok": True, "result": {"a": 1, "b": [2, 3]}})
        self.assertNotIn("\n", text)
        self.assertNotIn(": ", text)
        self.assertNotIn(", ", text)

    def test_non_run_tools_delegate_to_base_adapter(self) -> None:
        fallback = AsyncMock(return_value={"ok": True})
        with patch.object(canonical_server, "_base_dispatch", fallback):
            result = asyncio.run(canonical_server._dispatch("polykit_health", {}))
        self.assertEqual(result, {"ok": True})
        fallback.assert_awaited_once_with("polykit_health", {})


if __name__ == "__main__":
    unittest.main()
