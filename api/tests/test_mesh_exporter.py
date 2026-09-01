import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import trimesh

from services.process_runner import run_processor


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = REPO_ROOT / "out" / "builtin-node-packs" / "mesh-exporter"
BLENDER_AVAILABLE = bool(shutil.which("blender") or (Path(os.path.expanduser("~")) / ".local" / "bin" / "blender").is_file())


@unittest.skipUnless(BLENDER_AVAILABLE, "Blender is required for the FBX exporter smoke test")
class MeshExporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(["node", "scripts/build-builtins.mjs"], cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)

    def test_fbx_export_uses_blender_and_writes_binary_asset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.glb"
            trimesh.creation.box().export(source)
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()

            result = run_processor(
                PACK_DIR,
                "processor.js",
                {"filePath": str(source)},
                {"_node_id": "export", "export_format": "fbx"},
                str(workspace),
                str(temp),
            )
            output = Path(str(result["filePath"]))
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 1024)
            self.assertEqual(output.read_bytes()[:20], b"Kaydara FBX Binary  ")


if __name__ == "__main__":
    unittest.main()
