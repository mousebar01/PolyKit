"""PolyKit wrapper for the managed Hunyuan3D-Part provider.

The third-party MIT adapter is installed by setup.py into ``provider/``. It is
kept out of the bundled source tree so PolyKit can pin/update it independently
without vendoring Tencent's runtime or model weights.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

PACK_ROOT = Path(__file__).resolve().parent
PROVIDER_ROOT = PACK_ROOT / "provider"
PROVIDER_REVISION = "48b9ee3540bf7a85bcb7eb982f748d0fe14195a8"
_PROVIDER_ENTRY = PROVIDER_ROOT / "generator.py"
_PROVIDER_REVISION_FILE = PROVIDER_ROOT / ".polykit-provider-revision"


def _provider_revision() -> str:
    try:
        return _PROVIDER_REVISION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _load_provider_module() -> ModuleType:
    if not _PROVIDER_ENTRY.is_file():
        raise RuntimeError(
            "Hunyuan3D-Part runtime is not set up. Open Models, select "
            "Hunyuan3D-Part, and run Setup/Repair first."
        )

    installed_revision = _provider_revision()
    if installed_revision != PROVIDER_REVISION:
        installed_label = installed_revision[:8] if installed_revision else "unknown"
        raise RuntimeError(
            "Hunyuan3D-Part provider revision is out of date "
            f"(installed {installed_label}, expected {PROVIDER_REVISION[:8]}). "
            "Run Setup/Repair from Models before loading the node."
        )

    provider_src = PROVIDER_ROOT / "src"
    for path in (PROVIDER_ROOT, provider_src):
        text = str(path)
        if path.is_dir() and text not in sys.path:
            sys.path.insert(0, text)

    spec = importlib.util.spec_from_file_location(
        "polykit_hunyuan3d_part_provider",
        _PROVIDER_ENTRY,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load Hunyuan3D-Part provider at {_PROVIDER_ENTRY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_provider = _load_provider_module()
_ProviderGenerator = getattr(_provider, "Hunyuan3DPartGenerator")


class Hunyuan3DPartGenerator(_ProviderGenerator):
    """Bind the external adapter to PolyKit's persistent node-pack root.

    The adapter code lives in ``provider/`` but its managed venv, upstream
    Tencent runtime checkout, caches and setup state deliberately live at the
    official pack root. ``official_packs.sync_official_packs`` refreshes only
    reviewed code files, so those runtime directories survive app updates.
    """

    def __init__(self, model_dir: str | Path, outputs_dir: str | Path) -> None:
        super().__init__(
            model_dir,
            outputs_dir,
            project_root=PACK_ROOT,
        )

        # Make the managed cache location explicit for subprocesses spawned by
        # the provider. Its runtime also sets this variable, but defining it at
        # the wrapper boundary makes diagnostics and manual smoke tests agree.
        os.environ.setdefault("P3SAM_SONATA_CACHE", str(PACK_ROOT / ".cache" / "sonata"))

    @classmethod
    def params_schema(cls) -> list[dict[str, Any]]:
        # runner.py falls back to manifest metadata when this method is absent;
        # returning [] explicitly keeps the provider implementation independent
        # from PolyKit's presentation schema.
        return []

    # ------------------------------------------------------------------ #
    # Part post-processing (PolyKit-owned)
    # ------------------------------------------------------------------ #
    #
    # The third-party provider exports parts at their original world positions
    # (a tightly assembled multipart GLB) rebuilt from vertices/faces only, so
    # the input's UVs and texture are lost. Two PolyKit-owned steps restore the
    # useful presentation:
    #
    # 1. Texture-preserving split: when the original textured input and the
    #    segmentation face labels are available, re-split the ORIGINAL mesh by
    #    labels with its UVs, materials and texture bitmap intact (glb_split.py,
    #    pure stdlib, texture bytes byte-identical).
    # 2. Part separation: push parts outward from the model center for
    #    inspection (separation.py, geometry-only explode).
    #
    # Both live in the wrapper (not the provider) so Setup/Repair re-installs
    # never wipe them, and the workflow UI exposes ``part_separation``.

    def generate(
        self,
        image_bytes: object = None,
        params: Mapping[str, object] | None = None,
        progress_cb: Any | None = None,
        cancel_event: Any | None = None,
        **kwargs: Any,
    ) -> object:
        result = super().generate(
            image_bytes,
            params=params,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            **kwargs,
        )
        if isinstance(result, str):
            return self._apply_part_separation(result, dict(params or {}), progress_cb, image_bytes)
        return result

    @staticmethod
    def _load_face_ids(output_path: str) -> list[int] | None:
        try:
            import json

            seg = Path(output_path).parent / "segmentation.json"
            payload = json.loads(seg.read_text(encoding="utf-8"))
            raw = payload.get("face_ids")
            if isinstance(raw, list):
                return [int(value) for value in raw]
        except Exception:
            pass
        return None

    def _load_sibling_module(self, name: str, filename: str):
        """Load a sibling module by path (this wrapper has no package context)."""
        spec = importlib.util.spec_from_file_location(
            f"polykit_hunyuan3d_part_{name}", PACK_ROOT / filename
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load {filename}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _apply_part_separation(
        self,
        output_path: str,
        params: Mapping[str, object],
        progress_cb: Any | None,
        primary_input: object = None,
    ) -> str:
        try:
            separation = float(params.get("part_separation") or 0)
        except (TypeError, ValueError):
            separation = 0.0

        # --- Texture-preserving split (default) -------------------------- #
        # The provider output is geometry-only; re-split the ORIGINAL textured
        # mesh by the segmentation labels so UVs, materials and the texture
        # bitmap survive. Falls back to the geometry-only path when the input
        # mesh or labels are unavailable, or the GLB is not supported.
        original_mesh = ""
        if isinstance(primary_input, (str, Path)):
            original_mesh = str(primary_input)
        if not original_mesh:
            original_mesh = str(params.get("mesh_path") or "")
        face_ids = self._load_face_ids(output_path)
        if original_mesh and face_ids and Path(original_mesh).is_file():
            try:
                glb_split = self._load_sibling_module("glb_split", "glb_split.py")
                new_output = glb_split.split_textured_glb(
                    Path(original_mesh),
                    face_ids,
                    Path(output_path),
                    separation=separation,
                )
                self._emit_progress(
                    progress_cb,
                    stage="postprocess",
                    percent=100,
                    message=(
                        "Parts split with textures preserved."
                        if separation <= 0
                        else f"Parts split with textures, spread by {separation:g}."
                    ),
                )
                return str(new_output)
            except Exception:
                pass  # fall through to the geometry-only path

        # --- Geometry-only separation (fallback) ------------------------- #
        if separation <= 0:
            return output_path

        try:
            module = self._load_sibling_module("separation", "separation.py")
        except Exception:
            return output_path

        self._emit_progress(
            progress_cb,
            stage="postprocess",
            percent=96,
            message=f"Spreading parts for inspection (separation {separation:g}).",
        )
        output_path = module.apply_part_separation(output_path, separation)
        self._emit_progress(
            progress_cb,
            stage="postprocess",
            percent=100,
            message="Part separation applied.",
        )
        return output_path
