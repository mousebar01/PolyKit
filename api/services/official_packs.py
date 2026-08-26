"""Official (bundled) node-pack sync.

ComfyUI ships its core nodes with the application and keeps third-party
extensions in ``custom_nodes/``.  PolyKit mirrors that split:

* **Official packs** live in the repository (``<repo>/node-packs/`` for model
  packs, ``out/builtin-node-packs/`` for process packs built from
  ``src/areas/workflows/nodes/*``).  Their code is version-controlled and
  reviewed like any other source file.
* **Runtime packs** live in ``NODE_PACKS_DIR`` (``~/.polykit/node-packs`` by
  default).  Third-party packs are installed there by the user/UI; official
  packs are *materialised* there by this module so the registry can execute
  them.

Sync is always a one-way copy from the bundled source into the runtime dir.
Code files are refreshed whenever the bundled copy is newer or differs;
runtime state (``venv/``, ``__pycache__/``, weights, node_modules) is
preserved.  A bundled pack may additionally carry its own runtime state
(``venv/``, ``provider/``, ``.upstream/``, ``.cache/``) to be self-contained:
those directories are *seeded* into the runtime dir on first sync when absent,
and never refreshed afterwards, so a machine-prepared environment always wins.
Packs synced this way carry a ``.polykit-official`` sentinel so tooling can
recognise them as managed/built-in.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import List, Optional

# Files that are always refreshed from the bundled copy.
_CODE_FILES = {"manifest.json", "generator.py", "separation.py", "glb_split.py", "setup.py", "README.md", "build_vendor.py"}
_CODE_DIRS = {"docs", "patch", "vendor"}
# Runtime state that is never overwritten by a sync.
_RUNTIME_DIRS = {"venv", "__pycache__", "node_modules", ".git"}
_RUNTIME_FILES = {"package-lock.json"}
# Runtime directories a bundled pack may carry (self-contained packs). They are
# seeded into the runtime dir only when absent — never refreshed, so a prepared
# environment on the target machine always wins.
_SEED_RUNTIME_DIRS = {"venv", "provider", ".upstream", ".cache"}
_SENTINEL = ".polykit-official"


def official_packs_dir() -> Optional[Path]:
    """Locate the repository's bundled official model packs.

    Resolution order: ``POLYKIT_OFFICIAL_PACKS_DIR`` env, then the repo layout
    (``<repo>/node-packs``) used by the web/headless server.  Returns None when
    no bundled directory exists (for example when model packs are installed
    from GitHub instead).
    """
    env = os.environ.get("POLYKIT_OFFICIAL_PACKS_DIR")
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    # api/services/official_packs.py -> api/services -> api -> <repo>
    repo_root = Path(__file__).resolve().parent.parent.parent
    p = repo_root / "node-packs"
    return p if p.is_dir() else None


def builtin_process_packs_dir() -> Optional[Path]:
    """Locate the built-in process packs (``out/builtin-node-packs``).

    These are compiled by ``scripts/build-builtins.mjs`` from
    ``src/areas/workflows/nodes/*``. Present in development and Web builds.
    """
    env = os.environ.get("POLYKIT_BUILTIN_PACKS_DIR")
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    repo_root = Path(__file__).resolve().parent.parent.parent
    p = repo_root / "out" / "builtin-node-packs"
    return p if p.is_dir() else None


def _write_sentinel(pack_dir: Path) -> None:
    (pack_dir / _SENTINEL).write_text("official\n", encoding="utf-8")


def _sync_model_pack(src: Path, dst: Path) -> bool:
    """Refresh a model pack's code files into the runtime dir. Returns True if
    anything changed."""
    changed = False
    for fname in _CODE_FILES:
        s = src / fname
        if not s.is_file():
            continue
        d = dst / fname
        if not d.exists() or d.read_bytes() != s.read_bytes():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            changed = True
    for dname in _CODE_DIRS:
        s = src / dname
        if not s.is_dir():
            continue
        d = dst / dname
        for f in s.rglob("*"):
            parts = set(f.parts)
            if "__pycache__" in parts or f.suffix in {".pyc", ".pyo"}:
                continue
            rel = f.relative_to(s)
            target = d / rel
            if not target.exists() or target.read_bytes() != f.read_bytes():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                changed = True
    # Seed self-contained runtime state (venv, provider, upstream, caches) from
    # the bundled pack when the runtime dir lacks it. Existing environments are
    # never touched, so a machine-prepared venv always wins over the bundle.
    for dname in sorted(_SEED_RUNTIME_DIRS):
        s = src / dname
        d = dst / dname
        if s.is_dir() and not d.exists():
            print(f"[OfficialPacks] Seeding '{dname}' into {dst.name} from bundled pack …")
            shutil.copytree(s, d)
            changed = True
    return changed


# Code files that, when changed, force a whole-directory re-sync of a process pack.
_PROCESS_CODE_FILES = ("manifest.json", "processor.js", "processor.py")


def _sync_process_pack(src: Path, dst: Path) -> bool:
    """Mirror a compiled process pack (JS/Python + node_modules) into the
    runtime dir. Whole-directory copy on change; node_modules is included so
    the pack runs standalone."""
    changed = False
    for fname in _PROCESS_CODE_FILES:
        s = src / fname
        if not s.is_file():
            continue
        d = dst / fname
        if not d.exists() or d.read_bytes() != s.read_bytes():
            changed = True
            break
    if not changed and not (dst / ".polykit-official").exists():
        changed = True
    if changed:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    return changed


def sync_official_packs(node_packs_dir: Path) -> List[str]:
    """Materialise official packs into ``node_packs_dir`` (created if needed).

    Returns the list of pack ids that were (re)synced.  Idempotent and safe to
    call on every server start.
    """
    if node_packs_dir is None:
        return []
    node_packs_dir.mkdir(parents=True, exist_ok=True)
    synced: List[str] = []

    official = official_packs_dir()
    if official is not None:
        for pack_dir in sorted(official.iterdir()):
            if not pack_dir.is_dir():
                continue
            manifest_path = pack_dir / "manifest.json"
            if not manifest_path.is_file():
                continue
            pack_id = pack_dir.name
            dst = node_packs_dir / pack_id
            try:
                if _sync_model_pack(pack_dir, dst):
                    synced.append(pack_id)
                _write_sentinel(dst)
            except OSError as exc:
                print(f"[OfficialPacks] WARNING: failed to sync '{pack_id}': {exc}")

    builtin = builtin_process_packs_dir()
    if builtin is not None:
        for pack_dir in sorted(builtin.iterdir()):
            if not pack_dir.is_dir():
                continue
            if not (pack_dir / "manifest.json").is_file():
                continue
            pack_id = pack_dir.name
            dst = node_packs_dir / pack_id
            try:
                if _sync_process_pack(pack_dir, dst):
                    synced.append(pack_id)
                _write_sentinel(dst)
            except OSError as exc:
                print(f"[OfficialPacks] WARNING: failed to sync '{pack_id}': {exc}")

    if synced:
        print(f"[OfficialPacks] Synced official packs: {', '.join(sorted(set(synced)))}")
    return synced


def is_official(pack_dir: Path) -> bool:
    return (pack_dir / _SENTINEL).exists()
