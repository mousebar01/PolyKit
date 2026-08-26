"""Tests for the texture-preserving GLB part splitter.

The splitter reuses the original BIN chunk verbatim (texture bytes stay
byte-identical) and appends reindexed geometry per part, so UV -> texture
sampling is preserved exactly. Verified both by trimesh round-trip (UV +
material texture) and by byte-level comparison of the embedded texture.
"""
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "node-packs" / "hunyuan3d-part"))

from glb_split import (  # noqa: E402
    UnsupportedGlbError,
    read_glb,
    split_textured_glb,
    write_glb,
)

try:
    import trimesh
    _HAS_TRIMESH = True
except ImportError:
    trimesh = None
    _HAS_TRIMESH = False

try:
    import PIL  # noqa: F401
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def _png_bytes() -> bytes:
    """Tiny 2x2 RGBA PNG (solid red) built with pure stdlib."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)
    rows = b"".join(b"\x00" + bytes([200, 60, 60, 255]) * 2 for _ in range(2))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _box_vertices() -> np.ndarray:
    return np.array([
        [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
        [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
    ], dtype="<f4")


_BOX_FACES = np.array([
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
    [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
], dtype="<u2")


def _box_uv(vertex_count: int = 8) -> np.ndarray:
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]] * 2, dtype="<f4")
    return np.resize(uv, (vertex_count, 2))


def _build_glb(vertices: np.ndarray, faces: np.ndarray, tex_bytes: bytes) -> dict:
    """Assemble a GLB document + BIN for a single-primitive textured mesh."""
    uv = _box_uv(len(vertices))
    gltf: dict = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 0}],
        "bufferViews": [],
        "accessors": [],
        "images": [{"bufferView": 0, "mimeType": "image/png"}],
        "textures": [{"sampler": 0, "source": 0}],
        "samplers": [{}],
        "materials": [{
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0},
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            }
        }],
        "meshes": [],
        "nodes": [],
        "scenes": [{"nodes": []}],
        "scene": 0,
    }
    bin_out = bytearray()
    while len(bin_out) % 4:
        bin_out.append(0)
    gltf["bufferViews"].append({
        "buffer": 0,
        "byteOffset": len(bin_out),
        "byteLength": len(tex_bytes),
    })
    bin_out += tex_bytes

    def _acc(values: np.ndarray, component: int, type_name: str, target: int, min_max: bool = False) -> int:
        nonlocal bin_out
        dtype = np.dtype({5123: "<u2", 5126: "<f4"}[component])
        raw = np.ascontiguousarray(values, dtype=dtype).tobytes()
        while len(bin_out) % 4:
            bin_out.append(0)
        gltf["bufferViews"].append({
            "buffer": 0,
            "byteOffset": len(bin_out),
            "byteLength": len(raw),
            "target": target,
        })
        bin_out += raw
        accessor = {
            "bufferView": len(gltf["bufferViews"]) - 1,
            "componentType": component,
            "count": int(values.shape[0]),
            "type": type_name,
        }
        if min_max:
            accessor["min"] = [float(v) for v in values.min(axis=0)]
            accessor["max"] = [float(v) for v in values.max(axis=0)]
        gltf["accessors"].append(accessor)
        return len(gltf["accessors"]) - 1

    pos = _acc(vertices, 5126, "VEC3", 34962, min_max=True)
    uv_i = _acc(uv, 5126, "VEC2", 34962)
    idx = _acc(faces.reshape(-1), 5123, "SCALAR", 34963)
    gltf["meshes"] = [{
        "primitives": [{
            "attributes": {"POSITION": pos, "TEXCOORD_0": uv_i},
            "indices": idx,
            "material": 0,
            "mode": 4,
        }]
    }]
    gltf["nodes"] = [{"mesh": 0}]
    gltf["scenes"][0]["nodes"] = [0]
    gltf["buffers"][0]["byteLength"] = len(bin_out)
    return gltf, bytes(bin_out)


def _write_box_glb(path: Path, vertices: np.ndarray | None = None, faces=None) -> bytes:
    vertices = _box_vertices() if vertices is None else vertices
    faces = _BOX_FACES if faces is None else faces
    tex = _png_bytes()
    gltf, bin_bytes = _build_glb(vertices, faces, tex)
    write_glb(path, gltf, bin_bytes)
    return tex


def _embedded_texture_bytes(gltf: dict, bin_bytes: bytes) -> bytes:
    image_bv = gltf["bufferViews"][gltf["images"][0]["bufferView"]]
    start = image_bv["byteOffset"]
    return bin_bytes[start : start + image_bv["byteLength"]]


class GlbSplitTests(unittest.TestCase):
    def test_split_preserves_uv_and_texture(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "box.glb"
            tex = _write_box_glb(src)
            out = root / "split.glb"

            split_textured_glb(src, [0] * 6 + [1] * 6, out)

            gltf, bin_bytes = read_glb(out)
            self.assertEqual(_embedded_texture_bytes(gltf, bin_bytes), tex)

            if _HAS_TRIMESH:
                scene = trimesh.load(str(out))  # type: ignore[union-attr]
                self.assertEqual(len(scene.geometry), 2)
                for name, geometry in scene.geometry.items():
                    self.assertEqual(len(geometry.faces), 6, name)
                    visual = geometry.visual
                    self.assertIsNotNone(getattr(visual, "uv", None), name)
                    material = getattr(visual, "material", None)
                    self.assertIsNotNone(material, name)
                    if _HAS_PIL:
                        self.assertIsNotNone(
                            getattr(material, "baseColorTexture", None), name
                        )

    def test_split_face_counts_match_labels(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "box.glb"
            _write_box_glb(src)
            out = root / "split.glb"

            split_textured_glb(src, [0] * 6 + [1] * 6, out)
            gltf, _ = read_glb(out)
            self.assertEqual(len(gltf["meshes"]), 2)
            for mesh in gltf["meshes"]:
                indices = gltf["accessors"][mesh["primitives"][0]["indices"]]
                self.assertEqual(indices["count"], 18)  # 6 triangles

    def test_separation_spreads_parts_per_formula(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Two cubes: one at the origin, one offset by 5 on X.
            base = _box_vertices()
            vertices = np.vstack([base, base + [5, 0, 0]]).astype("<f4")
            faces = np.vstack([_BOX_FACES, _BOX_FACES + 8]).astype("<u2")
            src = root / "two.glb"
            _write_box_glb(src, vertices, faces)
            out = root / "split.glb"

            split_textured_glb(src, [0] * 12 + [1] * 12, out, separation=1.0)

            if not _HAS_TRIMESH:
                self.skipTest("trimesh unavailable")
            scene = trimesh.load(str(out))  # type: ignore[union-attr]
            centers = [g.bounds.mean(axis=0) for g in scene.geometry.values()]
            # Original centers (0,0,0) and (5,0,0); model center (2.5,0,0);
            # separation 1.0 doubles the offset, so distance becomes 10.
            distance = float(np.linalg.norm(centers[0] - centers[1]))
            self.assertAlmostEqual(distance, 10.0, delta=0.01)

    def test_face_ids_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "box.glb"
            _write_box_glb(src)
            out = root / "split.glb"

            with self.assertRaisesRegex(UnsupportedGlbError, "do not match"):
                split_textured_glb(src, [0] * 10, out)

    def test_multi_mesh_glb_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "box.glb"
            _write_box_glb(src)
            gltf, bin_bytes = read_glb(src)
            gltf["meshes"].append(dict(gltf["meshes"][0]))
            dup = root / "multi.glb"
            write_glb(dup, gltf, bin_bytes)

            with self.assertRaisesRegex(UnsupportedGlbError, "exactly one mesh"):
                split_textured_glb(dup, [0] * 12, root / "o.glb")

    def test_multi_primitive_glb_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "box.glb"
            _write_box_glb(src)
            gltf, bin_bytes = read_glb(src)
            gltf["meshes"][0]["primitives"].append(dict(gltf["meshes"][0]["primitives"][0]))
            dup = root / "multi.glb"
            write_glb(dup, gltf, bin_bytes)

            with self.assertRaisesRegex(UnsupportedGlbError, "single primitive"):
                split_textured_glb(dup, [0] * 12, root / "o.glb")

    def test_roundtrip_through_write_and_read_keeps_layout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "box.glb"
            tex = _write_box_glb(src)
            out = root / "split.glb"
            split_textured_glb(src, [0] * 6 + [1] * 6, out)

            # Reading our own output must not corrupt the buffer layout.
            gltf, bin_bytes = read_glb(out)
            self.assertEqual(gltf["asset"]["version"], "2.0")
            self.assertEqual(len(gltf["meshes"]), 2)
            self.assertEqual(_embedded_texture_bytes(gltf, bin_bytes), tex)


if __name__ == "__main__":
    unittest.main()
