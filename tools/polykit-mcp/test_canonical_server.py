from __future__ import annotations

import asyncio
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

    def test_run_control_tools_use_canonical_run_routes(self) -> None:
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
        self.assertEqual(calls[0], ("GET", "/runs/run%201"))
        self.assertEqual(calls[1], ("GET", "/runs/run%201/inspect"))
        self.assertEqual(
            calls[2],
            ("POST", "/runs/run%201/signals", {"name": "approve", "payload": {"ok": True}}),
        )
        self.assertEqual(calls[3], ("POST", "/runs/run%201/retry"))
        self.assertEqual(calls[4], ("DELETE", "/runs/run%201"))

    def test_non_run_tools_delegate_to_base_adapter(self) -> None:
        fallback = AsyncMock(return_value={"ok": True})
        with patch.object(canonical_server, "_base_dispatch", fallback):
            result = asyncio.run(canonical_server._dispatch("polykit_health", {}))
        self.assertEqual(result, {"ok": True})
        fallback.assert_awaited_once_with("polykit_health", {})


if __name__ == "__main__":
    unittest.main()
