"""Tests for the official (bundled) node-pack sync service."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from services.official_packs import (
    _sync_model_pack,
    _sync_process_pack,
    is_official,
    sync_official_packs,
)

MODEL_MANIFEST = {
    "id": "trellis2",
    "type": "model",
    "generator_class": "Trellis2GGUFGenerator",
    "env": "shared",
    "trusted": True,
    "builtin": True,
    "requirements": ["Pillow", "numpy"],
    "nodes": [{"id": "generate", "name": "Generate Mesh"}],
}


class OfficialPacksSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.official = self.root / "official"
        self.builtin = self.root / "builtin"
        self.runtime = self.root / "runtime"
        (self.official / "trellis2").mkdir(parents=True)
        self.builtin.mkdir(parents=True)
        self._old_env = {}
        for key in ("POLYKIT_OFFICIAL_PACKS_DIR", "POLYKIT_BUILTIN_PACKS_DIR"):
            self._old_env[key] = os.environ.get(key)
        os.environ["POLYKIT_OFFICIAL_PACKS_DIR"] = str(self.official)
        os.environ["POLYKIT_BUILTIN_PACKS_DIR"] = str(self.builtin)

    def tearDown(self) -> None:
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def _write_model_pack(self, manifest: dict, generator: str = "class T: pass\n") -> Path:
        p = self.official / "trellis2"
        (p / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (p / "generator.py").write_text(generator, encoding="utf-8")
        (p / "docs").mkdir(exist_ok=True)
        (p / "docs" / "guide.md").write_text("hi", encoding="utf-8")
        return p

    def test_sync_creates_pack_and_sentinel(self) -> None:
        self._write_model_pack(MODEL_MANIFEST)

        synced = sync_official_packs(self.runtime)
        self.assertIn("trellis2", synced)
        dst = self.runtime / "trellis2"
        self.assertTrue((dst / "manifest.json").exists())
        self.assertTrue((dst / "generator.py").exists())
        self.assertTrue((dst / "docs" / "guide.md").exists())
        self.assertTrue(is_official(dst))
        # Sentinel file itself must not be synced from source (none exists there).
        self.assertFalse((self.official / "trellis2" / ".polykit-official").exists())

    def test_sync_preserves_runtime_state(self) -> None:
        self._write_model_pack(MODEL_MANIFEST)
        sync_official_packs(self.runtime)
        dst = self.runtime / "trellis2"
        (dst / "venv" / "bin").mkdir(parents=True)
        (dst / "venv" / "bin" / "python").write_text("fake", encoding="utf-8")
        (dst / "weights.bin").write_bytes(b"\x00" * 8)

        # Update the bundled generator and sync again.
        self._write_model_pack(MODEL_MANIFEST, generator="class T: pass\n# v2\n")
        sync_official_packs(self.runtime)

        self.assertIn("# v2", (dst / "generator.py").read_text())
        # Runtime state must survive.
        self.assertTrue((dst / "venv" / "bin" / "python").exists())
        self.assertEqual((dst / "weights.bin").read_bytes(), b"\x00" * 8)
        self.assertTrue(is_official(dst))

    def test_process_pack_sync_copies_whole_dir(self) -> None:
        os.environ["POLYKIT_BUILTIN_PACKS_DIR"] = str(self.builtin)
        proc = self.builtin / "mesh-optimizer"
        proc.mkdir(parents=True)
        (proc / "manifest.json").write_text(
            json.dumps({"id": "mesh-optimizer", "type": "process", "entry": "processor.js"}),
            encoding="utf-8",
        )
        (proc / "processor.js").write_text("module.exports = {}", encoding="utf-8")
        (proc / "node_modules").mkdir()
        (proc / "node_modules" / "dep").mkdir()
        (proc / "node_modules" / "dep" / "index.js").write_text("x", encoding="utf-8")

        sync_official_packs(self.runtime)
        dst = self.runtime / "mesh-optimizer"
        self.assertTrue((dst / "processor.js").exists())
        self.assertTrue((dst / "node_modules" / "dep" / "index.js").exists())
        self.assertTrue(is_official(dst))

    def test_no_official_dir_is_noop(self) -> None:
        # Point both sources at empty dirs — hermetic, no repo fallback.
        empty = self.root / "empty"
        empty.mkdir()
        os.environ["POLYKIT_OFFICIAL_PACKS_DIR"] = str(empty)
        os.environ["POLYKIT_BUILTIN_PACKS_DIR"] = str(empty)
        self.assertEqual(sync_official_packs(self.runtime), [])
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_sync_does_not_clobber_venv_on_model_update(self) -> None:
        # Direct unit check on _sync_model_pack: venv is not in _CODE_DIRS.
        p = self.official / "trellis2"
        (p / "manifest.json").write_text(json.dumps(MODEL_MANIFEST), encoding="utf-8")
        dst = self.runtime / "trellis2"
        dst.mkdir(parents=True)
        (dst / "venv").mkdir()
        (dst / "venv" / "keep.txt").write_text("keep", encoding="utf-8")
        changed = _sync_model_pack(p, dst)
        self.assertTrue(changed)
        self.assertTrue((dst / "venv" / "keep.txt").exists())

    def test_sync_seeds_missing_runtime_state_from_bundled_pack(self) -> None:
        # A self-contained bundled pack carries venv/provider/.upstream/.cache.
        p = self.official / "trellis2"
        (p / "manifest.json").write_text(json.dumps(MODEL_MANIFEST), encoding="utf-8")
        (p / "generator.py").write_text("class T: pass\n", encoding="utf-8")
        for dname in ("venv", "provider", ".upstream", ".cache"):
            (p / dname).mkdir(parents=True)
            (p / dname / "marker.txt").write_text("bundled", encoding="utf-8")

        dst = self.runtime / "trellis2"
        changed = _sync_model_pack(p, dst)

        self.assertTrue(changed)
        for dname in ("venv", "provider", ".upstream", ".cache"):
            self.assertTrue((dst / dname / "marker.txt").exists(), dname)

    def test_sync_seeding_never_overwrites_existing_runtime_state(self) -> None:
        # The runtime dir already has a prepared environment: seeding must not
        # replace it with the bundled copy.
        p = self.official / "trellis2"
        (p / "manifest.json").write_text(json.dumps(MODEL_MANIFEST), encoding="utf-8")
        (p / "venv").mkdir()
        (p / "venv" / "bundled.txt").write_text("bundled", encoding="utf-8")

        dst = self.runtime / "trellis2"
        dst.mkdir(parents=True)
        (dst / "venv").mkdir()
        (dst / "venv" / "prepared.txt").write_text("prepared", encoding="utf-8")

        _sync_model_pack(p, dst)

        self.assertTrue((dst / "venv" / "prepared.txt").exists())
        self.assertFalse((dst / "venv" / "bundled.txt").exists())


if __name__ == "__main__":
    unittest.main()
