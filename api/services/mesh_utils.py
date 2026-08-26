"""Small shared helpers for workspace mesh files (GLB/GLTF)."""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Optional


def face_count(mesh_path: Path) -> Optional[int]:
    """Cheap triangle count from the GLB/GLTF header — no full mesh load.

    Returns None when the file isn't a parseable GLB/GLTF. Used to bound
    expensive operations (thumbnail/preview generation) before loading a
    multi-hundred-MB mesh into memory.
    """
    suffix = mesh_path.suffix.lower()
    try:
        if suffix == ".gltf":
            data = json.loads(mesh_path.read_text(encoding="utf-8", errors="replace"))
        elif suffix == ".glb":
            with open(mesh_path, "rb") as fh:
                header = fh.read(12)
                if len(header) < 12 or header[:4] != b"glTF":
                    return None
                length = struct.unpack("<I", header[8:12])[0]
                data = None
                while fh.tell() < length:
                    chunk = fh.read(8)
                    if len(chunk) < 8:
                        break
                    clen, ctype = struct.unpack("<II", chunk)
                    payload = fh.read(clen)
                    if ctype == 0x4E4F534A:  # JSON chunk
                        data = json.loads(payload)
                        break
            if data is None:
                return None
        else:
            return None
    except Exception:
        return None

    accessors = data.get("accessors", [])
    total = 0
    for mesh in data.get("meshes", []):
        for prim in mesh.get("primitives", []):
            index = prim.get("indices")
            count = None
            if isinstance(index, int) and 0 <= index < len(accessors):
                count = accessors[index].get("count")
            else:
                position = prim.get("attributes", {}).get("POSITION")
                if isinstance(position, int) and 0 <= position < len(accessors):
                    count = accessors[position].get("count")
            if count:
                total += int(count)
    return total // 3
