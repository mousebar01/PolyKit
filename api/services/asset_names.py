"""Central output-file naming for generated assets.

Convention: ``{slug}_{YYYYMMDD-HHMMSS}_{id8}[_tag]{ext}``

- ``slug`` — human-readable, derived from the input image / source stem
  (so ``my_robot.png`` produces ``my_robot_...glb``, like TRELLIS.2 naming
  outputs after the input).
- timestamp — sortable and unique enough for the workspace.
- ``id8`` — collision-safe random suffix.
- ``tag`` — optional pipeline stage (e.g. ``textured``).

Every writer that creates a workspace artifact (generation, upload,
optimize, workflow model nodes) should go through ``output_name`` so the
library stays consistent and recognizable.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

_CJK = "\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
_UNSAFE = re.compile(f"[^0-9A-Za-z{_CJK}]+")
_MAX_SLUG = 40


def slugify(name: str, fallback: str = "model") -> str:
    """Lowercase, keep CJK + alphanumerics, collapse separators, cap length."""
    slug = _UNSAFE.sub("_", str(name or "")).strip("_")
    slug = re.sub(r"_+", "_", slug).lower()[:_MAX_SLUG].strip("_")
    return slug or fallback


def output_name(stem: str, *, tag: str | None = None, ext: str = ".glb") -> str:
    """Build ``{slug}_{ts}_{id8}[_tag]{ext}`` from a source stem."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    parts = [slugify(stem), ts, uuid.uuid4().hex[:8]]
    if tag:
        parts.append(slugify(tag))
    ext = str(ext or "").strip()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    return f"{'_'.join(parts)}{ext}"
