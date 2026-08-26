import os
import tempfile
import unittest
from pathlib import Path

from services.run_store import _default_db_path
from services.runtime_paths import runtime_paths


class RuntimePathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()
        self.original_state_env = os.environ.get("POLYKIT_STATE_DB")

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
            state_db=self.original.state_db if self.original_state_env else None,
        )
        if self.original_state_env is None:
            runtime_paths._explicit_state_db = False
            os.environ.pop("POLYKIT_STATE_DB", None)
        else:
            os.environ["POLYKIT_STATE_DB"] = self.original_state_env

    def test_default_state_db_follows_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_paths._explicit_state_db = False
            os.environ.pop("POLYKIT_STATE_DB", None)
            workspace = Path(tmp) / "workspace"
            runtime_paths.update(workspace_dir=workspace)

            self.assertEqual(runtime_paths.workspace, workspace.resolve())
            self.assertEqual(
                runtime_paths.state_db,
                workspace.resolve() / ".polykit-runs.sqlite3",
            )
            self.assertEqual(_default_db_path(), runtime_paths.state_db)

    def test_runtime_paths_sync_child_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = runtime_paths.update(
                models_dir=root / "models",
                workspace_dir=root / "workspace",
                workflows_dir=root / "workflows",
                node_packs_dir=root / "node-packs",
            )
            self.assertEqual(os.environ["MODELS_DIR"], str(snapshot.models))
            self.assertEqual(os.environ["WORKSPACE_DIR"], str(snapshot.workspace))
            self.assertEqual(os.environ["WORKFLOWS_DIR"], str(snapshot.workflows))
            self.assertEqual(os.environ["NODE_PACKS_DIR"], str(snapshot.node_packs))


if __name__ == "__main__":
    unittest.main()
