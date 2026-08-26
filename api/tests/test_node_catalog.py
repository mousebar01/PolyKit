import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import node_catalog
from services.runtime_paths import runtime_paths


class RuntimePathTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.original_paths = runtime_paths.snapshot()

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original_paths.models,
            workspace_dir=self.original_paths.workspace,
            workflows_dir=self.original_paths.workflows,
            node_packs_dir=self.original_paths.node_packs,
        )

    def use_node_pack_root(self, root: Path) -> None:
        runtime_paths.update(node_packs_dir=root)


class BuiltinNodeTests(unittest.TestCase):
    def test_builtin_definitions_cover_sources_and_sinks(self) -> None:
        definitions = node_catalog._builtin_definitions()
        by_type = {d.class_type: d for d in definitions}
        self.assertEqual(
            set(by_type),
            {"polykit.image", "polykit.text", "polykit.mesh", "polykit.output", "polykit.preview"},
        )
        self.assertEqual(by_type["polykit.image"].outputs, ["image"])
        self.assertEqual(by_type["polykit.mesh"].outputs, ["mesh"])
        self.assertEqual(by_type["polykit.output"].inputs, ["mesh"])
        self.assertTrue(all(d.category == "builtin" for d in definitions))


class RuntimeNodeTests(unittest.TestCase):
    def test_fake_executor_is_explicit_runtime_node(self) -> None:
        with patch.dict(os.environ, {"POLYKIT_EXECUTOR": "fake"}):
            definitions = node_catalog._runtime_definitions()
        self.assertEqual(len(definitions), 1)
        fake = definitions[0]
        self.assertEqual(fake.class_type, "fake")
        self.assertEqual(fake.category, "model")
        self.assertEqual(fake.inputs, ["image"])
        self.assertEqual(fake.outputs, ["mesh"])
        self.assertTrue(fake.builtin)

    def test_normal_executor_has_no_runtime_only_nodes(self) -> None:
        with patch.dict(os.environ, {"POLYKIT_EXECUTOR": "cuda"}):
            self.assertEqual(node_catalog._runtime_definitions(), [])


class ModelNodeTests(RuntimePathTestCase):
    def _write_model_pack(self, root: Path) -> Path:
        pack_dir = root / "trellis2"
        pack_dir.mkdir(parents=True)
        (pack_dir / "manifest.json").write_text(
            json.dumps({
                "id": "trellis2",
                "name": "TRELLIS.2",
                "type": "model",
                "generator_class": "TrellisGenerator",
                "description": "Generate and refine meshes.",
                "nodes": [
                    {
                        "id": "generate",
                        "name": "Generate Mesh",
                        "input": "image",
                        "output": "mesh",
                        "params_schema": [{"id": "remesh", "type": "select"}],
                    },
                    {
                        "id": "refine",
                        "name": "Refine Mesh",
                        "inputs": ["image", "mesh"],
                        "output": "mesh",
                    },
                ],
            }),
            encoding="utf-8",
        )
        return pack_dir

    def test_model_definitions_come_from_manifest_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_model_pack(root)
            self.use_node_pack_root(root)
            definitions = node_catalog._model_definitions()

        by_type = {d.class_type: d for d in definitions}
        self.assertEqual(set(by_type), {"trellis2/generate", "trellis2/refine"})
        generate = by_type["trellis2/generate"]
        self.assertEqual(generate.category, "model")
        self.assertEqual(generate.inputs, ["image"])
        self.assertEqual(generate.outputs, ["mesh"])
        self.assertEqual(generate.pack_id, "trellis2")
        self.assertEqual(len(generate.params_schema), 1)
        self.assertEqual(by_type["trellis2/refine"].inputs, ["image", "mesh"])

    def test_catalog_does_not_require_model_runtime_instantiation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_model_pack(root)
            self.use_node_pack_root(root)
            definition = node_catalog.get_node_definition("trellis2/generate")
        self.assertIsNotNone(definition)
        self.assertEqual(definition.class_type, "trellis2/generate")  # type: ignore[union-attr]


class ProcessNodeTests(RuntimePathTestCase):
    def _write_process_extension(self, root: Path, pack_id: str = "smoke-proc") -> Path:
        pack_dir = root / pack_id
        pack_dir.mkdir(parents=True)
        (pack_dir / "manifest.json").write_text(
            json.dumps({
                "id": pack_id,
                "name": "Smoke Process",
                "type": "process",
                "entry": "processor.py",
                "description": "Copies a mesh.",
                "nodes": [
                    {
                        "id": "copy",
                        "name": "Copy Mesh",
                        "input": "mesh",
                        "output": "mesh",
                        "params_schema": [{"id": "mode", "type": "select"}],
                    },
                ],
            }),
            encoding="utf-8",
        )
        (pack_dir / "processor.py").write_text("# processor", encoding="utf-8")
        return pack_dir

    def test_process_definitions_scan_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_process_extension(root)
            self.use_node_pack_root(root)
            definitions = node_catalog._process_definitions()

        self.assertEqual(len(definitions), 1)
        node = definitions[0]
        self.assertEqual(node.class_type, "smoke-proc/copy")
        self.assertEqual(node.category, "process")
        self.assertEqual(node.inputs, ["mesh"])
        self.assertEqual(node.outputs, ["mesh"])
        self.assertEqual(node.entry, "processor.py")
        self.assertEqual(len(node.params_schema), 1)

    def test_process_node_pack_resolves_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pack_dir = self._write_process_extension(root)
            self.use_node_pack_root(root)
            resolved = node_catalog.process_node_pack("smoke-proc/copy")

        self.assertIsNotNone(resolved)
        got_pack_dir, manifest, node = resolved  # type: ignore[misc]
        self.assertEqual(got_pack_dir, pack_dir)
        self.assertEqual(manifest["type"], "process")
        self.assertEqual(node["id"], "copy")

    def test_process_node_pack_rejects_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_process_extension(root)
            self.use_node_pack_root(root)
            self.assertIsNone(node_catalog.process_node_pack("smoke-proc/nope"))
            self.assertIsNone(node_catalog.process_node_pack("missing/copy"))
            self.assertIsNone(node_catalog.process_node_pack("no-slash"))


class RegistryAggregationTests(RuntimePathTestCase):
    def test_is_known_covers_all_categories_and_fake_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model_dir = root / "m"
            model_dir.mkdir()
            (model_dir / "manifest.json").write_text(
                json.dumps({
                    "id": "m",
                    "type": "model",
                    "generator_class": "Model",
                    "nodes": [{"id": "generate", "input": "image", "output": "mesh"}],
                }),
                encoding="utf-8",
            )
            process_dir = root / "smoke-proc"
            process_dir.mkdir()
            (process_dir / "manifest.json").write_text(
                json.dumps({
                    "id": "smoke-proc",
                    "type": "process",
                    "entry": "processor.py",
                    "nodes": [{"id": "copy", "input": "mesh", "output": "mesh"}],
                }),
                encoding="utf-8",
            )
            self.use_node_pack_root(root)

            with patch.dict(os.environ, {"POLYKIT_EXECUTOR": "fake"}):
                self.assertTrue(node_catalog.is_known("polykit.image"))
                self.assertTrue(node_catalog.is_known("polykit.output"))
                self.assertTrue(node_catalog.is_known("fake"))
                self.assertTrue(node_catalog.is_known("m/generate"))
                self.assertTrue(node_catalog.is_known("smoke-proc/copy"))
                self.assertFalse(node_catalog.is_known("nobody/nowhere"))


if __name__ == "__main__":
    unittest.main()
