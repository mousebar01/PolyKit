"""Typed directory registry for model/node-pack compatibility.

Runtime root ownership lives in :mod:`services.runtime_paths`. This module keeps
the ComfyUI-style folder lookup API used by node packs without maintaining a
second mutable copy of PolyKit's core paths.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from services.runtime_paths import runtime_paths


# Extension-provided paths. Core roots are resolved from RuntimePaths on every
# read; extensions may still add extra search locations to a core kind.
folder_names_and_paths: Dict[str, Tuple[List[Path], Set[str]]] = {}


def set_paths(
    models_dir: Optional[Path] = None,
    workspace_dir: Optional[Path] = None,
    node_packs_dir: Optional[Path] = None,
) -> None:
    """Compatibility entry point for callers that update runtime roots."""
    runtime_paths.update(
        models_dir=models_dir,
        workspace_dir=workspace_dir,
        node_packs_dir=node_packs_dir,
    )


def register_folder(name: str, paths: Iterable[Path], extensions: Optional[Iterable[str]] = None) -> None:
    """Register or extend a folder kind without taking ownership of core roots."""
    ext_set: Set[str] = set(extensions or {""})
    existing = folder_names_and_paths.get(name)
    if existing:
        existing[0].extend(Path(p) for p in paths)
        existing[1].update(ext_set)
    else:
        folder_names_and_paths[name] = (list(Path(p) for p in paths), ext_set)


def get_folder_paths(name: str) -> List[Path]:
    """Return the managed root followed by extension-provided search paths."""
    extras = list(folder_names_and_paths.get(name, ([], set()))[0])
    if name == "models":
        return [runtime_paths.models, *extras]
    if name == "workspace":
        return [runtime_paths.workspace, *extras]
    if name == "node_packs":
        return [runtime_paths.node_packs, *extras]
    return extras


def get_weights_dir(model_dir: Path, manifest: dict) -> Path:
    """Resolve where a model pack's weights live.

    ``manifest.download.location`` is relative to the configured model root;
    otherwise the per-node model directory supplied by the runtime is used.
    """
    download = manifest.get("download") or {}
    location = download.get("location")
    if location:
        return runtime_paths.models / str(location).lstrip("/\\")
    return Path(model_dir)


def __getattr__(name: str):
    """Compatibility for legacy imports of the old module-level path names."""
    if name == "MODELS_DIR":
        return runtime_paths.models
    if name == "WORKSPACE_DIR":
        return runtime_paths.workspace
    if name == "NODE_PACKS_DIR":
        return runtime_paths.node_packs
    raise AttributeError(name)
