import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np

from services import asset_thumbnails


class _FakeTexture:
    def __init__(self, size: tuple[int, int], rgb: tuple[int, int, int] = (200, 100, 50)) -> None:
        self.size = size
        self.rgb = rgb

    def convert(self, mode: str):
        if mode != "RGB":
            raise ValueError(mode)
        return self

    def resize(self, size: tuple[int, int]):
        return _FakeTexture(size, self.rgb)

    def __array__(self, dtype=None):
        width, height = self.size
        data = np.empty((height, width, 3), dtype=np.uint8)
        data[:, :] = self.rgb
        return data.astype(dtype, copy=False) if dtype is not None else data


class AssetThumbnailMaterialTests(unittest.TestCase):
    def test_representative_source_vertices_inverts_decimation_mapping(self) -> None:
        # Original vertices 0/1 collapse to output 0, 2 maps to 1 and 3 maps to 2.
        representatives = asset_thumbnails._representative_source_vertices(
            np.array([0, 0, 1, 2], dtype=np.int64),
            3,
        )
        np.testing.assert_array_equal(representatives, np.array([0, 2, 3], dtype=np.int64))

    def test_texture_payload_keeps_uvs_and_downscales_albedo(self) -> None:
        material = SimpleNamespace(
            baseColorTexture=_FakeTexture((1024, 256)),
            baseColorFactor=[128, 255, 64, 255],
            metallicFactor=0.25,
            roughnessFactor=0.75,
        )
        mesh = SimpleNamespace(
            visual=SimpleNamespace(
                material=material,
                uv=np.array([
                    [0.0, 0.0],
                    [1.0, 0.0],
                    [1.0, 1.0],
                    [0.0, 1.0],
                ], dtype=np.float64),
            )
        )
        vertices = np.zeros((4, 3), dtype=np.float64)
        faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)

        payload = asset_thumbnails._texture_payload(mesh, vertices, faces, None)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["pixels"].shape, (128, 512, 3))
        np.testing.assert_allclose(
            payload["triangle_uvs"],
            np.array([
                [0.0, 0.0], [1.0, 0.0], [1.0, 1.0],
                [0.0, 0.0], [1.0, 1.0], [0.0, 1.0],
            ]),
        )
        np.testing.assert_allclose(payload["base_color"], [128 / 255, 1.0, 64 / 255, 1.0])
        self.assertEqual(payload["metallic"], 0.25)
        self.assertEqual(payload["roughness"], 0.75)

    def test_missing_texture_keeps_neutral_fallback_path(self) -> None:
        mesh = SimpleNamespace(
            visual=SimpleNamespace(
                material=SimpleNamespace(baseColorTexture=None),
                uv=np.array([[0.0, 0.0]], dtype=np.float64),
            )
        )
        payload = asset_thumbnails._texture_payload(
            mesh,
            np.zeros((1, 3), dtype=np.float64),
            np.zeros((0, 3), dtype=np.int64),
            None,
        )
        self.assertIsNone(payload)


class AssetThumbnailRenderIsolationTests(unittest.TestCase):
    def test_ensure_thumbnail_skips_unsupported_extension(self) -> None:
        with TemporaryDirectory() as td:
            bad = Path(td) / "asset.fbx"
            bad.write_bytes(b"x")
            self.assertIsNone(asset_thumbnails.ensure_thumbnail("Assets/asset.fbx", bad))

    def test_ensure_thumbnail_skips_oversized_source(self) -> None:
        with TemporaryDirectory() as td:
            big = Path(td) / "huge.glb"
            big.write_bytes(b"\0" * (asset_thumbnails._MAX_SOURCE_BYTES + 1))
            self.assertIsNone(asset_thumbnails.ensure_thumbnail("Assets/huge.glb", big))

    def test_ensure_thumbnail_returns_none_for_missing_file(self) -> None:
        self.assertIsNone(asset_thumbnails.ensure_thumbnail("Assets/gone.glb", Path("/nonexistent/x.glb")))

    def test_ensure_thumbnail_skips_path_without_triangle_geometry(self) -> None:
        # A binary blob that is not a mesh still passes the cheap checks, then
        # the render child fails fast and the endpoint degrades to None.
        with TemporaryDirectory() as td:
            junk = Path(td) / "junk.glb"
            junk.write_bytes(b"not a real glb")
            self.assertIsNone(asset_thumbnails.ensure_thumbnail("Assets/junk.glb", junk))


if __name__ == "__main__":
    unittest.main()
