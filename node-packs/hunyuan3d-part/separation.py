"""Part separation post-process for segmented mesh GLBs.

The Hunyuan3D-Part provider exports the *assembled* mesh (parts at their
original world positions, often merged into a single geometry). For
inspection it helps to "explode" the result: each part is pushed outward from
the model center along its own away-direction. This module is PolyKit-owned
(it is not part of the third-party provider) so Setup/Repair re-installs never
wipe it, and it is kept free of provider imports so it can be unit-tested
directly.

When the GLB already carries multiple geometries they are used as the parts.
A single-geometry GLB is split using the P3-SAM sidecars the provider leaves
next to it (``segmentation.json`` face ids, or the per-mask GLBs under
``.stage-p3-sam/``), so the separation works on real segmented outputs too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

OutputPath = Union[str, Path]


def _named_parts(scene) -> list[tuple[str, object]]:
    if not hasattr(scene, "geometry"):
        return []
    parts = [(name, geom) for name, geom in scene.geometry.items()]
    return [item for item in parts if getattr(item[1], "faces", None) is not None and len(item[1].faces) > 0]


def _slice_from_face_ids(mesh, segmentation_path: Path) -> list[object]:
    """Split a single-geometry mesh into parts using the segmentation face ids."""
    try:
        import json

        import numpy as np

        payload = json.loads(segmentation_path.read_text(encoding="utf-8"))
        raw_ids = payload.get("face_ids")
        if not isinstance(raw_ids, list):
            return []
        ids = np.asarray(raw_ids, dtype=int)
        if ids.size != len(mesh.faces):
            return []
        labels = sorted(set(int(label) for label in ids.tolist() if label >= 0))
        if len(labels) < 2:
            return []
        parts = []
        for label in labels:
            face_indices = np.nonzero(ids == label)[0].tolist()
            if len(face_indices) < 3:
                continue
            pieces = mesh.submesh([face_indices], append=True)
            if pieces is not None and len(pieces.faces) > 0:
                parts.append(pieces)
        return parts
    except Exception:
        return []


def _masks_from_stage_dir(output_path: Path) -> list[object]:
    """Fallback: use the per-mask GLBs the P3-SAM stage writes while running."""
    try:
        import trimesh

        stage_roots = sorted((output_path.parent / ".stage-p3-sam").glob("*/part_mask/*.glb"))
        parts = []
        for mask_path in stage_roots:
            try:
                mask = trimesh.load(str(mask_path))
            except Exception:
                continue
            if isinstance(mask, trimesh.Scene):
                geoms = [g for g in mask.geometry.values() if len(getattr(g, "faces", ())) > 0]
                mask = geoms[0] if geoms else None
            if mask is not None and len(mask.faces) > 0:
                parts.append(mask)
        return parts
    except Exception:
        return []


def _reconstruct_parts(output_path: Path, scene) -> list[object]:
    """Recover per-part meshes for a single-geometry output using sidecars."""
    if hasattr(scene, "geometry") and len(scene.geometry) == 1:
        mesh = next(iter(scene.geometry.values()))
    elif hasattr(scene, "faces") and len(scene.faces) > 0:
        mesh = scene
    else:
        return []

    sliced = _slice_from_face_ids(mesh, output_path.parent / "segmentation.json")
    if len(sliced) >= 2:
        return sliced
    return _masks_from_stage_dir(output_path)


def apply_part_separation(output_path: OutputPath, separation: float) -> OutputPath:
    """Ensure a segmentation GLB carries separate parts, optionally spread out.

    The provider exports the *assembled* mesh (parts merged into a single
    geometry). This post-process rewrites the file as a multipart GLB when it
    can recover the per-part meshes (from the GLB's own geometries, or from
    the P3-SAM sidecars next to it):

    * fewer than two parts known -> file left untouched;
    * already multipart and no spread requested (``separation <= 0``) ->
      left byte-identical (the default output is preserved exactly);
    * otherwise the parts are exported as separate geometries, each pushed
      outward by ``(part_center - model_center) * separation`` — 0 keeps the
      assembled multipart pose, 1 doubles each part's distance from the
      center.
    """
    try:
        import numpy as np
        import trimesh
    except ImportError:
        return output_path

    path = Path(output_path)
    try:
        scene = trimesh.load(str(path))
    except Exception:
        return output_path

    parts = _named_parts(scene)
    reconstructed = False
    if len(parts) < 2:
        recovered = _reconstruct_parts(path, scene)
        parts = [(f"part-{index}", part) for index, part in enumerate(recovered)]
        reconstructed = len(parts) >= 2

    if len(parts) < 2:
        return output_path
    if not reconstructed and separation <= 0:
        # Already carries separate parts in their original pose — keep the
        # file byte-identical unless a spread was requested.
        return output_path

    try:
        import numpy as np
        import trimesh

        centers = np.stack([part.bounds.mean(axis=0) for _, part in parts])
        model_center = centers.mean(axis=0)
        exploded = trimesh.Scene()
        for name, part in parts:
            mesh = part.copy()
            delta = (part.bounds.mean(axis=0) - model_center) * separation
            mesh.apply_translation(delta)
            exploded.add_geometry(mesh, node_name=name, geom_name=name)
        exploded.export(str(path))
    except Exception:
        # Never fail a completed generation over the inspection nicety.
        return output_path
    return output_path