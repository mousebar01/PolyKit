import asyncio
import json
import unittest
from pathlib import Path

from mcp_entrypoint import EXTRA_TOOL_NAMES, list_tools


class McpEntrypointTests(unittest.TestCase):
    def test_combined_entrypoint_keeps_legacy_tools_and_adds_workflow_controls(self) -> None:
        tools = asyncio.run(list_tools())
        names = {tool.name for tool in tools}
        self.assertIn("polykit_world_create", names)
        self.assertIn("polykit_get_generation_status", names)
        self.assertTrue(EXTRA_TOOL_NAMES.issubset(names))
        self.assertIn("polykit_agent_workflow_next", names)
        self.assertIn("polykit_world_validate", names)
        self.assertIn("polykit_world_build_structure", names)

    def test_repo_mcp_config_uses_combined_entrypoint(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        args = config["mcpServers"]["polykit"]["args"]
        self.assertEqual(args[-1], "api/mcp_entrypoint.py")


if __name__ == "__main__":
    unittest.main()
