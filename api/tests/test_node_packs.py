from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import routers.node_packs as node_packs
from services.runtime_paths import runtime_paths


class NodePackListTests(unittest.TestCase):
    def setUp(self) -> None:
        self._paths = runtime_paths.snapshot()

    def tearDown(self) -> None:
        runtime_paths.update(
            models_dir=self._paths.models,
            workspace_dir=self._paths.workspace,
            workflows_dir=self._paths.workflows,
            node_packs_dir=self._paths.node_packs,
        )

    def test_list_includes_process_packs_with_entry_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack_dir = root / "image-background-remover"
            pack_dir.mkdir()
            (pack_dir / ".polykit-official").write_text("official\n", encoding="utf-8")
            (pack_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "image-background-remover",
                        "name": "Image Background Remover",
                        "type": "process",
                        "entry": "processor.py",
                        "nodes": [
                            {
                                "id": "remove-background",
                                "input": "image",
                                "output": "image",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            runtime_paths.update(node_packs_dir=root)

            with patch.object(node_packs.model_runtime_registry, "manifests", return_value={}):
                result = asyncio.run(node_packs.list_node_packs())

        self.assertEqual(len(result), 1)
        pack = result[0]
        self.assertEqual(pack["type"], "process")
        self.assertEqual(pack["id"], "image-background-remover")
        self.assertEqual(pack["entry"], "processor.py")
        self.assertTrue(pack["builtin"])
        self.assertEqual(pack["nodes"][0]["id"], "remove-background")
        self.assertEqual(pack["nodes"][0]["input"], "image")
        self.assertEqual(pack["nodes"][0]["output"], "image")


if __name__ == "__main__":
    unittest.main()
