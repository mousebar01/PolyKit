from __future__ import annotations

import importlib.util
import json
import py_compile
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPO_ROOT / "node-packs" / "skintokens-rig"


def _load_setup_module():
    spec = importlib.util.spec_from_file_location(
        "polykit_skintokens_setup_contract", PACK_ROOT / "setup.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_generator_module():
    spec = importlib.util.spec_from_file_location(
        "polykit_skintokens_generator_contract", PACK_ROOT / "generator.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkinTokensNodePackManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((PACK_ROOT / "manifest.json").read_text(encoding="utf-8"))

    def test_pack_is_official_isolated_mesh_model(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["id"], "skintokens-rig")
        self.assertEqual(manifest["type"], "model")
        self.assertEqual(manifest["env"], "isolated")
        self.assertTrue(manifest["builtin"])
        self.assertTrue(manifest["trusted"])
        self.assertEqual(manifest["generator_class"], "SkinTokensRigGenerator")
        self.assertEqual(manifest["output_tag"], "rigged")
        self.assertEqual(manifest["output_capability"], "rigged-mesh")
        self.assertEqual(manifest["input_formats"], ["glb"])
        self.assertEqual(manifest["python_min"], "3.11")

        node = manifest["nodes"][0]
        self.assertEqual(node["id"], "auto-rig")
        self.assertEqual(node["input"], "mesh")
        self.assertEqual(node["inputs"], ["mesh"])
        self.assertEqual(node["output"], "mesh")

    def test_manifest_declares_all_runtime_resources(self) -> None:
        manifest = self.manifest
        resource = manifest["models"][0]
        prefixes = resource["include_prefixes"]
        self.assertEqual(resource["repo"], "VAST-AI/SkinTokens")
        self.assertEqual(resource["required_for"], ["auto-rig"])
        self.assertIn("experiments/skin_vae_2_10_32768/", prefixes)
        self.assertIn("experiments/articulation_xl_quantization_256_token_4/", prefixes)
        self.assertNotIn("models/Qwen3-0.6B/", prefixes)
        self.assertIn("Qwen3 metadata", resource["note"])

        node = manifest["nodes"][0]
        self.assertEqual(node["hf_repo"], resource["repo"])
        self.assertEqual(node["download_check"], resource["check"])
        self.assertEqual(node["hf_include_prefixes"], prefixes)

    def test_rig_params_have_safe_defaults(self) -> None:
        params = {param["id"]: param for param in self.manifest["nodes"][0]["params_schema"]}
        self.assertTrue(params["use_transfer"]["default"])
        self.assertFalse(params["use_postprocess"]["default"])
        self.assertEqual(params["top_k"]["type"], "int")
        self.assertEqual(params["top_p"]["type"], "float")
        self.assertEqual(params["use_transfer"]["type"], "boolean")

    def test_setup_does_not_require_flash_attn(self) -> None:
        setup_source = (PACK_ROOT / "setup.py").read_text(encoding="utf-8")
        self.assertNotIn('"flash-attn"', setup_source)
        self.assertIn("SDPA", setup_source)

    def test_setup_disables_blender_generated_normals(self) -> None:
        setup_source = (PACK_ROOT / "setup.py").read_text(encoding="utf-8")
        self.assertIn('provider / "src/rig_package/parser/bpy.py"', setup_source)
        self.assertIn("export_normals=False", setup_source)
        self.assertIn("export_tangents=False", setup_source)

    def test_pack_python_files_compile(self) -> None:
        py_compile.compile(str(PACK_ROOT / "generator.py"), doraise=True)
        py_compile.compile(str(PACK_ROOT / "setup.py"), doraise=True)

    def test_generator_enforces_glb_and_validates_skin_output(self) -> None:
        source = (PACK_ROOT / "generator.py").read_text(encoding="utf-8")
        self.assertIn('input_path.suffix.lower() != ".glb"', source)
        self.assertIn("has_skin_metadata(output_path)", source)
        self.assertIn("JOINTS_0", source)
        self.assertIn("WEIGHTS_0", source)


class SkinTokensSetupPatchTests(unittest.TestCase):
    def test_replace_once_is_idempotent(self) -> None:
        setup = _load_setup_module()
        path = PACK_ROOT / "manifest.json"
        # Exercise the helper with a temporary file rather than touching the
        # checked-in provider or downloading the third-party source.
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            target = Path(directory) / "sample.py"
            target.write_text("old\n", encoding="utf-8")
            self.assertTrue(setup._replace_once(target, "old", "new"))
            self.assertFalse(setup._replace_once(target, "old", "new"))
            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")


class SkinTokensRuntimeContractTests(unittest.TestCase):
    def test_qwen_metadata_download_skips_large_model_weights(self) -> None:
        generator = _load_generator_module()
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory) / "models"
            calls = []

            def snapshot_download(**kwargs):
                calls.append(kwargs)
                target = Path(kwargs["local_dir"])
                target.mkdir(parents=True, exist_ok=True)
                (target / "config.json").write_text("{}", encoding="utf-8")
                return str(target)

            fake_hub = SimpleNamespace(snapshot_download=snapshot_download)
            adapter = generator.SkinTokensRigGenerator(model_dir, Path(directory) / "out")
            with patch.dict(sys.modules, {"huggingface_hub": fake_hub}):
                adapter._ensure_qwen_metadata()

            self.assertEqual(calls[0]["repo_id"], "Qwen/Qwen3-0.6B")
            self.assertIn("config.json", calls[0]["allow_patterns"])
            self.assertIn("*.safetensors", calls[0]["ignore_patterns"])

    def test_cuda_capacity_rejects_missing_gpu(self) -> None:
        generator = _load_generator_module()
        fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        with patch.dict(sys.modules, {"torch": fake_torch}):
            with self.assertRaisesRegex(RuntimeError, "requires an NVIDIA CUDA GPU"):
                generator._check_cuda_capacity()

    def test_cuda_capacity_rejects_low_free_vram(self) -> None:
        generator = _load_generator_module()
        gib = 1024 * 1024 * 1024
        fake_cuda = SimpleNamespace(
            is_available=lambda: True,
            mem_get_info=lambda: (13 * gib, 24 * gib),
        )
        fake_torch = SimpleNamespace(cuda=fake_cuda)
        with patch.dict(sys.modules, {"torch": fake_torch}):
            with self.assertRaisesRegex(RuntimeError, "14 GB of free VRAM"):
                generator._check_cuda_capacity()

    def test_generate_rejects_non_glb_before_loading_runtime(self) -> None:
        generator = _load_generator_module()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.obj"
            source.write_text("o source\n", encoding="utf-8")
            adapter = generator.SkinTokensRigGenerator(Path(directory) / "models", Path(directory) / "out")
            with self.assertRaisesRegex(ValueError, "requires a GLB"):
                adapter.generate(source)


if __name__ == "__main__":
    unittest.main()
