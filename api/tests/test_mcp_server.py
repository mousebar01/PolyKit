import unittest

from mcp_server import _on_list_tools, list_tools


class McpWorldToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_world_tools_are_advertised_by_the_current_mcp_server(self) -> None:
        tools = await list_tools()
        names = {tool.name for tool in tools}
        self.assertTrue(
            {
                "polykit_world_get",
                "polykit_world_save",
                "polykit_world_update_stage",
                "polykit_world_list_workflows",
                "polykit_world_generate_asset",
                "polykit_world_attach_asset",
            }.issubset(names)
        )

        result = await _on_list_tools(None, None)
        self.assertEqual({tool.name for tool in result.tools}, names)


if __name__ == "__main__":
    unittest.main()
