from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from services.gltf_skin import has_skin_metadata


def _write_glb(path: Path, document: dict) -> None:
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    total_length = 12 + 8 + len(encoded)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
    )


class GltfSkinTests(unittest.TestCase):
    def test_detects_structural_skin_markers_in_glb(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rigged.glb"
            _write_glb(
                path,
                {
                    "asset": {"version": "2.0"},
                    "nodes": [{"name": "root"}],
                    "skins": [{"joints": [0]}],
                    "meshes": [{"primitives": [{"attributes": {"JOINTS_0": 0, "WEIGHTS_0": 1}}]}],
                },
            )
            self.assertTrue(has_skin_metadata(path))

    def test_rejects_mesh_without_skin_or_invalid_joint_index(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plain = Path(td) / "plain.glb"
            _write_glb(
                plain,
                {
                    "asset": {"version": "2.0"},
                    "nodes": [{"name": "root"}],
                    "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
                },
            )
            self.assertFalse(has_skin_metadata(plain))

            invalid = Path(td) / "invalid.glb"
            _write_glb(
                invalid,
                {
                    "asset": {"version": "2.0"},
                    "nodes": [{"name": "root"}],
                    "skins": [{"joints": [1]}],
                    "meshes": [{"primitives": [{"attributes": {"JOINTS_0": 0, "WEIGHTS_0": 1}}]}],
                },
            )
            self.assertFalse(has_skin_metadata(invalid))

    def test_detects_json_gltf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rigged.gltf"
            path.write_text(
                json.dumps(
                    {
                        "asset": {"version": "2.0"},
                        "nodes": [{}],
                        "skins": [{"joints": [0]}],
                        "meshes": [{"primitives": [{"attributes": {"JOINTS_0": 0, "WEIGHTS_0": 1}}]}],
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(has_skin_metadata(path))


if __name__ == "__main__":
    unittest.main()
