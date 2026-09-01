import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from services.process_runner import run_processor


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = REPO_ROOT / "out" / "builtin-node-packs" / "game-exporter"


class GameExporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import subprocess

        subprocess.run(["node", "scripts/build-builtins.mjs"], cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)

    def _run(self, name: str, params: dict[str, object]) -> tuple[Path, list[Path]]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "Hero.fbx"
            source.write_bytes(b"Kaydara FBX Binary  \\x00" + bytes(range(64)))
            workspace = root / "workspace"
            workspace.mkdir()
            temp = root / "tmp"
            temp.mkdir()
            result = run_processor(PACK_DIR, "processor.py", {"filePath": str(source)}, {"_node_id": name, **params}, str(workspace), str(temp))
            primary = Path(str(result["filePath"]))
            sidecars = [Path(str(item)) for item in result.get("sidecars", [])]
            # Keep files alive after TemporaryDirectory closes so callers can inspect them.
            keep = Path(tempfile.mkdtemp(prefix="game-exporter-test-"))
            copied_primary = keep / primary.name
            copied_primary.write_bytes(primary.read_bytes())
            copied_sidecars: list[Path] = []
            for sidecar in sidecars:
                destination = keep / sidecar.name
                destination.write_bytes(sidecar.read_bytes())
                copied_sidecars.append(destination)
            return copied_primary, copied_sidecars

    def test_unity_bundle_contains_meta_and_truthful_importer_note(self) -> None:
        primary, sidecars = self._run("unity-import-bundle", {})
        archive = next(path for path in sidecars if path.suffix == ".zip")
        report = next(path for path in sidecars if path.suffix == ".json")
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            self.assertIn("Assets/PolyKit/hero.fbx", names)
            self.assertIn("Assets/PolyKit/hero.fbx.meta", names)
            self.assertIn("Assets/PolyKit/PolyKitImportManifest.json", names)
            self.assertIn("Unity ModelImporter", zf.read("Assets/PolyKit/PolyKitImportManifest.json").decode())
            meta = zf.read("Assets/PolyKit/hero.fbx.meta").decode()
            self.assertRegex(meta, r"guid: [0-9a-f]{32}")
        payload = json.loads(report.read_text())
        self.assertFalse(payload["nativeAsset"])
        self.assertEqual(payload["source"]["format"], "fbx")
        self.assertTrue(primary.is_file())

    def test_unreal_bundle_records_collision_setting_and_preserves_mesh(self) -> None:
        primary, sidecars = self._run("unreal-import-bundle", {"auto_generate_collision": True})
        archive = next(path for path in sidecars if path.suffix == ".zip")
        with zipfile.ZipFile(archive) as zf:
            manifest = json.loads(zf.read("Content/PolyKit/PolyKitImportManifest.json"))
            self.assertEqual(manifest["target"], "unreal")
            self.assertTrue(manifest["importSettings"]["autoGenerateCollision"])
            self.assertIn("Content/PolyKit/hero.fbx", zf.namelist())
        self.assertTrue(primary.read_bytes().startswith(b"Kaydara FBX Binary"))


if __name__ == "__main__":
    unittest.main()
