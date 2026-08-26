"""Texture-preserving GLB part splitter.

Pure-stdlib GLB reader/writer that splits a textured mesh into parts while
keeping the original UVs and the texture bitmap byte-identical.

Why a custom writer: trimesh 5.0 cannot embed texture images on GLB/GLTF
export (its exporter writes ``images: []``), and ``submesh`` drops UV/material
entirely. This module instead reuses the original BIN chunk verbatim — the
texture bytes stay untouched — and appends freshly reindexed geometry for each
part, so the UV -> texture sampling relationship is preserved exactly.

Supported input: a GLB whose target mesh has exactly one primitive (the common
shape for generated assets). Anything else raises ``UnsupportedGlbError`` and
the caller falls back to the geometry-only path.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np

_GLB_HEADER = struct.Struct("<4sII")
_CHUNK_HEADER = struct.Struct("<II")
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942

_COMPONENT_DTYPES = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_TYPE_COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}

_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_TRIANGLES = 4


class UnsupportedGlbError(ValueError):
    """Raised when a GLB cannot be split texture-preservingly."""


# --------------------------------------------------------------------------- #
# GLB container I/O                                                           #
# --------------------------------------------------------------------------- #

def read_glb(path: Path) -> tuple[dict, bytes]:
    """Return ``(gltf json, bin chunk bytes)`` for a binary GLB."""
    data = Path(path).read_bytes()
    if len(data) < 12:
        raise UnsupportedGlbError("File is too small to be a GLB")
    magic, version, length = _GLB_HEADER.unpack_from(data, 0)
    if magic != b"glTF" or version != 2:
        raise UnsupportedGlbError(f"Not a binary glTF 2.0 file (magic={magic!r}, version={version})")
    offset = 12
    gltf: dict | None = None
    bin_bytes = b""
    while offset + 8 <= len(data):
        clen, ctype = _CHUNK_HEADER.unpack_from(data, offset)
        payload = data[offset + 8 : offset + 8 + clen]
        if ctype == _JSON_CHUNK:
            gltf = json.loads(payload.rstrip(b" \x00").decode("utf-8"))
        elif ctype == _BIN_CHUNK:
            bin_bytes = payload
        offset += 8 + clen
    if gltf is None:
        raise UnsupportedGlbError("GLB has no JSON chunk")
    return gltf, bin_bytes


def write_glb(path: Path, gltf: dict, bin_bytes: bytes) -> None:
    """Write a binary GLB from a glTF JSON document and BIN bytes."""
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    bin_bytes = bytes(bin_bytes) + b"\x00" * ((4 - len(bin_bytes) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    out = bytearray()
    out += _GLB_HEADER.pack(b"glTF", 2, total)
    out += _CHUNK_HEADER.pack(len(json_bytes), _JSON_CHUNK)
    out += json_bytes
    out += _CHUNK_HEADER.pack(len(bin_bytes), _BIN_CHUNK)
    out += bin_bytes
    Path(path).write_bytes(bytes(out))


# --------------------------------------------------------------------------- #
# Accessor helpers                                                            #
# --------------------------------------------------------------------------- #

def _read_accessor(gltf: dict, bin_bytes: bytes, accessor_index: int) -> np.ndarray:
    """Read an accessor's data as ``(count, type_count)`` numpy array."""
    acc = gltf["accessors"][accessor_index]
    component_type = int(acc["componentType"])
    if component_type not in _COMPONENT_DTYPES:
        raise UnsupportedGlbError(f"Unsupported accessor componentType {component_type}")
    type_count = _TYPE_COUNTS[acc["type"]]
    dtype = np.dtype(_COMPONENT_DTYPES[component_type])
    if "bufferView" not in acc:
        raise UnsupportedGlbError("Sparse or inline accessors are not supported")
    bv = gltf["bufferViews"][acc["bufferView"]]
    if bv.get("buffer", 0) != 0:
        raise UnsupportedGlbError("External buffers are not supported")
    offset = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride", dtype.itemsize * type_count)
    count = int(acc["count"])
    if stride == dtype.itemsize * type_count:
        arr = np.frombuffer(bin_bytes, dtype=dtype, count=count * type_count, offset=offset)
        return arr.reshape(count, type_count)
    # Strided layout: read row by row.
    arr = np.empty((count, type_count), dtype=dtype)
    for index in range(count):
        row = np.frombuffer(bin_bytes, dtype=dtype, count=type_count, offset=offset + index * stride)
        arr[index] = row
    return arr


def _append_buffer_view(bin_out: bytearray, payload: bytes, *, target: int) -> dict:
    """Append *payload* to the new BIN buffer, 4-byte aligned."""
    while len(bin_out) % 4:
        bin_out.append(0)
    offset = len(bin_out)
    bin_out += payload
    return {"buffer": 0, "byteOffset": offset, "byteLength": len(payload), "target": target}


def _append_accessor(
    bin_out: bytearray,
    gltf: dict,
    values: np.ndarray,
    *,
    component_type: int,
    type_name: str,
    target: int,
    min_max: bool = False,
) -> int:
    """Append *values* to the BIN buffer and register accessor + bufferView."""
    dtype = np.dtype(_COMPONENT_DTYPES[component_type])
    raw = np.ascontiguousarray(values, dtype=dtype).tobytes()
    buffer_view = _append_buffer_view(bin_out, raw, target=target)
    gltf["bufferViews"].append(buffer_view)
    accessor: dict[str, Any] = {
        "bufferView": len(gltf["bufferViews"]) - 1,
        "componentType": component_type,
        "count": int(values.shape[0]),
        "type": type_name,
    }
    if min_max:
        accessor["min"] = [float(v) for v in values.min(axis=0)]
        accessor["max"] = [float(v) for v in values.max(axis=0)]
    gltf["accessors"].append(accessor)
    return len(gltf["accessors"]) - 1


def _subset_accessor(gltf: dict, bin_bytes: bytes, accessor_index: int, used: np.ndarray) -> np.ndarray:
    """Return the accessor rows referenced by *used* vertex indices."""
    return _read_accessor(gltf, bin_bytes, accessor_index)[used]


# --------------------------------------------------------------------------- #
# Texture-preserving split                                                    #
# --------------------------------------------------------------------------- #

def split_textured_glb(
    glb_path: Path,
    face_ids: list[int],
    output_path: Path,
    *,
    separation: float = 0.0,
) -> Path:
    """Split the first single-primitive mesh of *glb_path* by *face_ids*.

    Each part keeps the original vertex attributes (position, normal, UV,
    colors, tangent) and shares the original materials and texture bitmap.
    ``separation > 0`` spreads parts outward from the model center. Returns
    *output_path* on success; raises :class:`UnsupportedGlbError` otherwise.
    """
    gltf, bin_bytes = read_glb(glb_path)

    meshes = gltf.get("meshes") or []
    if len(meshes) != 1:
        raise UnsupportedGlbError(
            f"Texture-preserving split requires exactly one mesh, got {len(meshes)}"
        )
    primitives = meshes[0].get("primitives") or []
    if len(primitives) != 1:
        raise UnsupportedGlbError(
            f"Texture-preserving split requires a single primitive, got {len(primitives)}"
        )
    primitive = primitives[0]
    if primitive.get("mode", _TRIANGLES) != _TRIANGLES:
        raise UnsupportedGlbError("Only triangle primitives are supported")

    indices_accessor = primitive.get("indices")
    if indices_accessor is None:
        raise UnsupportedGlbError("Primitive without an index buffer is not supported")
    indices = _read_accessor(gltf, bin_bytes, int(indices_accessor)).reshape(-1)
    if len(indices) % 3 != 0:
        raise UnsupportedGlbError("Index count is not a multiple of three")
    face_count = len(indices) // 3

    labels = np.asarray(face_ids, dtype=np.int64)
    if labels.ndim != 1 or labels.size != face_count:
        raise UnsupportedGlbError(
            f"face_ids ({labels.size}) do not match the mesh face count ({face_count})"
        )
    part_labels = sorted(set(int(label) for label in labels.tolist() if label >= 0))
    if len(part_labels) < 1:
        raise UnsupportedGlbError("face_ids contain no part labels")

    # Original resource tables we keep unchanged (materials, textures, images).
    kept_materials = gltf.get("materials") or []
    kept_images = gltf.get("images") or []
    kept_textures = gltf.get("textures") or []
    kept_samplers = gltf.get("samplers") or []
    # The original BIN chunk is copied verbatim to the front of the new buffer,
    # so every original bufferView (including the texture image's) stays valid.
    # Keep them all in the output and append the fresh geometry after.
    original_buffer_views = gltf.get("bufferViews") or []
    new_gltf: dict[str, Any] = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 0}],  # patched after assembly
        "bufferViews": list(original_buffer_views),
        "accessors": [],
        "materials": kept_materials,
        "textures": kept_textures,
        "images": kept_images,
        "samplers": kept_samplers,
        "meshes": [],
        "nodes": [],
        "scenes": [{"nodes": []}],
        "scene": 0,
    }
    bin_out = bytearray(bin_bytes)  # original BIN first: texture bytes stay put

    # Per-part assembly.
    used_material = int(primitive.get("material", 0) or 0)
    attributes = primitive.get("attributes") or {}
    part_indices: dict[int, np.ndarray] = {}
    for label in part_labels:
        face_rows = np.nonzero(labels == label)[0]
        part_indices[label] = indices[(face_rows * 3)[:, None] + np.arange(3)].reshape(-1)

    # Deduplicate vertices per part: build used-vertex masks from the indices.
    vertex_count = int(_read_accessor(gltf, bin_bytes, int(attributes["POSITION"])).shape[0])
    part_vertex_maps: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for label in part_labels:
        flat = part_indices[label]
        used, inverse = np.unique(flat, return_inverse=True)
        part_vertex_maps[label] = (used, inverse)
        part_indices[label] = inverse.astype(np.uint32)

    positions = _read_accessor(gltf, bin_bytes, int(attributes["POSITION"]))
    centers = []
    for label in part_labels:
        used, _ = part_vertex_maps[label]
        centers.append(positions[used].mean(axis=0))
    centers = np.stack(centers) if centers else np.zeros((0, 3))
    model_center = centers.mean(axis=0)

    # POSITION accessor's original component type/type_name.
    position_acc = gltf["accessors"][int(attributes["POSITION"])]
    position_component = int(position_acc["componentType"])
    position_type = position_acc.get("type", "VEC3")

    for label in part_labels:
        used, inverse = part_vertex_maps[label]
        part_attributes: dict[str, int] = {}
        for semantic, acc_index in attributes.items():
            values = _subset_accessor(gltf, bin_bytes, int(acc_index), used)
            if separation and semantic == "POSITION":
                delta = (positions[used] - model_center) * separation
                values = values + delta
            if semantic == "POSITION":
                component = position_component
                type_name = position_type
            else:
                acc_meta = gltf["accessors"][int(acc_index)]
                component = int(acc_meta["componentType"])
                type_name = acc_meta.get("type", "SCALAR")
            part_attributes[semantic] = _append_accessor(
                bin_out,
                new_gltf,
                values,
                component_type=component,
                type_name=type_name,
                target=_ARRAY_BUFFER,
                min_max=(semantic == "POSITION"),
            )

        part_indices_raw = part_indices[label]
        component = 5125 if len(used) > 65535 else 5123
        index_accessor = _append_accessor(
            bin_out,
            new_gltf,
            part_indices_raw,
            component_type=component,
            type_name="SCALAR",
            target=_ELEMENT_ARRAY_BUFFER,
        )
        mesh_index = len(new_gltf["meshes"])
        new_gltf["meshes"].append({
            "primitives": [{
                "attributes": part_attributes,
                "indices": index_accessor,
                "material": used_material,
                "mode": _TRIANGLES,
            }]
        })
        new_gltf["nodes"].append({"mesh": mesh_index, "name": f"part-{label}"})
        new_gltf["scenes"][0]["nodes"].append(mesh_index)

    new_gltf["buffers"][0]["byteLength"] = len(bin_out)
    write_glb(output_path, new_gltf, bytes(bin_out))
    return Path(output_path)
