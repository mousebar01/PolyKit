"""Small, dependency-free glTF skin metadata reader.

The asset library must be able to distinguish an ordinary mesh from a rigged
mesh without loading the full scene through trimesh/Open3D.  GLB keeps its
JSON description in the first chunk, while .gltf is already JSON, so checking
the structural skin markers is both cheap and safe.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any


_GLB_HEADER = struct.Struct("<4sII")
_GLB_CHUNK = struct.Struct("<II")
_JSON_CHUNK = 0x4E4F534A  # ASCII "JSON" in little-endian uint32 form.


def _read_gltf_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.suffix.lower() == ".gltf":
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None

        with path.open("rb") as stream:
            header = stream.read(_GLB_HEADER.size)
            if len(header) != _GLB_HEADER.size:
                return None
            magic, version, total_length = _GLB_HEADER.unpack(header)
            if magic != b"glTF" or version != 2 or total_length < _GLB_HEADER.size:
                return None

            # The JSON chunk is required to be the first GLB chunk.  We still
            # walk chunks defensively so malformed files fail closed.
            remaining = total_length - _GLB_HEADER.size
            while remaining >= _GLB_CHUNK.size:
                chunk_length, chunk_type = _GLB_CHUNK.unpack(stream.read(_GLB_CHUNK.size))
                remaining -= _GLB_CHUNK.size
                if chunk_length > remaining:
                    return None
                chunk = stream.read(chunk_length)
                remaining -= chunk_length
                if chunk_type == _JSON_CHUNK:
                    payload = json.loads(chunk.rstrip(b" \t\r\n").decode("utf-8"))
                    return payload if isinstance(payload, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError, struct.error, ValueError):
        return None
    return None


def has_skin_metadata(path: str | Path) -> bool:
    """Return whether a glTF/GLB contains a usable skin binding structure.

    This intentionally checks structure, not filenames or sidecars:
    ``skins[].joints`` plus a primitive carrying both ``JOINTS_0`` and
    ``WEIGHTS_0``.  Numeric weight validation belongs to the rigging adapter's
    runtime smoke test because it requires reading binary accessors.
    """
    source = Path(path)
    document = _read_gltf_json(source)
    if not document:
        return False

    skins = document.get("skins")
    nodes = document.get("nodes")
    meshes = document.get("meshes")
    if not isinstance(skins, list) or not skins:
        return False
    if not isinstance(nodes, list) or not nodes:
        return False
    if not isinstance(meshes, list):
        return False

    valid_skin = any(
        isinstance(skin, dict)
        and isinstance(skin.get("joints"), list)
        and len(skin["joints"]) > 0
        and all(isinstance(index, int) and 0 <= index < len(nodes) for index in skin["joints"])
        for skin in skins
    )
    if not valid_skin:
        return False

    for mesh in meshes:
        if not isinstance(mesh, dict):
            continue
        primitives = mesh.get("primitives")
        if not isinstance(primitives, list):
            continue
        for primitive in primitives:
            if not isinstance(primitive, dict):
                continue
            attributes = primitive.get("attributes")
            if isinstance(attributes, dict) and "JOINTS_0" in attributes and "WEIGHTS_0" in attributes:
                return True
    return False


__all__ = ["has_skin_metadata"]
