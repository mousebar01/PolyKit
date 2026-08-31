import asyncio
import io
import json
import struct
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi import HTTPException
from unittest.mock import patch

import routers.model as model
import routers.node_packs as node_packs
import routers.settings as settings_router
import routers.workspace_library as workspace_library
from services.runtime_paths import runtime_paths
from services.runtime_settings import DownloadSourceConfig


def _write_glb(path: Path, document: dict) -> None:
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(encoded))
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
    )


class WebBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._paths = runtime_paths.snapshot()

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self._paths.models,
            workspace_dir=self._paths.workspace,
            workflows_dir=self._paths.workflows,
            node_packs_dir=self._paths.node_packs,
        )

    def test_workspace_library_lists_only_supported_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            (root / "Workflows").mkdir()
            (root / "Workflows" / "hero.glb").write_bytes(b"glb")
            (root / "Workflows" / "hero.png").write_bytes(b"png")
            (root / "Workflows" / "notes.csv").write_text("ignore", encoding="utf-8")
            (root / "Workflows" / ".hidden.glb").write_bytes(b"ignore")

            with patch("services.asset_previews.start_prewarm"):
                result = asyncio.run(workspace_library.list_library())

            self.assertTrue(result["success"])
            self.assertEqual([entry["workspacePath"] for entry in result["entries"]], ["Workflows/hero.glb", "Workflows/hero.png"])
            image_entry = result["entries"][1]
            self.assertEqual(image_entry["capability"], "image")
            self.assertEqual(image_entry["previewKind"], "image")
            self.assertTrue(image_entry["openable"])

    def test_workspace_library_rejects_traversal_and_reads_safe_mesh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            (root / "Workflows").mkdir()
            (root / "Workflows" / "hero.glb").write_bytes(b"glb")
            unsafe = asyncio.run(workspace_library.read_library(workspace_library.LibraryRequest(workspacePath="../hero.glb")))
            safe = asyncio.run(workspace_library.read_library(workspace_library.LibraryRequest(workspacePath="Workflows/hero.glb")))
            self.assertFalse(unsafe["success"])
            self.assertEqual(unsafe["error"]["code"], "unsafe-path")
            self.assertTrue(safe["success"])
            self.assertEqual(safe["preview"]["kind"], "3d-model")

    def test_workspace_library_opens_generated_world_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            workflows = root / "Workflows"
            workflows.mkdir()
            (workflows / "emberfall.world.json").write_text("{}", encoding="utf-8")
            result = asyncio.run(workspace_library.open_library(workspace_library.LibraryRequest(workspacePath="Workflows/emberfall.world.json")))
            self.assertTrue(result["success"])
            self.assertEqual(result["entry"]["capability"], "generated-world")
            self.assertTrue(result["entry"]["openable"])

    def test_workspace_library_rename_preserves_generated_world_suffix_and_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            workflows = root / "Workflows"
            workflows.mkdir()
            world_path = workflows / "emberfall.world.json"
            world_path.write_text(json.dumps({"id": "emberfall", "kind": "polykit.world"}), encoding="utf-8")
            result = asyncio.run(workspace_library.rename_asset(workspace_library.LibraryRenameRequest(workspacePath="Workflows/emberfall.world.json", newName="harbor")))
            self.assertTrue(result["success"])
            self.assertEqual(result["workspacePath"], "Workflows/harbor.world.json")
            self.assertFalse(world_path.exists())
            renamed = workflows / "harbor.world.json"
            self.assertTrue(renamed.exists())
            self.assertEqual(json.loads(renamed.read_text(encoding="utf-8"))["id"], "harbor")

    def test_workspace_library_reads_image_preview_without_mesh_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            (root / "Workflows").mkdir()
            (root / "Workflows" / "hero.png").write_bytes(b"png")
            result = asyncio.run(workspace_library.read_library(workspace_library.LibraryRequest(workspacePath="Workflows/hero.png")))
            self.assertTrue(result["success"])
            self.assertEqual(result["entry"]["capability"], "image")
            self.assertEqual(result["preview"], {"kind": "image", "imageUrl": "/workspace/Workflows/hero.png"})

    def test_workspace_library_exports_mixed_assets_as_original_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            workflows = root / "Workflows"
            workflows.mkdir()
            (workflows / "hero.png").write_bytes(b"png")
            (workflows / "hero.glb").write_bytes(b"glb")
            response = workspace_library.export_assets(workspace_library.LibraryExportRequest(workspacePaths=["Workflows/hero.png", "Workflows/hero.glb"]))
            self.assertEqual(response.media_type, "application/zip")
            self.assertIn("polykit-assets.zip", response.headers["content-disposition"])

            async def read_body() -> bytes:
                chunks = [chunk async for chunk in response.body_iterator]
                return b"".join(chunks)

            with zipfile.ZipFile(io.BytesIO(asyncio.run(read_body()))) as bundle:
                self.assertEqual(set(bundle.namelist()), {"hero.png", "hero.glb"})
                self.assertEqual(bundle.read("hero.png"), b"png")
                self.assertEqual(bundle.read("hero.glb"), b"glb")

    def test_workspace_library_classifies_rigged_glb_and_keeps_previews(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(workspace_dir=root)
            workflows = root / "Workflows"
            workflows.mkdir()
            _write_glb(
                workflows / "hero_rigged.glb",
                {
                    "asset": {"version": "2.0"},
                    "nodes": [{"name": "root"}],
                    "skins": [{"joints": [0]}],
                    "meshes": [{"primitives": [{"attributes": {"JOINTS_0": 0, "WEIGHTS_0": 1}}]}],
                },
            )
            with patch("services.asset_previews.start_prewarm"):
                result = asyncio.run(workspace_library.list_library())
            self.assertTrue(result["success"])
            entry = result["entries"][0]
            self.assertEqual(entry["capability"], "rigged-mesh")
            self.assertIn("thumbnail", entry)
            self.assertIn("preview", entry)

    def test_model_path_guard_rejects_traversal_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(models_dir=root)
            (root / "real").mkdir()
            (root / "alias").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaises(HTTPException):
                model._safe_model_path("../outside")
            with self.assertRaises(HTTPException):
                model._safe_model_path("alias/model")

    def test_model_downloaded_checks_server_files_without_loaded_generator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(models_dir=root)
            marker = root / "skintokens-rig" / "auto-rig" / "checkpoint.ckpt"
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b"weights")
            result = asyncio.run(model.model_downloaded("skintokens-rig/auto-rig", "checkpoint.ckpt"))
            self.assertEqual(result, {"model_id": "skintokens-rig/auto-rig", "downloaded": True})

    def test_model_delete_can_remove_weights_before_isolated_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(models_dir=root)
            model_dir = root / "skintokens-rig" / "auto-rig"
            model_dir.mkdir(parents=True)
            (model_dir / "partial.ckpt").write_bytes(b"partial")
            with patch.object(model.model_runtime_registry, "get_generator", side_effect=ValueError("missing venv")):
                result = asyncio.run(model.delete_model("skintokens-rig/auto-rig"))
            self.assertTrue(result["deleted"])
            self.assertFalse(model_dir.exists())

    def test_headless_setup_rejects_untrusted_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(node_packs_dir=root)
            pack = root / "third-party"
            pack.mkdir()
            (pack / "setup.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
            with patch.dict(os.environ, {"POLYKIT_HEADLESS": "1"}, clear=False):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(node_packs.setup_node_pack("third-party"))
            self.assertEqual(raised.exception.status_code, 403)

    def test_headless_setup_allows_official_pack_and_uses_manifest_python_min(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_paths.update(node_packs_dir=root)
            pack = root / "official"
            pack.mkdir()
            (pack / ".polykit-official").write_text("official\n", encoding="utf-8")
            (pack / "manifest.json").write_text(json.dumps({"id": "official", "python_min": "3.11"}), encoding="utf-8")
            (pack / "setup.py").write_text("from pathlib import Path\nPath('setup-ran').write_text('ok')\n", encoding="utf-8")
            with patch.dict(os.environ, {"POLYKIT_HEADLESS": "1"}, clear=False):
                result = asyncio.run(node_packs.setup_node_pack("official"))
            self.assertEqual(result["status"], "ok")
            self.assertTrue((pack / "setup-ran").is_file())
            self.assertGreaterEqual(node_packs._python_version(result["python_exe"]), (3, 11))

    def test_download_source_probe_urls_are_ecosystem_specific(self) -> None:
        sources = DownloadSourceConfig(
            huggingface_endpoint="https://hf.example/",
            pypi_index_url="https://pypi.example/simple/",
            pytorch_index_url="https://torch.example/whl/{tag}/",
        )
        self.assertEqual(settings_router._source_probe_url("huggingface", sources), "https://hf.example/api/models?limit=1")
        self.assertEqual(settings_router._source_probe_url("pypi", sources), "https://pypi.example/simple/fastapi/")
        self.assertEqual(settings_router._source_probe_url("pytorch", sources), "https://torch.example/whl/cu126/torch/")


if __name__ == "__main__":
    unittest.main()
