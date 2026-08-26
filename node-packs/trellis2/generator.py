"""Compatibility wrapper for the bundled Trellis.2 generator.

The implementation in ``vendor/generator_impl.py`` is the exact generator
shipped by PolyKit before this orientation hotfix. Keep the compatibility
correction here small and explicit so the coordinate convention cannot silently
drift again.

Coordinate contract at the PolyKit boundary:
- TRELLIS native geometry: Z-up, front at -Y
- GLB / Three.js:          Y-up, front at +Z
- canonical mapping:      (x, y, z) -> (x, z, -y)

The frozen implementation currently has two orientation defects:
1. geometry export applies (x, -z, y), the inverse X rotation;
2. refine applies an extra canonical rotation even though Aero-Ex's texturing
   pipeline already converts GLB -> TRELLIS internally and back to GLB on output.

This wrapper compensates for those two defects without duplicating the large
model/runtime implementation.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


_PACK_DIR = Path(__file__).parent
_IMPL_PATH = _PACK_DIR / "vendor" / "generator_impl.py"
_IMPL_MODULE_NAME = (
    f"{__name__.rsplit('.', 1)[0]}.generator_impl"
    if "." in __name__
    else "polykit_trellis2_generator_impl"
)
_spec = importlib.util.spec_from_file_location(_IMPL_MODULE_NAME, _IMPL_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - import machinery guard
    raise ImportError(f"Could not load Trellis.2 implementation from {_IMPL_PATH}")
_impl = importlib.util.module_from_spec(_spec)
sys.modules[_IMPL_MODULE_NAME] = _impl
_spec.loader.exec_module(_impl)
# The frozen implementation derives all pack-relative paths from __file__. Since
# it now lives under vendor/, restore the real node-pack root for manifest/venv/
# patch/model lookups before any generator instance is used.
_impl._EXTENSION_DIR = _PACK_DIR


def _copy_vertices(vertices: Any):
    """Return a writable copy for either torch tensors or numpy-like arrays."""
    if hasattr(vertices, "clone"):
        return vertices.clone()
    import numpy as np
    return np.asarray(vertices).copy()


def _axis_copy(values: Any):
    return values.copy() if hasattr(values, "copy") else values.clone()


def _precorrect_geometry_vertices(vertices: Any):
    """Pre-rotate by 180° around X before the legacy geometry exporter.

    The legacy exporter then applies (x, -z, y). Their composition is the
    desired TRELLIS -> GLB mapping (x, z, -y).
    """
    fixed = _copy_vertices(vertices)
    y = _axis_copy(fixed[:, 1])
    z = _axis_copy(fixed[:, 2])
    fixed[:, 1] = -y
    fixed[:, 2] = -z
    return fixed


def _prepare_refine_vertices_for_legacy_export(vertices: Any):
    """Apply the inverse of the legacy refine export's extra rotation.

    Aero-Ex texturing already returns GLB-compatible coordinates. The frozen
    implementation rotates those vertices once more with (x, z, -y). Feeding
    it (x, -z, y) here makes that later transform a no-op overall.
    """
    fixed = _copy_vertices(vertices)
    y = _axis_copy(fixed[:, 1])
    z = _axis_copy(fixed[:, 2])
    fixed[:, 1] = -z
    fixed[:, 2] = y
    return fixed


def _mesh_parts(mesh: Any) -> list[Any]:
    return list(mesh.geometry.values()) if hasattr(mesh, "geometry") else [mesh]


class _TextureModelProxy:
    """Delegate to the real model while neutralising the duplicate refine rotation."""

    def __init__(self, target: Any) -> None:
        self._target = target

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    def texture_mesh(self, *args, **kwargs):
        result = self._target.texture_mesh(*args, **kwargs)
        if not isinstance(result, (tuple, list)) or not result:
            return result

        out_mesh = result[0]
        for part in _mesh_parts(out_mesh):
            part.vertices = _prepare_refine_vertices_for_legacy_export(part.vertices)

        if isinstance(result, tuple):
            return (out_mesh, *result[1:])
        return [out_mesh, *result[1:]]


class Trellis2GGUFGenerator(_impl.Trellis2GGUFGenerator):
    """Trellis.2 generator with a stable Y-up/front+Z output contract."""

    def _export_geometry(self, mesh_with_voxel, remesh_resolution: int = 768) -> Path:
        # The frozen implementation only reads ``vertices`` and ``faces`` from
        # this object. A rigid 180° X pre-rotation makes its legacy +90° X
        # export land on the correct -90° X / GLB orientation, while keeping
        # all of its remesh, winding and ground-cap cleanup unchanged.
        proxy = SimpleNamespace(
            vertices=_precorrect_geometry_vertices(mesh_with_voxel.vertices),
            faces=mesh_with_voxel.faces,
        )
        return super()._export_geometry(proxy, remesh_resolution)

    def _run_refine(self, image_bytes, params, progress_cb=None, cancel_event=None) -> Path:
        # Aero-Ex's texturing pipeline preprocesses GLB -> TRELLIS and
        # postprocesses TRELLIS -> GLB itself. The frozen PolyKit implementation
        # adds one more transform afterward. Wrap only the texture call so that
        # the later legacy transform cancels out, preserving the upstream GLB
        # coordinates and all material/UV handling.
        model = self._model
        self._model = _TextureModelProxy(model)
        try:
            return super()._run_refine(image_bytes, params, progress_cb, cancel_event)
        finally:
            self._model = model
