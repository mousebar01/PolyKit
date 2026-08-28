"""Image background removal process node.

The processor uses rembg's local ONNX models, with ``isnet-anime`` as the
default because it preserves clean illustrated character silhouettes better
than a photoreal-oriented model. It follows PolyKit's line-delimited process
protocol and always writes a durable PNG with an alpha channel.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path


_MODELS = {"isnet-anime", "birefnet-general", "bria-rmbg", "u2net"}


def emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": percent, "label": label})


def done(path: Path) -> None:
    emit({"type": "done", "result": {"filePath": str(path)}})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _slug(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+", "_", value).strip("_").lower()
    return result[:48] or "image"


def _output_path(workspace_dir: str, input_path: Path) -> Path:
    output_dir = Path(workspace_dir) / "Workflows"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return output_dir / f"{_slug(input_path.stem)}_{stamp}_{uuid.uuid4().hex[:8]}_cutout.png"


def main() -> None:
    raw = sys.stdin.readline()
    data = json.loads(raw)
    input_data = data.get("input") or {}
    params = data.get("params") or {}
    workspace_dir = str(data.get("workspaceDir") or "")
    input_raw = input_data.get("filePath")
    input_path = Path(str(input_raw)) if input_raw else None
    if input_path is None or not input_path.is_file():
        error(f"image-background-remover: input image not found: {input_raw}")
        return

    model_name = str(params.get("model") or "isnet-anime")
    if model_name not in _MODELS:
        error(f"image-background-remover: unsupported segmentation model '{model_name}'")
        return

    try:
        from PIL import Image
        from rembg import new_session, remove
    except ImportError as exc:
        error(
            "image-background-remover: rembg and Pillow are required. "
            f"Install the image processing dependencies ({exc})."
        )
        return

    output_path = _output_path(workspace_dir, input_path)
    try:
        progress(5, "Loading segmentation model…")
        session = new_session(model_name)
        progress(20, "Removing background…")
        with Image.open(input_path) as source:
            rgba = source.convert("RGBA")
            result = remove(rgba, session=session, post_process_mask=True)
            if isinstance(result, Image.Image):
                output = result.convert("RGBA")
            else:
                from io import BytesIO
                output = Image.open(BytesIO(result)).convert("RGBA")
            progress(85, "Writing transparent PNG…")
            output.save(output_path, format="PNG")
            output.close()
        progress(100, "Done")
        done(output_path)
    except Exception as exc:
        error(f"image-background-remover: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"image-background-remover: {exc}")
