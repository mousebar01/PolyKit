"""Tests for the ComfyUI-style folder registry compatibility layer."""
import tempfile
import unittest
from pathlib import Path

import services.folder_paths as fp
from services.runtime_paths import runtime_paths


class FolderPathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.original = runtime_paths.snapshot()
        fp.folder_names_and_paths.clear()
        runtime_paths.update(
            models_dir=self.root / "models",
            workspace_dir=self.root / "workspace",
            node_packs_dir=self.root / "node-packs",
        )

    def tearDown(self) -> None:
        fp.folder_names_and_paths.clear()
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def test_legacy_path_attributes_are_runtime_path_views(self) -> None:
        self.assertEqual(fp.MODELS_DIR, (self.root / "models").resolve())
        self.assertEqual(fp.WORKSPACE_DIR, (self.root / "workspace").resolve())
        self.assertEqual(fp.NODE_PACKS_DIR, (self.root / "node-packs").resolve())

    def test_get_weights_dir_uses_declared_location(self) -> None:
        manifest = {"download": {"location": "trellis2", "repo": "x/y"}}
        model_dir = fp.MODELS_DIR / "trellis2" / "generate"
        self.assertEqual(fp.get_weights_dir(model_dir, manifest), fp.MODELS_DIR / "trellis2")

    def test_get_weights_dir_falls_back_to_model_dir(self) -> None:
        manifest = {}
        model_dir = fp.MODELS_DIR / "somepack" / "node"
        self.assertEqual(fp.get_weights_dir(model_dir, manifest), model_dir)

    def test_register_and_get_folder_paths(self) -> None:
        extra = self.root / "extra-models"
        extra.mkdir()
        fp.register_folder("checkpoints", [extra], {".safetensors"})
        self.assertIn(extra, fp.get_folder_paths("checkpoints"))
        self.assertIn(".safetensors", fp.folder_names_and_paths["checkpoints"][1])

    def test_extensions_can_add_search_paths_to_managed_root(self) -> None:
        extra = self.root / "shared-models"
        extra.mkdir()
        fp.register_folder("models", [extra], {""})
        self.assertEqual(fp.get_folder_paths("models"), [runtime_paths.models, extra])

    def test_set_paths_updates_single_runtime_owner(self) -> None:
        new_models = self.root / "new-models"
        fp.set_paths(models_dir=new_models)
        self.assertEqual(runtime_paths.models, new_models.resolve())
        self.assertEqual(fp.MODELS_DIR, new_models.resolve())
        self.assertEqual(fp.get_folder_paths("models")[0], new_models.resolve())


if __name__ == "__main__":
    unittest.main()
