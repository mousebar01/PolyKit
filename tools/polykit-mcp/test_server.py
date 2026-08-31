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
    def test_adapter_imports_no_product_domain_modules(self) -> None:
        source = SERVER_PATH.read_text(encoding="utf-8")
        for forbidden in ("from services", "import services", "from routers", "import routers"):
            self.assertNotIn(forbidden, source)

    def test_tool_surface_is_unique_and_contains_no_agent_task_runtime(self) -> None:
        tools = asyncio.run(server_module.list_tools())
        names = [tool.name for tool in tools]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("polykit_workflow_inspect", names)
        self.assertIn("polykit_world_validate", names)
        self.assertIn("polykit_world_build_structure", names)
        self.assertIn("polykit_world_compile_repair", names)
        forbidden = ("agent_workflow", "session_begin", "session_complete", "world_update_stage")
        self.assertFalse(any(token in name for name in names for token in forbidden))

    def test_mcp_validator_enum_matches_visual_and_spatial_surface(self) -> None:
        self.assertIn("world.spatial.validate", server_module.WORLD_VALIDATORS)
        self.assertIn("world.visual.validate", server_module.WORLD_VALIDATORS)
        self.assertEqual(len(server_module.WORLD_VALIDATORS), len(set(server_module.WORLD_VALIDATORS)))

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
                    "capability": "world.spatial.validate",
                    "run_id": "run-structure",
                },
            ))
        self.assertEqual(result["status"], "pass")
        request.assert_awaited_once_with(
            "POST",
            "/workspace-library/worlds/winter%20cabin/validate",
            {"capability": "world.spatial.validate", "run_id": "run-structure"},
        )

    def test_compile_repair_is_a_pure_http_compiler_proxy(self) -> None:
        request = AsyncMock(return_value={"status": "blocked", "workflow_definition": None})
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch(
                "polykit_world_compile_repair",
                {
                    "world_id": "winter cabin",
                    "capability": "world.spatial.validate",
                    "repair_scope_id": "repair:world.spatial.validate:spatial.attachment.cabin.wall-floor",
                    "run_id": "run-structure",
                    "allow_scope_expansion": False,
                },
            ))
        self.assertEqual(result["status"], "blocked")
        request.assert_awaited_once_with(
            "POST",
            "/workspace-library/worlds/winter%20cabin/production-recipes/compile",
            {
                "capability": "world.spatial.validate",
                "repair_scope_id": "repair:world.spatial.validate:spatial.attachment.cabin.wall-floor",
                "run_id": "run-structure",
                "collection": "Scenes",
                "render_preview": True,
                "allow_scope_expansion": False,
            },
        )

    def test_compile_repair_can_explicitly_forward_scope_expansion(self) -> None:
        request = AsyncMock(return_value={"status": "ready", "scope_expanded": True})
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch(
                "polykit_world_compile_repair",
                {
                    "world_id": "cabin",
                    "capability": "world.construction.validate",
                    "repair_scope_id": "repair:world.construction.validate:attachment-gap",
                    "collection": "Repairs",
                    "render_preview": False,
                    "allow_scope_expansion": True,
                },
            ))
        self.assertTrue(result["scope_expanded"])
        request.assert_awaited_once_with(
            "POST",
            "/workspace-library/worlds/cabin/production-recipes/compile",
            {
                "capability": "world.construction.validate",
                "repair_scope_id": "repair:world.construction.validate:attachment-gap",
                "run_id": None,
                "collection": "Repairs",
                "render_preview": False,
                "allow_scope_expansion": True,
            },
        )

    def test_compile_repair_rejects_unknown_validator_before_http(self) -> None:
        request = AsyncMock()
        with patch.object(server_module, "_request_json", request):
            with self.assertRaisesRegex(ValueError, "Unsupported validator"):
                asyncio.run(server_module._dispatch(
                    "polykit_world_compile_repair",
                    {
                        "world_id": "cabin",
                        "capability": "world.fake.validate",
                        "repair_scope_id": "repair:fake",
                    },
                ))
        request.assert_not_awaited()

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
