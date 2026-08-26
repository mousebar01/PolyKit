"""Filesystem-backed inventory of installed Node Pack manifests.

Inventory answers what packs/nodes are installed. It deliberately does not
load model code, create subprocesses or inspect accelerator runtime state.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from services.runtime_paths import runtime_paths


_OFFICIAL_SENTINEL = ".polykit-official"


def read_manifest(pack_dir: Path) -> dict:
    try:
        value = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return {}


def iter_installed_packs() -> Iterator[tuple[Path, dict]]:
    root = runtime_paths.node_packs
    if not root.is_dir():
        return
    for pack_dir in sorted(root.iterdir()):
        if not pack_dir.is_dir() or pack_dir.name.startswith("."):
            continue
        if (pack_dir / ".polykit-incomplete").exists():
            continue
        manifest = read_manifest(pack_dir)
        if manifest:
            yield pack_dir, manifest


def get_pack(pack_id: str) -> tuple[Path, dict] | None:
    pack_dir = runtime_paths.node_packs / pack_id
    if not pack_dir.is_dir():
        return None
    manifest = read_manifest(pack_dir)
    if not manifest:
        return None
    return pack_dir, manifest


def is_official(pack_dir: Path) -> bool:
    return (pack_dir / _OFFICIAL_SENTINEL).is_file()
