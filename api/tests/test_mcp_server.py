import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch

from mcp_server import _dispatch, _on_list_tools, _workspace_image_reference, list_tools


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
                "polykit_generate_from_image",
                "polykit_remove_background",
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

    async def test_generate_from_image_forwards_texture_and_collection_options(self) -> None:
        client = _Client()
        with patch("builtins.open", mock_open(read_data=b"png")):
            message = await _dispatch(
                client,
                "polykit_generate_from_image",
                {
                    "image_path": "/workspace/hero.png",
                    "model_id": "trellis2/generate",
                    "collection": "Models",
                    "enable_texture": True,
                    "texture_resolution": 1536,
                    "params": {"texture_steps": 12},
                    "workflow_id": "wf-text-to-3d",
                },
            )
        self.assertIn("image-run", message)
        url, kwargs = client.payload
        self.assertTrue(url.endswith("/workflow-runs/from-image"))
        self.assertEqual(
            kwargs["data"],
            {
                "remesh": "quad",
                "collection": "Models",
                "enable_texture": "true",
                "texture_resolution": "1536",
                "model_id": "trellis2/generate",
                "workflow_id": "wf-text-to-3d",
                "params": '{"texture_steps": 12}',
            },
        )

    async def test_remove_background_dispatches_a_typed_image_workflow(self) -> None:
        client = _Client()
        with patch(
            "mcp_server._workspace_image_reference",
            return_value={"kind": "workspace_path", "path": "Workflows/hero.png"},
        ):
            message = await _dispatch(
                client,
                "polykit_remove_background",
                {"image_path": "/workspace/hero.png", "model": "isnet-anime"},
            )
        self.assertIn("image-run", message)
        url, kwargs = client.payload
        self.assertTrue(url.endswith("/workflow-runs/execute"))
        payload = kwargs["json"]
        self.assertEqual(payload["prompt"]["image"]["class_type"], "polykit.image")
        self.assertEqual(payload["prompt"]["cutout"]["class_type"], "image-background-remover/remove-background")
        self.assertEqual(payload["prompt"]["cutout"]["inputs"]["params"], {"model": "isnet-anime"})
        self.assertEqual(payload["prompt"]["output"]["inputs"]["image"], ["cutout", "image"])

    def test_workspace_image_reference_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch("mcp_server.runtime_paths", SimpleNamespace(workspace=root)):
                source = root / "hero.png"
                source.write_bytes(b"png")
                self.assertEqual(
                    _workspace_image_reference(str(source)),
                    {"kind": "workspace_path", "path": "hero.png"},
                )
                with self.assertRaises(ValueError):
                    _workspace_image_reference(str(root.parent / "outside.png"))


if __name__ == "__main__":
    unittest.main()
