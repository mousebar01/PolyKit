import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from routers.workspace_library import LibraryDeleteRequest, LibraryRenameRequest, delete_assets, rename_asset
from services.runtime_paths import runtime_paths


class LibraryManageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-manage-test-")
        self.workspace = Path(self._tmp.name)
        runtime_paths.update(workspace_dir=self.workspace)
        self.root = self.workspace / "Workflows"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def _write(self, name: str, content: bytes = b"x") -> Path:
        p = self.root / name
        p.write_bytes(content)
        return p

    async def test_delete_removes_file_and_sidecars(self) -> None:
        self._write("hero.glb")
        self._write("hero.landmarks.v1.json")
        self._write("hero.scene.json")
        result = await delete_assets(LibraryDeleteRequest(workspacePaths=["Workflows/hero.glb"]))
        self.assertEqual(result["deleted"], ["Workflows/hero.glb"])
        self.assertFalse((self.root / "hero.glb").exists())
        self.assertFalse((self.root / "hero.landmarks.v1.json").exists())
        self.assertFalse((self.root / "hero.scene.json").exists())

    async def test_delete_reports_missing_and_rejects_unsafe(self) -> None:
        result = await delete_assets(LibraryDeleteRequest(
            workspacePaths=["Workflows/nope.glb", "Exports/x.glb", "../evil.glb"],
        ))
        self.assertEqual(result["deleted"], [])
        self.assertIn("Workflows/nope.glb", result["missing"])
        self.assertIn("Exports/x.glb", result["rejected"])
        self.assertIn("../evil.glb", result["rejected"])

    async def test_delete_batch(self) -> None:
        self._write("a.glb")
        self._write("b.glb")
        result = await delete_assets(LibraryDeleteRequest(workspacePaths=["Workflows/a.glb", "Workflows/b.glb"]))
        self.assertEqual(len(result["deleted"]), 2)
        self.assertFalse((self.root / "a.glb").exists())
        self.assertFalse((self.root / "b.glb").exists())

    async def test_rename_preserves_extension_and_moves_sidecars(self) -> None:
        self._write("hero.glb")
        self._write("hero.landmarks.v1.json")
        result = await rename_asset(LibraryRenameRequest(workspacePath="Workflows/hero.glb", newName="villain"))
        self.assertEqual(result["workspacePath"], "Workflows/villain.glb")
        self.assertTrue((self.root / "villain.glb").exists())
        self.assertTrue((self.root / "villain.landmarks.v1.json").exists())

    async def test_rename_rejects_collision_and_unsafe(self) -> None:
        self._write("hero.glb")
        self._write("other.glb")
        with self.assertRaises(HTTPException) as ctx:
            await rename_asset(LibraryRenameRequest(workspacePath="Workflows/hero.glb", newName="other"))
        self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(HTTPException) as ctx:
            await rename_asset(LibraryRenameRequest(workspacePath="Workflows/hero.glb", newName="../x.glb"))
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
