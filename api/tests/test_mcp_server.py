import unittest

from mcp_server import _dispatch, _on_list_tools, list_tools


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self):
        self.payload = None

    async def post(self, url, **kwargs):
        self.payload = (url, kwargs)
        return _Response({"run_id": "image-run"})


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
                "polykit_generate_image",
                "polykit_world_attach_asset",
            }.issubset(names)
        )

        result = await _on_list_tools(None, None)
        self.assertEqual({tool.name for tool in result.tools}, names)

    async def test_generate_image_dispatches_a_local_text_image_dag(self) -> None:
        client = _Client()
        message = await _dispatch(
            client,
            "polykit_generate_image",
            {"prompt": "single low-poly observatory", "params": {"seed": 7}},
        )
        self.assertIn("image-run", message)
        url, kwargs = client.payload
        self.assertTrue(url.endswith("/workflow-runs/execute"))
        payload = kwargs["json"]
        self.assertEqual(payload["prompt"]["image"]["class_type"], "anima/generate")
        self.assertEqual(payload["prompt"]["output"]["class_type"], "polykit.image_output")


if __name__ == "__main__":
    unittest.main()
