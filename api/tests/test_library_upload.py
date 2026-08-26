import io
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from routers.workspace_library import upload_asset
from services.runtime_paths import runtime_paths

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _upload_file(data: bytes, name: str = "photo.png", content_type: str = "image/png") -> UploadFile:
    return UploadFile(
        filename=name,
        file=io.BytesIO(data),
        headers=Headers({"content-type": content_type}),
    )


class UploadAssetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-upload-test-")
        self.workspace = Path(self._tmp.name)
        runtime_paths.update(workspace_dir=self.workspace)

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    async def test_uploads_image_into_workspace_and_returns_path(self) -> None:
        result = await upload_asset(file=_upload_file(_PNG), collection="Workflows")
        self.assertTrue(result["success"])
        path = result["workspacePath"]
        self.assertTrue(path.startswith("Workflows/"), path)
        self.assertTrue((self.workspace / path).is_file())
        self.assertEqual(result["url"], f"/workspace/{path}")

    async def test_respects_collection(self) -> None:
        result = await upload_asset(file=_upload_file(_PNG), collection="Archive")
        self.assertTrue(result["workspacePath"].startswith("Archive/"))

    async def test_rejects_non_image_or_mesh(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await upload_asset(file=_upload_file(b"nothing", name="notes.txt", content_type="text/plain"))
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_uploads_mesh_into_workspace(self) -> None:
        glb = b"glTF-binary" + b"\x00" * 32
        result = await upload_asset(
            file=_upload_file(glb, name="model.glb", content_type="model/gltf-binary"),
            collection="Workflows",
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["workspacePath"].endswith(".glb"), result["workspacePath"])
        self.assertTrue((self.workspace / result["workspacePath"]).is_file())

    async def test_rejects_empty_image(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await upload_asset(file=_upload_file(b""))
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
