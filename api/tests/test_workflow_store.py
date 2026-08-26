import json
import os
import tempfile
import unittest
from pathlib import Path

from services import workflow_store
from services.runtime_paths import runtime_paths


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = runtime_paths.snapshot()

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self.original.models,
            workspace_dir=self.original.workspace,
            workflows_dir=self.original.workflows,
            node_packs_dir=self.original.node_packs,
        )

    def use_store(self, root: Path) -> Path:
        return runtime_paths.update(workflows_dir=root).workflows

    def _workflow(self, workflow_id: str, updated_at: str = "2026-08-22T00:00:00Z") -> dict:
        return {
            "id": workflow_id,
            "name": f"Workflow {workflow_id}",
            "description": "",
            "nodes": [],
            "edges": [],
            "createdAt": "2026-08-21T00:00:00Z",
            "updatedAt": updated_at,
        }

    def test_save_list_and_delete_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.use_store(Path(temp_dir))
            workflow = self._workflow("workflow/with unsafe filename chars")
            saved = workflow_store.save_workflow(workflow)
            self.assertEqual(saved, workflow)

            files = list(root.glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertNotIn("workflow", files[0].name)
            self.assertEqual(workflow_store.list_workflows(), [workflow])

            self.assertTrue(workflow_store.delete_workflow(workflow["id"]))
            self.assertEqual(workflow_store.list_workflows(), [])
            self.assertFalse(workflow_store.delete_workflow(workflow["id"]))

    def test_list_orders_latest_update_first_and_skips_corrupt_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.use_store(Path(temp_dir))
            older = self._workflow("older", "2026-08-21T10:00:00Z")
            newer = self._workflow("newer", "2026-08-22T10:00:00Z")
            workflow_store.save_workflow(older)
            workflow_store.save_workflow(newer)
            (root / "corrupt.json").write_text("{not-json", encoding="utf-8")

            self.assertEqual(
                [workflow["id"] for workflow in workflow_store.list_workflows()],
                ["newer", "older"],
            )

    def test_list_migrates_legacy_desktop_filename_to_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.use_store(Path(temp_dir))
            workflow = self._workflow("legacy-desktop-id")
            legacy = root / "legacy-desktop-id.json"
            legacy.write_text(json.dumps(workflow), encoding="utf-8")

            self.assertEqual(workflow_store.list_workflows(), [workflow])
            self.assertFalse(legacy.exists())
            files = list(root.glob("*.json"))
            self.assertEqual(files, [workflow_store._workflow_path(workflow["id"])])

    def test_newer_legacy_duplicate_wins_then_old_files_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.use_store(Path(temp_dir))
            older = self._workflow("duplicate", "2026-08-21T10:00:00Z")
            newer = self._workflow("duplicate", "2026-08-22T10:00:00Z")
            workflow_store.save_workflow(older)
            legacy = root / "duplicate.json"
            legacy.write_text(json.dumps(newer), encoding="utf-8")

            self.assertEqual(workflow_store.list_workflows(), [newer])
            self.assertFalse(legacy.exists())
            self.assertEqual(len(list(root.glob("*.json"))), 1)

    def test_delete_removes_legacy_and_canonical_copies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.use_store(Path(temp_dir))
            workflow = self._workflow("delete-me")
            workflow_store.save_workflow(workflow)
            legacy = root / "delete-me.json"
            legacy.write_text(json.dumps(workflow), encoding="utf-8")

            self.assertTrue(workflow_store.delete_workflow("delete-me"))
            self.assertEqual(list(root.glob("*.json")), [])

    def test_accepts_legacy_blocks_schema_as_migration_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.use_store(Path(temp_dir))
            legacy = {
                "id": "legacy-blocks",
                "name": "Legacy Blocks",
                "description": "",
                "input": "image",
                "blocks": [{"id": "one", "extension": "pack/node", "enabled": True, "params": {}}],
                "createdAt": "2026-08-20T00:00:00Z",
                "updatedAt": "2026-08-21T00:00:00Z",
            }
            self.assertEqual(workflow_store.save_workflow(legacy), legacy)
            self.assertEqual(workflow_store.list_workflows(), [legacy])

    def test_set_workflows_dir_updates_runtime_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first"
            second = root / "second"
            workflow_store.set_workflows_dir(first)
            workflow_store.save_workflow(self._workflow("first-only"))

            target = workflow_store.set_workflows_dir(second)
            self.assertEqual(runtime_paths.workflows, target)
            self.assertEqual(runtime_paths.workflows, target)
            self.assertEqual(os.environ.get("WORKFLOWS_DIR"), str(target))
            self.assertTrue(target.is_dir())
            self.assertEqual(workflow_store.list_workflows(), [])
            self.assertEqual(len(list(first.glob("*.json"))), 1)

    def test_rejects_invalid_workflow_shape_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.use_store(Path(temp_dir))
            with self.assertRaises(ValueError):
                workflow_store.save_workflow({"id": "bad", "name": "Bad", "nodes": {}})
            self.assertEqual(list(root.iterdir()), [])

    def test_atomic_save_leaves_no_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.use_store(Path(temp_dir))
            workflow_store.save_workflow(self._workflow("atomic"))
            self.assertEqual(list(root.glob("*.tmp")), [])
            self.assertEqual(list(root.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
