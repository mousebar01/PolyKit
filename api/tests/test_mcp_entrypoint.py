import asyncio
import json
import unittest
from pathlib import Path

from mcp_entrypoint import EXTRA_TOOL_NAMES, list_tools


class McpEntrypointTests(unittest.TestCase):
    def test_entrypoint_exposes_world_domain_bridges_without_agent_task_runtime(self) -> None:
        tools = asyncio.run(list_tools())
        names = {tool.name for tool in tools}
        self.assertIn("polykit_world_create", names)
        self.assertIn("polykit_get_generation_status", names)
        self.assertEqual(EXTRA_TOOL_NAMES, {"polykit_world_validate", "polykit_world_build_structure"})
        self.assertTrue(EXTRA_TOOL_NAMES.issubset(names))
        self.assertFalse(any(name.startswith("polykit_agent_workflow_") for name in names))
        world_create = next(tool for tool in tools if tool.name == "polykit_world_create")
        self.assertNotIn("Agent Workflow", world_create.description)
        self.assertIn("World API", world_create.description)

    def test_repo_mcp_config_uses_world_domain_entrypoint(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        args = config["mcpServers"]["polykit"]["args"]
        self.assertEqual(args[-1], "api/mcp_entrypoint.py")


if __name__ == "__main__":
    unittest.main()
