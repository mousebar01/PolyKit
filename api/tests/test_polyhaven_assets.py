from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trimesh

from services import polyhaven_assets as module
from services.runtime_paths import runtime_paths


class PolyHavenAssetTests(unittest.TestCase):
    def test_search_is_scored_and_returns_provider_provenance(self) -> None:
        index = {
            "Barrel_01": {
                "name": "Barrel 01",
                "tags": ["barrel", "industrial"],
                "categories": ["containers"],
                "category": "Containers/Barrels",
                "description": "A red metal oil barrel",
                "type": 2,
                "thumbnail_url": "https://cdn.polyhaven.com/thumb.png",
            },
            "Tree_01": {"name": "Tree", "tags": ["plant"], "type": 2},
        }
        with patch.object(module, "_load_model_index", return_value=index):
            matches = module.search_models("red barrel", category="containers", limit=5)
        self.assertEqual([item["asset_id"] for item in matches], ["Barrel_01"])
        self.assertEqual(matches[0]["provider"], "polyhaven")
        self.assertEqual(matches[0]["license"], "CC0")
        self.assertEqual(matches[0]["source_url"], "https://polyhaven.com/a/Barrel_01")

    def test_manifest_rejects_traversal_and_remote_urls(self) -> None:
        with self.assertRaises(module.PolyHavenError):
            module._collect_file_specs({"../escape.bin": {"url": "https://dl.polyhaven.org/a", "size": 1}})
        with self.assertRaises(module.PolyHavenError):
            module._collect_file_specs({"safe.bin": {"url": "https://example.com/a", "size": 1}})

    def test_import_downloads_bundle_normalizes_to_glb_and_writes_sidecar(self) -> None:
        exported = trimesh.creation.box().export(file_type="gltf")
        assert isinstance(exported, dict)
        root_bytes = exported["model.gltf"]
        files_bytes = {name: value for name, value in exported.items()}
        def digest(name: str) -> str:
            return hashlib.md5(files_bytes[name]).hexdigest()

        manifest = {
            "gltf": {
                "1k": {
                    "gltf": {
                        "url": "https://dl.polyhaven.org/model.gltf",
                        "size": len(root_bytes),
                        "md5": digest("model.gltf"),
                        "include": {
                            name: {
                                "url": f"https://dl.polyhaven.org/{name}",
                                "size": len(value),
                                "md5": digest(name),
                            }
                            for name, value in files_bytes.items()
                            if name != "model.gltf"
                        },
                    }
                }
            }
        }
        original = runtime_paths.snapshot()
        with tempfile.TemporaryDirectory(prefix="polykit-polyhaven-test-") as temp_dir:
            workspace = Path(temp_dir)
            runtime_paths.update(workspace_dir=workspace)
            def fake_download(spec: module._FileSpec, destination: Path) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(files_bytes[spec.relative_path])

            with patch.object(module, "_request_json", side_effect=[
                {"type": 2, "name": "Test Box", "files_hash": "files-v1", "tags": ["box"], "description": "A test box", "category": "Props", "thumbnail_url": "https://cdn.polyhaven.com/test.png"},
                manifest,
            ]), patch.object(module, "_download_file", side_effect=fake_download):
                result = module.import_model("Test_Box", resolution="1k", workspace=workspace)

            output = workspace / result["workspace_path"]
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            self.assertEqual(result["format"], "glb")
            sidecar = output.with_name(f"{output.stem}.asset.json")
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(metadata["provider"], "polyhaven")
            self.assertEqual(metadata["license"], "CC0")
            self.assertEqual(metadata["asset_id"], "Test_Box")
            self.assertTrue((workspace / "Workflows" / ".external" / "polyhaven").is_dir())
        runtime_paths.update(
            models_dir=original.models,
            workspace_dir=original.workspace,
            workflows_dir=original.workflows,
            node_packs_dir=original.node_packs,
        )

    def test_reimport_reuses_sidecar_and_keeps_unique_output_name(self) -> None:
        exported = trimesh.creation.box().export(file_type="gltf")
        assert isinstance(exported, dict)
        files_bytes = {name: value for name, value in exported.items()}
        root_bytes = files_bytes["model.gltf"]
        manifest = {
            "gltf": {
                "1k": {
                    "gltf": {
                        "url": "https://dl.polyhaven.org/model.gltf",
                        "size": len(root_bytes),
                        "md5": hashlib.md5(root_bytes).hexdigest(),
                        "include": {
                            name: {
                                "url": f"https://dl.polyhaven.org/{name}",
                                "size": len(value),
                                "md5": hashlib.md5(value).hexdigest(),
                            }
                            for name, value in files_bytes.items()
                            if name != "model.gltf"
                        },
                    }
                }
            }
        }
        original = runtime_paths.snapshot()
        with tempfile.TemporaryDirectory(prefix="polykit-polyhaven-reimport-") as temp_dir:
            workspace = Path(temp_dir)
            runtime_paths.update(workspace_dir=workspace)

            def fake_download(spec: module._FileSpec, destination: Path) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(files_bytes[spec.relative_path])

            info = {
                "type": 2,
                "name": "Test Box",
                "files_hash": "files-v1",
                "tags": [],
            }
            with patch.object(module, "_request_json", side_effect=[info, manifest, info, manifest]), patch.object(
                module, "_download_file", side_effect=fake_download
            ):
                first = module.import_model("Test_Box", resolution="1k", workspace=workspace)
                second = module.import_model("Test_Box", resolution="1k", workspace=workspace)

            self.assertNotEqual(first["workspace_path"], "Workflows/PolyHaven/Test_Box_1k.glb")
            self.assertTrue(first["workspace_path"].endswith("_1k.glb"))
            self.assertEqual(second["workspace_path"], first["workspace_path"])
            self.assertTrue(second["reused"])
        runtime_paths.update(
            models_dir=original.models,
            workspace_dir=original.workspace,
            workflows_dir=original.workflows,
            node_packs_dir=original.node_packs,
        )

    def test_gltf_data_uri_is_allowed_without_external_files(self) -> None:
        payload = base64.b64encode(b"not-needed").decode("ascii")
        root = Path(tempfile.mkdtemp()) / "model.gltf"
        root.write_text(json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": f"data:application/octet-stream;base64,{payload}"}]}), encoding="utf-8")
        module._validate_gltf_bundle(root)


if __name__ == "__main__":
    unittest.main()
