"""Deterministic CPU-only executor used for headless control-plane testing.

This is deliberately not an inference approximation.  It writes a tiny,
valid GLB with a color derived from the input bytes so API, queue, upload,
cancel, export, and browser integration can be tested without CUDA.
"""
from __future__ import annotations

import hashlib
import json
import struct
import threading
import uuid
from pathlib import Path
from typing import Callable, Optional

from services.generators.base import BaseGenerator, GenerationCancelled


class FakeGenerator(BaseGenerator):
    MODEL_ID = "fake"
    DISPLAY_NAME = "Fake CPU Executor"
    VRAM_GB = 0

    def is_downloaded(self) -> bool:
        return True

    def load(self) -> None:
        self._model = True

    def generate(
        self,
        image_bytes: bytes,
        params: dict,
        progress_cb: Callable[[int, str], None],
        cancel_event: Optional[threading.Event] = None,
    ) -> Path:
        if cancel_event and cancel_event.is_set():
            raise GenerationCancelled()

        progress_cb(20, "Fake executor: preparing test mesh")
        digest = hashlib.sha256(image_bytes).digest()
        if cancel_event and cancel_event.is_set():
            raise GenerationCancelled()

        output_dir = Path(self.outputs_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"fake-{uuid.uuid4().hex[:12]}.glb"
        output_path.write_bytes(_triangle_glb(digest[:3]))
        progress_cb(100, "Fake executor: test mesh ready")
        return output_path


def _triangle_glb(rgb: bytes) -> bytes:
    """Return a minimal GLB containing one colored triangle."""
    vertices = struct.pack(
        "<9f",
        0.0, 0.0, 0.0,
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
    )
    indices = struct.pack("<3H", 0, 1, 2)
    binary = vertices + indices
    binary += b"\0" * ((4 - len(binary) % 4) % 4)

    color = [round(channel / 255, 6) for channel in rgb] + [1.0]
    document = {
        "asset": {"version": "2.0", "generator": "PolyKit fake executor"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{
            "primitives": [{
                "attributes": {"POSITION": 0},
                "indices": 1,
                "material": 0,
            }],
        }],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": color}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(vertices), "target": 34962},
            {"buffer": 0, "byteOffset": len(vertices), "byteLength": len(indices), "target": 34963},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [0, 0, 0],
                "max": [1, 1, 0],
            },
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
    }
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    total_length = 12 + 8 + len(json_chunk) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<I4s", len(json_chunk), b"JSON")
        + json_chunk
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )
