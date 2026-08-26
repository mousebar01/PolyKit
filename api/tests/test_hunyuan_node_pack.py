from __future__ import annotations

import importlib.util
import json
import py_compile
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile
import trimesh

from routers.node_packs import _source_pack_payload
from services.official_packs import _sync_model_pack


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPO_ROOT / "node-packs" / "hunyuan3d-part"


def _load_setup_module():
    spec = importlib.util.spec_from_file_location(
        "polykit_hunyuan_setup_contract",
        PACK_ROOT / "setup.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HunyuanNodePackManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((PACK_ROOT / "manifest.json").read_text(encoding="utf-8"))

    def test_pack_is_official_isolated_mesh_model(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["id"], "hunyuan3d-part")
        self.assertEqual(manifest["type"], "model")
        self.assertEqual(manifest["env"], "isolated")
        self.assertTrue(manifest["builtin"])
        self.assertTrue(manifest["trusted"])
        self.assertEqual(manifest["generator_class"], "Hunyuan3DPartGenerator")

        node = manifest["nodes"][0]
        self.assertEqual(node["id"], "decompose-mesh")
        self.assertEqual(node["input"], "mesh")
        self.assertEqual(node["inputs"], ["mesh"])
        self.assertEqual(node["output"], "mesh")

    def test_p3sam_download_is_exact_safetensors_resource(self) -> None:
        manifest = self.manifest
        node = manifest["nodes"][0]
        resource = manifest["models"][0]

        self.assertEqual(resource["id"], "p3-sam")
        self.assertEqual(resource["kind"], "huggingface")
        self.assertEqual(resource["repo"], "tencent/Hunyuan3D-Part")
        self.assertEqual(resource["location"], "hunyuan3d-part/decompose-mesh")
        self.assertEqual(resource["check"], "p3sam/p3sam.safetensors")
        self.assertEqual(resource["include_prefixes"], ["p3sam/p3sam.safetensors"])
        self.assertEqual(resource["required_for"], ["decompose-mesh"])

        # Keep legacy node-level downloader compatibility until every pack uses
        # models[]. Both declarations must point to the exact same artifact.
        self.assertEqual(node["hf_repo"], resource["repo"])
        self.assertEqual(node["download_check"], resource["check"])
        self.assertEqual(node["hf_include_prefixes"], resource["include_prefixes"])
        self.assertNotIn("p3sam.ckpt", json.dumps(manifest))

    def test_web_build_keeps_official_model_packs_in_server_source(self) -> None:
        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["scripts"]["build"], "npm run web:build")
        self.assertTrue((REPO_ROOT / "node-packs" / "trellis2" / "manifest.json").is_file())
        self.assertTrue((REPO_ROOT / "node-packs" / "hunyuan3d-part" / "manifest.json").is_file())

    def test_manifest_cannot_self_grant_official_trust(self) -> None:
        source = {
            "id": "third-party",
            "name": "Third Party",
            "type": "model",
            "builtin": True,
            "trusted": True,
            "nodes": [],
        }
        untrusted = _source_pack_payload("third-party", source, official=False)
        official = _source_pack_payload("third-party", source, official=True)

        self.assertFalse(untrusted["builtin"])
        self.assertFalse(untrusted["trusted"])
        self.assertTrue(official["builtin"])
        self.assertTrue(official["trusted"])

    def test_part_separation_param_is_exposed_as_float(self) -> None:
        node = self.manifest["nodes"][0]
        separation = next(
            param for param in node["params_schema"] if param["id"] == "part_separation"
        )
        self.assertEqual(separation["type"], "float")
        self.assertEqual(separation["default"], 0)
        self.assertEqual(separation["min"], 0)
        self.assertEqual(separation["max"], 2)
        self.assertEqual(separation["step"], 0.05)
        zh = separation.get("i18n", {}).get("zh-CN", {}).get("label")
        self.assertEqual(zh, "分离程度")


class HunyuanPartSeparationTests(unittest.TestCase):
    """Unit tests for the PolyKit-owned part separation post-process."""

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "polykit_hunyuan3d_part_separation",
            PACK_ROOT / "separation.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.apply = staticmethod(module.apply_part_separation)

    def _two_part_scene_glb(self) -> Path:
        box = trimesh.creation.box((1.0, 1.0, 1.0))
        left = box.copy().apply_translation([-2.0, 0.0, 0.0])
        right = box.copy().apply_translation([2.0, 0.0, 0.0])
        scene = trimesh.Scene({"left": left, "right": right})
        path = Path(self._temp_dir.name) / "parts.glb"
        scene.export(str(path))
        return path

    def setUp(self) -> None:
        self._temp_dir = TemporaryDirectory()

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_spreads_parts_outward_from_center(self) -> None:
        path = self._two_part_scene_glb()
        result = self.apply(path, 1.0)
        self.assertEqual(Path(result), path)
        scene = trimesh.load(str(path))
        centers = {k: g.bounds.mean(axis=0) for k, g in scene.geometry.items()}
        # model center is the origin; each part doubles its distance from it
        self.assertAlmostEqual(centers["left"][0], -4.0, places=4)
        self.assertAlmostEqual(centers["right"][0], 4.0, places=4)

    def test_zero_separation_leaves_file_untouched(self) -> None:
        path = self._two_part_scene_glb()
        before = path.read_bytes()
        result = self.apply(path, 0)
        self.assertEqual(Path(result), path)
        self.assertEqual(path.read_bytes(), before)

    def test_negative_separation_is_a_noop(self) -> None:
        path = self._two_part_scene_glb()
        before = path.read_bytes()
        result = self.apply(path, -0.5)
        self.assertEqual(Path(result), path)
        self.assertEqual(path.read_bytes(), before)

    def test_single_geometry_is_not_spread(self) -> None:
        box = trimesh.creation.box((1.0, 1.0, 1.0))
        path = Path(self._temp_dir.name) / "single.glb"
        trimesh.Scene({"only": box}).export(str(path))
        before = path.read_bytes()
        result = self.apply(path, 1.0)
        self.assertEqual(Path(result), path)
        self.assertEqual(path.read_bytes(), before)

    def test_single_geometry_is_split_using_face_ids_sidecar(self) -> None:
        import json

        # a 2x1x1 box: left half (6 faces) + right half (6 faces), one GLB geometry
        parts = [trimesh.creation.box((1.0, 1.0, 1.0)).apply_translation([-0.5, 0.0, 0.0]),
                 trimesh.creation.box((1.0, 1.0, 1.0)).apply_translation([0.5, 0.0, 0.0])]
        merged = trimesh.util.concatenate(parts)
        merged = trimesh.Trimesh(vertices=merged.vertices, faces=merged.faces, process=False)
        geom_path = Path(self._temp_dir.name) / "merged.glb"
        trimesh.Scene({"only": merged}).export(str(geom_path))
        # split faces by half along x: label 0 for x<0, label 1 for x>=0
        mid = merged.vertices[:, 0].mean()
        face_mid = merged.vertices[merged.faces].mean(axis=1)[:, 0]
        labels = [0 if x < mid else 1 for x in face_mid]
        with (geom_path.parent / "segmentation.json").open("w", encoding="utf-8") as fh:
            json.dump({"face_ids": labels}, fh)
        self.assertEqual(len(set(labels)), 2)

        result = self.apply(geom_path, 1.0)
        scene = trimesh.load(str(geom_path))
        self.assertEqual(len(scene.geometry), 2)
        centers = {k: g.bounds.mean(axis=0) for k, g in scene.geometry.items()}
        values = [c[0] for c in centers.values()]
        self.assertAlmostEqual(min(values), -1.0, places=4)   # left half at -0.5 -> 2x
        self.assertAlmostEqual(max(values), 1.0, places=4)     # right half at +0.5 -> 2x

    def test_single_geometry_with_sidecar_becomes_multipart_at_zero(self) -> None:
        import json

        left = trimesh.creation.box((1.0, 1.0, 1.0)).apply_translation([-0.5, 0.0, 0.0])
        right = trimesh.creation.box((1.0, 1.0, 1.0)).apply_translation([0.5, 0.0, 0.0])
        merged = trimesh.Trimesh(
            vertices=trimesh.util.concatenate([left, right]).vertices,
            faces=trimesh.util.concatenate([left, right]).faces,
            process=False,
        )
        geom_path = Path(self._temp_dir.name) / "merged2.glb"
        trimesh.Scene({"only": merged}).export(str(geom_path))
        mid = merged.vertices[:, 0].mean()
        face_mid = merged.vertices[merged.faces].mean(axis=1)[:, 0]
        with (geom_path.parent / "segmentation.json").open("w", encoding="utf-8") as fh:
            json.dump({"face_ids": [0 if x < mid else 1 for x in face_mid]}, fh)

        self.apply(geom_path, 0)  # no spread requested
        scene = trimesh.load(str(geom_path))
        self.assertEqual(len(scene.geometry), 2)             # still becomes multipart
        centers = [g.bounds.mean(axis=0)[0] for g in scene.geometry.values()]
        self.assertAlmostEqual(min(centers), -0.5, places=4)  # assembled original pose
        self.assertAlmostEqual(max(centers), 0.5, places=4)


class HunyuanOfficialSyncTests(unittest.TestCase):
    def test_sync_refreshes_code_without_deleting_managed_runtime(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            src = root / "source"
            dst = root / "runtime"
            src.mkdir()
            dst.mkdir()

            (src / "manifest.json").write_text('{"id":"hunyuan3d-part"}\n', encoding="utf-8")
            (src / "generator.py").write_text("VERSION = 2\n", encoding="utf-8")
            (src / "separation.py").write_text("VERSION = 2\n", encoding="utf-8")
            (src / "setup.py").write_text("VERSION = 2\n", encoding="utf-8")
            (dst / "manifest.json").write_text('{"id":"old"}\n', encoding="utf-8")
            (dst / "generator.py").write_text("VERSION = 1\n", encoding="utf-8")

            provider = dst / "provider"
            venv = dst / "venv"
            upstream = dst / ".upstream"
            cache = dst / ".cache"
            for runtime_dir in (provider, venv, upstream, cache):
                runtime_dir.mkdir()
                (runtime_dir / "keep.txt").write_text("keep\n", encoding="utf-8")

            changed = _sync_model_pack(src, dst)

            self.assertTrue(changed)
            self.assertEqual((dst / "generator.py").read_text(encoding="utf-8"), "VERSION = 2\n")
            self.assertEqual((dst / "separation.py").read_text(encoding="utf-8"), "VERSION = 2\n")
            for runtime_dir in (provider, venv, upstream, cache):
                self.assertEqual((runtime_dir / "keep.txt").read_text(encoding="utf-8"), "keep\n")


class HunyuanNodePackSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.setup = _load_setup_module()

    def test_provider_revision_is_pinned(self) -> None:
        revision = self.setup.PROVIDER_REVISION
        self.assertRegex(revision, re.compile(r"^[0-9a-f]{40}$"))
        self.assertEqual(revision, "48b9ee3540bf7a85bcb7eb982f748d0fe14195a8")
        self.assertIn(revision, self.setup.PROVIDER_ZIP)
        wrapper = (PACK_ROOT / "generator.py").read_text(encoding="utf-8")
        self.assertIn(f'PROVIDER_REVISION = "{revision}"', wrapper)

    def test_safe_extract_rejects_zip_path_traversal(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("provider/ok.txt", "ok")
                zf.writestr("provider/../../escape.txt", "no")

            with self.assertRaisesRegex(RuntimeError, "Unsafe provider archive member"):
                self.setup._safe_extract(archive, root / "extract")
            self.assertFalse((root / "escape.txt").exists())

    def test_safe_extract_accepts_single_root_archive(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "provider.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("provider-rev/generator.py", "VALUE = 1\n")
                zf.writestr("provider-rev/setup.py", "VALUE = 2\n")

            extracted = self.setup._safe_extract(archive, root / "extract")
            self.assertEqual(extracted.name, "provider-rev")
            self.assertTrue((extracted / "generator.py").is_file())
            self.assertTrue((extracted / "setup.py").is_file())

    def test_compat_patches_match_pinned_provider_setup(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            provider = root / "provider"
            provider.mkdir()
            candidates = [
                Path("/tmp/hunyuan-provider-src/setup.py"),
                Path.home() / ".polykit" / "node-packs" / "hunyuan3d-part" / "provider" / "setup.py",
            ]
            source = next((path for path in candidates if path.is_file()), None)
            if source is None:
                self.skipTest("pinned provider setup.py is not available")
            raw = source.read_text(encoding="utf-8")
            if self.setup.PROVIDER_COMPAT_SENTINEL in raw:
                raw = raw.split("\n", 1)[1]
            (provider / "setup.py").write_text(raw, encoding="utf-8")
            (provider / "generator.py").write_text("VALUE = 1\n", encoding="utf-8")
            (provider / ".polykit-provider-revision").write_text(
                self.setup.PROVIDER_REVISION + "\n",
                encoding="utf-8",
            )

            original_root = self.setup.PACK_ROOT
            original_provider = self.setup.PROVIDER_ROOT
            try:
                self.setup.PACK_ROOT = root
                self.setup.PROVIDER_ROOT = provider
                self.setup._apply_provider_compat_patches()
            finally:
                self.setup.PACK_ROOT = original_root
                self.setup.PROVIDER_ROOT = original_provider

            patched = (provider / "setup.py").read_text(encoding="utf-8")
            self.assertIn(self.setup.PROVIDER_COMPAT_SENTINEL, patched)
            self.assertIn("def _extract_json_object", patched)
            self.assertIn("def _linux_amd64_native_install_steps", patched)
            self.assertIn("linux-amd64-prebuilt-wheels", patched)
            self.assertIn("git_version >= (2, 27)", patched)
            self.assertIn("native_import_results: dict[str, bool] | None = None", patched)
            self.assertIn("required_dependencies[module] = bool(present)", patched)
            return patched

    def test_compat_patches_converge_from_partially_patched_provider(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            provider = root / "provider"
            provider.mkdir()
            candidates = [
                Path("/tmp/hunyuan-provider-src/setup.py"),
                Path.home() / ".polykit" / "node-packs" / "hunyuan3d-part" / "provider" / "setup.py",
            ]
            source = next((path for path in candidates if path.is_file()), None)
            if source is None:
                self.skipTest("pinned provider setup.py is not available")
            raw = source.read_text(encoding="utf-8")
            if self.setup.PROVIDER_COMPAT_SENTINEL in raw:
                raw = raw.split("\n", 1)[1]
            (provider / "setup.py").write_text(raw, encoding="utf-8")
            (provider / ".polykit-provider-revision").write_text(
                self.setup.PROVIDER_REVISION + "\n",
                encoding="utf-8",
            )

            original_root = self.setup.PACK_ROOT
            original_provider = self.setup.PROVIDER_ROOT
            try:
                self.setup.PACK_ROOT = root
                self.setup.PROVIDER_ROOT = provider
                self.setup._apply_provider_compat_patches()
                full = (provider / "setup.py").read_text(encoding="utf-8")
                self.assertTrue(full.startswith(self.setup.PROVIDER_COMPAT_SENTINEL))

                # Simulate an older patch run: a legacy marker line, current
                # sentinel missing, and the newest hunk reverted. Re-applying
                # must converge byte-for-byte.
                sentinel_line = self.setup.PROVIDER_COMPAT_SENTINEL + "\n"
                partial = full[len(sentinel_line):]
                old_last, new_last = self.setup._provider_compat_replacements()[-1]
                self.assertIn(new_last, partial)
                partial = partial.replace(new_last, old_last, 1)
                partial = "# polykit-compat-patches: git-2.25, json-stdout\n" + partial
                (provider / "setup.py").write_text(partial, encoding="utf-8")

                self.setup._apply_provider_compat_patches()
                converged = (provider / "setup.py").read_text(encoding="utf-8")
            finally:
                self.setup.PACK_ROOT = original_root
                self.setup.PROVIDER_ROOT = original_provider

            self.assertEqual(converged, full)
            py_compile.compile(str(provider / "setup.py"), doraise=True)


if __name__ == "__main__":
    unittest.main()
