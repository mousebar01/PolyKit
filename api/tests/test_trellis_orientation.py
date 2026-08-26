import importlib.util
from pathlib import Path
import unittest

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "node-packs" / "trellis2" / "generator.py"


def _load_generator_module():
    spec = importlib.util.spec_from_file_location("trellis2_orientation_test_generator", GENERATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {GENERATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_geometry_export(vertices: np.ndarray) -> np.ndarray:
    """Mapping in the frozen implementation: (x, y, z) -> (x, -z, y)."""
    out = np.asarray(vertices, dtype=np.float32).copy()
    x, y, z = out[:, 0].copy(), out[:, 1].copy(), out[:, 2].copy()
    out[:, 0] = x
    out[:, 1] = -z
    out[:, 2] = y
    return out


def _legacy_refine_extra_rotation(vertices: np.ndarray) -> np.ndarray:
    """Extra mapping in the frozen refine path: (x, y, z) -> (x, z, -y)."""
    out = np.asarray(vertices, dtype=np.float32).copy()
    x, y, z = out[:, 0].copy(), out[:, 1].copy(), out[:, 2].copy()
    out[:, 0] = x
    out[:, 1] = z
    out[:, 2] = -y
    return out


class TrellisOrientationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generator = _load_generator_module()

    def test_geometry_export_maps_native_basis_to_y_up_front_plus_z(self) -> None:
        # TRELLIS native basis: +X right, -Y front, +Z up.
        native = np.array([
            [1.0, 0.0, 0.0],
            [0.0, -1.0, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float32)
        precorrected = self.generator._precorrect_geometry_vertices(native)
        exported = _legacy_geometry_export(precorrected)

        expected = np.array([
            [1.0, 0.0, 0.0],  # right -> +X
            [0.0, 0.0, 1.0],  # front -> +Z
            [0.0, 1.0, 0.0],  # up -> +Y
        ], dtype=np.float32)
        np.testing.assert_allclose(exported, expected)

    def test_refine_compensation_makes_legacy_extra_rotation_a_noop(self) -> None:
        # Aero-Ex texturing already returns canonical GLB coordinates.  The
        # wrapper pre-applies the inverse so the frozen implementation's extra
        # rotation composes to identity.
        canonical = np.array([
            [1.0, 2.0, 3.0],
            [-4.0, 5.0, -6.0],
        ], dtype=np.float32)
        prepared = self.generator._prepare_refine_vertices_for_legacy_export(canonical)
        final_vertices = _legacy_refine_extra_rotation(prepared)
        np.testing.assert_allclose(final_vertices, canonical)


if __name__ == "__main__":
    unittest.main()
