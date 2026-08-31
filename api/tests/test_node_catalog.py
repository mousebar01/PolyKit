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
    def test_builtin_definitions_cover_sources_controls_and_sinks(self) -> None:
        definitions = node_catalog._builtin_definitions()
        by_type = {d.class_type: d for d in definitions}
        self.assertEqual(
            set(by_type),
            {
                "polykit.image",
                "polykit.text",
                "polykit.mesh",
                "polykit.interrupt",
                "polykit.output",
                "polykit.preview",
                "polykit.image_output",
            },
        )
        self.assertEqual(by_type["polykit.image"].outputs, ["image"])
        self.assertEqual(by_type["polykit.mesh"].outputs, ["mesh"])
        self.assertEqual(by_type["polykit.interrupt"].inputs, ["after"])
        self.assertEqual(by_type["polykit.interrupt"].outputs, ["signal"])
        self.assertEqual(by_type["polykit.output"].inputs, ["mesh"])
        self.assertEqual(by_type["polykit.image_output"].inputs, ["image"])
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
                        "name": "Generate 3D",
                        "input": "image",
                        "output": "mesh",
                        "params_schema": [{"id": "seed", "type": "int", "default": 0}],
                    },
                    {
                        "id": "refine",
                        "name": "Refine Texture",
                        "inputs": ["image", "mesh"],
                        "input_labels": ["Reference Image", "Base Mesh"],
                        "output": "mesh",
                    },
                ],
            }),
            encoding="utf-8",
        )
        return pack_dir

    def test_model_definitions_come_from_manifest_without_runtime_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_model_pack(root)
            self.use_node_pack_root(root)
            definitions = node_catalog._model_definitions()

        by_type = {item.class_type: item for item in definitions}
        self.assertEqual(set(by_type), {"trellis2/generate", "trellis2/refine"})
        self.assertEqual(by_type["trellis2/generate"].inputs, ["image"])
        self.assertEqual(by_type["trellis2/generate"].outputs, ["mesh"])
        self.assertEqual(by_type["trellis2/refine"].inputs, ["image", "mesh"])
        self.assertEqual(by_type["trellis2/refine"].input_labels, ["Reference Image", "Base Mesh"])
        self.assertEqual(by_type["trellis2/generate"].params_schema[0]["id"], "seed")


class ProcessNodeTests(RuntimePathTestCase):
    def test_process_definitions_include_entry_and_batch_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack = root / "mesh-tools"
            pack.mkdir(parents=True)
            (pack / "manifest.json").write_text(
                json.dumps({
                    "id": "mesh-tools",
                    "name": "Mesh Tools",
                    "type": "process",
                    "entry": "processor.py",
                    "nodes": [
                        {
                            "id": "merge",
                            "name": "Merge Meshes",
                            "input": "mesh",
                            "output": "mesh",
                            "batch_input": "mesh",
                        }
                    ],
                }),
                encoding="utf-8",
            )
            self.use_node_pack_root(root)
            definitions = node_catalog._process_definitions()

        self.assertEqual(len(definitions), 1)
        merge = definitions[0]
        self.assertEqual(merge.class_type, "mesh-tools/merge")
        self.assertEqual(merge.entry, "processor.py")
        self.assertEqual(merge.batch_input, "mesh")


if __name__ == "__main__":
    unittest.main()
