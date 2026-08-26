import tempfile
import unittest
from pathlib import Path

import trimesh

from services.asset_previews import ensure_preview
from services.mesh_utils import face_count
from services.runtime_paths import runtime_paths


def _write_glb(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.creation.icosphere(subdivisions=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mesh.export(file_type="glb"))
    return mesh


class AssetPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self._tmp = tempfile.TemporaryDirectory(prefix="polykit-preview-test-")
        self.workspace = Path(self._tmp.name)
        runtime_paths.update(workspace_dir=self.workspace)
        self.mesh_path = self.workspace / "Workflows" / "tiny.glb"
        self.mesh = _write_glb(self.mesh_path)

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )
        self._tmp.cleanup()

    def test_face_count_matches_mesh(self) -> None:
        self.assertEqual(face_count(self.mesh_path), len(self.mesh.faces))

    def test_face_count_ignores_non_glb(self) -> None:
        txt = self.mesh_path.with_suffix(".txt")
        txt.write_text("not a model", encoding="utf-8")
        self.assertIsNone(face_count(txt))

    def test_ensure_preview_generates_cached_glb(self) -> None:
        out = ensure_preview("Workflows/tiny.glb", self.mesh_path)
        self.assertIsNotNone(out)
        self.assertTrue(out.is_file())
        self.assertEqual(out.suffix, ".glb")
        self.assertEqual(ensure_preview("Workflows/tiny.glb", self.mesh_path), out)
        self.assertTrue(out.is_relative_to(self.workspace))

    def test_face_cap_skips_generation(self) -> None:
        import services.asset_previews as module

        original = module._MAX_SOURCE_FACES
        module._MAX_SOURCE_FACES = 1
        try:
            self.assertIsNone(ensure_preview("Workflows/tiny.glb", self.mesh_path))
        finally:
            module._MAX_SOURCE_FACES = original


if __name__ == "__main__":
    unittest.main()
