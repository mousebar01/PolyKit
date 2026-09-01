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
        self.assertIn("polykit_skill_list", names)
        self.assertIn("polykit_skill_get", names)
        self.assertIn("polykit_skill_read_resource", names)
        self.assertIn("polykit_workflow_inspect", names)
        self.assertIn("polykit_workflow_signal", names)
        self.assertIn("polykit_workflow_retry", names)
        self.assertIn("polykit_world_validate", names)
        self.assertIn("polykit_world_build_structure", names)
        self.assertIn("polykit_world_compile_repair", names)
        self.assertIn("polykit_asset_search_external", names)
        self.assertIn("polykit_asset_import_external", names)
        forbidden = ("agent_workflow", "session_begin", "session_complete", "world_update_stage")
        self.assertFalse(any(token in name for name in names for token in forbidden))

    def test_skill_tools_are_explicitly_read_only(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(server_module.list_tools())}
        self.assertIn("read-only", (tools["polykit_skill_list"].description or "").lower())
        self.assertIn("does not authorize tools", tools["polykit_skill_get"].description or "")
        self.assertIn("never executed", tools["polykit_skill_read_resource"].description or "")

    def test_skill_catalog_and_content_are_get_only_proxies(self) -> None:
        request = AsyncMock(side_effect=[
            {"skills": [{"name": "reference-reconstruction"}]},
            {"name": "reference-reconstruction", "instructions": "Use WorkflowRun"},
            {"path": "references/guide one.md", "content": "guide"},
        ])
        with patch.object(server_module, "_request_json", request):
            catalog = asyncio.run(server_module._dispatch("polykit_skill_list", {}))
            skill = asyncio.run(server_module._dispatch("polykit_skill_get", {"name": "reference-reconstruction"}))
            resource = asyncio.run(server_module._dispatch(
                "polykit_skill_read_resource",
                {"name": "reference-reconstruction", "path": "references/guide one.md"},
            ))
        self.assertEqual(catalog["skills"][0]["name"], "reference-reconstruction")
        self.assertIn("WorkflowRun", skill["instructions"])
        self.assertEqual(resource["content"], "guide")
        self.assertEqual(request.await_args_list[0].args, ("GET", "/agent-skills"))
        self.assertEqual(request.await_args_list[1].args, ("GET", "/agent-skills/reference-reconstruction"))
        self.assertEqual(
            request.await_args_list[2].args,
            ("GET", "/agent-skills/reference-reconstruction/resources/references/guide%20one.md"),
        )
        for call in request.await_args_list:
            self.assertNotIn("/workflow-runs/", call.args[1])

    def test_mcp_validator_enum_matches_visual_and_spatial_surface(self) -> None:
        self.assertIn("world.spatial.validate", server_module.WORLD_VALIDATORS)
        self.assertIn("world.visual.validate", server_module.WORLD_VALIDATORS)
        self.assertEqual(len(server_module.WORLD_VALIDATORS), len(set(server_module.WORLD_VALIDATORS)))

    def test_compile_repair_tool_description_requires_separate_execution(self) -> None:
        tools = asyncio.run(server_module.list_tools())
        compile_tool = next(tool for tool in tools if tool.name == "polykit_world_compile_repair")
        description = compile_tool.description or ""
        self.assertIn("never starts a WorkflowRun", description)
        self.assertIn("polykit_workflow_execute", description)

    def test_workflow_inspect_is_a_read_only_get_proxy(self) -> None:
        request = AsyncMock(return_value={"run_id": "run-1", "status": "running"})
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch("polykit_workflow_inspect", {"run_id": "run-1"}))
        self.assertEqual(result["run_id"], "run-1")
        request.assert_awaited_once_with("GET", "/workflow-runs/run-1/inspect")

    def test_workflow_signal_forwards_judgment_to_same_run(self) -> None:
        request = AsyncMock(return_value={"run_id": "run 1", "status": "pending", "resumed": True})
        payload = {"decision": "approve", "score": 0.93}
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch(
                "polykit_workflow_signal",
                {"run_id": "run 1", "name": "visual-approval", "payload": payload},
            ))
        self.assertTrue(result["resumed"])
        request.assert_awaited_once_with(
            "POST",
            "/workflow-runs/run%201/signals",
            {"name": "visual-approval", "payload": payload},
        )
        self.assertNotEqual(request.await_args.args[1], "/workflow-runs/execute")

    def test_workflow_retry_resumes_same_run_without_execute_submission(self) -> None:
        request = AsyncMock(return_value={"run_id": "run-2", "status": "pending", "resumed": True})
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch("polykit_workflow_retry", {"run_id": "run-2"}))
        self.assertEqual(result["run_id"], "run-2")
        request.assert_awaited_once_with("POST", "/workflow-runs/run-2/retry")
        self.assertNotEqual(request.await_args.args[1], "/workflow-runs/execute")

    def test_workflow_signal_and_retry_descriptions_preserve_run_authority(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(server_module.list_tools())}
        signal_description = tools["polykit_workflow_signal"].description or ""
        retry_description = tools["polykit_workflow_retry"].description or ""
        self.assertIn("same run_id", signal_description)
        self.assertIn("never creates a new run", signal_description)
        self.assertIn("same run_id", retry_description)
        self.assertIn("never submits a replacement WorkflowRun", retry_description)

    def test_workflow_signal_payload_schema_accepts_json_values(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(server_module.list_tools())}
        payload_schema = tools["polykit_workflow_signal"].input_schema["properties"]["payload"]
        self.assertEqual(
            {entry["type"] for entry in payload_schema["anyOf"]},
            {"object", "array", "string", "number", "boolean", "null"},
        )

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

    def test_external_asset_search_is_read_only_http_proxy(self) -> None:
        request = AsyncMock(return_value={"success": True, "provider": "polyhaven", "matches": []})
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch(
                "polykit_asset_search_external",
                {"query": "wooden chair", "category": "furniture", "limit": 3},
            ))
        self.assertEqual(result["provider"], "polyhaven")
        request.assert_awaited_once_with(
            "POST",
            "/workspace-library/providers/polyhaven/search",
            {"query": "wooden chair", "category": "furniture", "limit": 3, "refresh": False},
        )

    def test_external_asset_import_requires_valid_resolution_and_never_creates_run(self) -> None:
        request = AsyncMock(return_value={"success": True, "provider": "polyhaven", "asset": {"asset_id": "Chair_01"}})
        with patch.object(server_module, "_request_json", request):
            result = asyncio.run(server_module._dispatch(
                "polykit_asset_import_external",
                {"asset_id": "Chair_01", "resolution": "1k"},
            ))
        self.assertEqual(result["asset"]["asset_id"], "Chair_01")
        request.assert_awaited_once_with(
            "POST",
            "/workspace-library/providers/polyhaven/import",
            {"asset_id": "Chair_01", "resolution": "1k"},
        )
        self.assertNotIn("workflow-runs", request.await_args.args[1])

    def test_external_tool_descriptions_separate_search_and_side_effect(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(server_module.list_tools())}
        search_description = tools["polykit_asset_search_external"].description or ""
        import_description = tools["polykit_asset_import_external"].description or ""
        self.assertIn("Read-only", search_description)
        self.assertIn("never downloads", search_description)
        self.assertIn("side effect", import_description)
        self.assertIn("asset_id", import_description)

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
        called_path = request.await_args.args[1]
        self.assertNotIn("/workflow-runs/", called_path)

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
