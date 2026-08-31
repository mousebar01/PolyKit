from __future__ import annotations

import asyncio
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


SERVER_PATH = Path(__file__).with_name("server.py")
SPEC = importlib.util.spec_from_file_location("polykit_mcp_server", SERVER_PATH)
assert SPEC and SPEC.loader
server_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server_module)


class PolyKitMcpTests(unittest.TestCase):
    def test_tool_surface_is_unique_and_contains_no_agent_task_runtime(self) -> None:
        tools = asyncio.run(server_module.list_tools())
        names = [tool.name for tool in tools]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("polykit_workflow_inspect", names)
        self.assertIn("polykit_world_validate", names)
        self.assertIn("polykit_world_build_structure", names)
        forbidden = ("agent_workflow", "session_begin", "session_complete", "world_update_stage")
        self.assertFalse(any(token in name for name in names for token in forbidden))

    def test_workflow_inspect_is_a_read_only_get_proxy(self) -> None:
        request = AsyncMock(return_value={"run_id": "run-1", "status": "running"})
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch("polykit_workflow_inspect", {"run_id": "run-1"}))
        self.assertEqual(result["run_id"], "run-1")
        request.assert_awaited_once_with("GET", "/workflow-runs/run-1/inspect")

    def test_world_validate_only_proxies_domain_validation(self) -> None:
        request = AsyncMock(return_value={"status": "pass", "issues": []})
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch(
                "polykit_world_validate",
                {
                    "world_id": "winter cabin",
                    "capability": "world.construction.validate",
                    "run_id": "run-structure",
                },
            ))
        self.assertEqual(result["status"], "pass")
        request.assert_awaited_once_with(
            "POST",
            "/workspace-library/worlds/winter%20cabin/validate",
            {"capability": "world.construction.validate", "run_id": "run-structure"},
        )

    def test_structure_build_returns_the_canonical_run_response(self) -> None:
        request = AsyncMock(return_value={"run_id": "run-build", "status": "pending"})
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch(
                "polykit_world_build_structure",
                {"world_id": "cabin", "building_id": "main"},
            ))
        self.assertEqual(result["run_id"], "run-build")
        request.assert_awaited_once_with(
            "POST",
            "/workspace-library/worlds/cabin/build-structure",
            {"building_id": "main", "collection": "Scenes", "render_preview": True},
        )

    def test_call_tool_returns_machine_readable_error_json(self) -> None:
        with patch.object(server_module, "_dispatch", AsyncMock(side_effect=ValueError("bad input"))):
            content = asyncio.run(server_module.call_tool("polykit_world_get", {}))
        payload = json.loads(content[0].text)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "adapter")
        self.assertIn("bad input", payload["error"]["message"])

    def test_world_save_rejects_mismatched_document_id_before_http(self) -> None:
        request = AsyncMock()
        with patch.object(server_module, "_request_json", request):
            with self.assertRaisesRegex(ValueError, "document.id"):
                asyncio.run(server_module._dispatch(
                    "polykit_world_save",
                    {"world_id": "one", "document": {"id": "two"}},
                ))
        request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
