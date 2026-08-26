import shutil
import tempfile
import unittest
from pathlib import Path

from services.mesh_artifacts import MeshArtifact
from services.runtime_paths import runtime_paths
from services.workflow_engine import ArtifactNodeOutputCache, _materialize_cached_preview
from services.workflow_executor import NodeOutputCache


class WorkflowCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )

    def test_workspace_source_signature_changes_when_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            runtime_paths.update(workspace_dir=workspace)
            source = workspace / "Workflows" / "input.bin"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"first")

            cache = NodeOutputCache()
            payload = {"image": {"kind": "workspace_path", "path": "Workflows/input.bin"}}
            first = cache.signature("polykit.image", payload, {})
            source.write_bytes(b"second-version")
            second = cache.signature("polykit.image", payload, {})

            self.assertNotEqual(first, second)

    def test_artifact_cache_owns_a_stable_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            runtime_paths.update(workspace_dir=workspace)
            run_file = workspace / ".artifacts" / "run-1" / "models" / "mesh.glb"
            run_file.parent.mkdir(parents=True, exist_ok=True)
            run_file.write_bytes(b"mesh-data")

            cache = ArtifactNodeOutputCache(max_entries=8)
            cache.set(
                "signature",
                {"mesh": MeshArtifact(path=run_file, persistent=False, origin="model")},
            )
            run_file.unlink()

            cached = cache.get("signature")
            self.assertIsNotNone(cached)
            artifact = cached["mesh"]
            self.assertIsInstance(artifact, MeshArtifact)
            self.assertTrue(artifact.path.is_file())
            self.assertEqual(artifact.path.read_bytes(), b"mesh-data")
            self.assertEqual(artifact.origin, "cache")

    def test_prune_recreates_a_deleted_cache_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            runtime_paths.update(workspace_dir=workspace)
            cache = ArtifactNodeOutputCache(max_entries=8)
            shutil.rmtree(workspace / ".node-cache")

            cache.prune()

            self.assertTrue((workspace / ".node-cache").is_dir())

    def test_preview_materializes_cache_hit_into_run_owned_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            runtime_paths.update(workspace_dir=workspace)
            source = workspace / ".node-cache" / "sig" / "mesh-0.glb"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"cached-preview")
            cached = MeshArtifact(path=source, persistent=True, origin="cache")

            preview = _materialize_cached_preview(
                cached,
                workspace / ".artifacts" / "run-preview" / "preview",
            )
            self.assertIsInstance(preview, MeshArtifact)
            self.assertFalse(preview.persistent)
            self.assertEqual(preview.origin, "preview-cache")
            self.assertIn(".artifacts", preview.path.parts)
            self.assertEqual(preview.path.read_bytes(), b"cached-preview")

            source.unlink()
            self.assertTrue(preview.path.is_file())
            self.assertEqual(preview.path.read_bytes(), b"cached-preview")


if __name__ == "__main__":
    unittest.main()
