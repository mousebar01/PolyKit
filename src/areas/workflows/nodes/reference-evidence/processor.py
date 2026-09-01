"""Reference evidence process nodes.

The first node in this pack turns a reference image into a reviewable evidence
bundle.  It deliberately does not pretend to infer semantic details without a
vision model: it creates a deterministic image grid, crops each region, and a
machine-readable checklist that a later reviewer/model can fill in.

The output image is the normal workflow ``image`` artifact.  The JSON report
and region crops are returned as process sidecars, so the existing FastAPI
artifact publisher keeps ownership of their durable workspace paths.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DETAIL_TAXONOMY = (
    "gloss",
    "bevel",
    "fastener",
    "linework",
    "contour",
    "seam",
    "stitch",
    "stain",
    "scratch",
    "chip",
    "decal",
    "emissive",
    "hole",
    "groove",
    "ridge",
)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": percent, "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _slug(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+", "_", value)
    return result.strip("_").lower()[:48] or "reference"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _grid_size(params: dict[str, Any]) -> int:
    try:
        value = int(params.get("grid_size", 3))
    except (TypeError, ValueError):
        value = 3
    return max(2, min(5, value))


def _target_min_details(params: dict[str, Any]) -> int:
    try:
        value = int(params.get("target_min_details", 8) or 8)
    except (TypeError, ValueError):
        value = 8
    return max(1, min(64, value))


def _include_crops(params: dict[str, Any]) -> bool:
    value = params.get("include_crops", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _region_bounds(width: int, height: int, grid: int, row: int, col: int) -> tuple[int, int, int, int]:
    # Use integer boundaries that cover every source pixel exactly once.
    left = (col * width) // grid
    top = (row * height) // grid
    right = ((col + 1) * width) // grid
    bottom = ((row + 1) * height) // grid
    return left, top, max(left + 1, right), max(top + 1, bottom)


def _build_report(
    *,
    input_path: Path,
    width: int,
    height: int,
    grid: int,
    subject: str,
    overlay_name: str,
    crop_names: list[dict[str, Any]],
    target_min_details: int,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "kind": "polykit.detail-inventory",
        "status": "needs_visual_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "subject": subject,
        "sourceImage": {
            "name": input_path.name,
            "sha256": _sha256(input_path),
            "width": width,
            "height": height,
        },
        "scan": {
            "method": f"grid-{grid}x{grid}",
            "targetMinDetails": target_min_details,
            "overlay": overlay_name,
            "regions": crop_names,
        },
        "detailChecklist": [
            {
                "id": category,
                "category": category,
                "present": None,
                "confidence": 0.0,
                "evidence": [],
                "reviewStatus": "unreviewed",
            }
            for category in DETAIL_TAXONOMY
        ],
        "reviewNotes": [
            "Semantic presence is intentionally unreviewed; inspect the region crops before modeling.",
            "Map every confirmed detail to a concrete component, material, or procedural operation.",
        ],
    }


def _run_detail_inventory(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for reference evidence: {exc}") from exc

    grid = _grid_size(params)
    target_min_details = _target_min_details(params)
    subject = str(params.get("subject") or input_path.stem).strip()[:120] or input_path.stem
    include_crops = _include_crops(params)
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = workspace_dir / f"{token}_detail-grid.png"
    report_path = workspace_dir / f"{token}_detail-inventory.json"

    with Image.open(input_path) as source:
        image = source.convert("RGB")
        width, height = image.size
        overlay = image.copy()
        draw = ImageDraw.Draw(overlay, "RGBA")
        crop_names: list[dict[str, Any]] = []
        for row in range(grid):
            for col in range(grid):
                left, top, right, bottom = _region_bounds(width, height, grid, row, col)
                region_id = f"r{row + 1}c{col + 1}"
                draw.rectangle((left, top, right - 1, bottom - 1), outline=(35, 211, 238, 230), width=max(1, min(width, height) // 320))
                if include_crops:
                    crop_path = workspace_dir / f"{token}_{region_id}.png"
                    image.crop((left, top, right, bottom)).save(crop_path, format="PNG")
                    crop_name = crop_path.name
                else:
                    crop_name = None
                crop_names.append(
                    {
                        "id": region_id,
                        "row": row,
                        "column": col,
                        "bbox": [left, top, right, bottom],
                        "bboxNormalized": [left / width, top / height, right / width, bottom / height],
                        "crop": crop_name,
                    }
                )
        overlay.save(overlay_path, format="PNG")

    report = _build_report(
        input_path=input_path,
        width=width,
        height=height,
        grid=grid,
        subject=subject,
        overlay_name=overlay_path.name,
        crop_names=crop_names,
        target_min_details=target_min_details,
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sidecars = [report_path]
    if include_crops:
        sidecars.extend(workspace_dir / str(region["crop"]) for region in crop_names if region.get("crop"))
    return {
        "filePath": str(overlay_path),
        "sidecars": [str(path) for path in sidecars],
        "metadata": {
            "evidence_kind": "detail-inventory",
            "schema_version": 1,
            "status": report["status"],
            "region_count": len(crop_names),
            "detail_candidate_count": len(DETAIL_TAXONOMY),
            "report": report_path.name,
        },
    }


def main() -> None:
    raw = sys.stdin.readline()
    data = json.loads(raw)
    input_data = data.get("input") or {}
    params = data.get("params") or {}
    input_raw = input_data.get("filePath")
    input_path = Path(str(input_raw)) if input_raw else None
    if input_path is None or not input_path.is_file():
        error(f"reference-evidence: input image not found: {input_raw}")
        return

    workspace_dir = Path(str(data.get("workspaceDir") or ""))
    try:
        progress(5, "Reading reference image…")
        progress(20, "Building detail regions…")
        result = _run_detail_inventory(input_path, workspace_dir, params)
        progress(85, "Writing evidence bundle…")
        progress(100, "Evidence ready")
        emit({"type": "done", "result": result})
    except Exception as exc:
        error(f"reference-evidence: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"reference-evidence: {exc}")
