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
import colorsys
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


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _histogram_quantile(histogram: list[int], total: int, fraction: float) -> int:
    target = max(1, int(total * max(0.0, min(1.0, fraction))))
    seen = 0
    for value, count in enumerate(histogram):
        seen += count
        if seen >= target:
            return value
    return 255


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


def _run_reference_quality(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageStat
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for reference quality: {exc}") from exc

    min_width = _bounded_int(params.get("min_width", 512), 512, 32, 16384)
    min_height = _bounded_int(params.get("min_height", 512), 512, 32, 16384)
    min_contrast = max(0.0, min(128.0, float(params.get("min_contrast", 12.0) or 12.0)))
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = workspace_dir / f"{token}_reference-quality.png"
    report_path = workspace_dir / f"{token}_reference-quality.json"

    with Image.open(input_path) as source:
        width, height = source.size
        source_format = str(source.format or input_path.suffix.lstrip(".")).lower()
        source_mode = source.mode
        gray = source.convert("L")
        stats = ImageStat.Stat(gray)
        histogram = gray.histogram()
        total_pixels = width * height
        p05 = _histogram_quantile(histogram, total_pixels, 0.05)
        p95 = _histogram_quantile(histogram, total_pixels, 0.95)
        contrast = float(stats.stddev[0])
        edge_mean = float(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0])
        alpha = None
        if "A" in source.getbands():
            alpha_stats = ImageStat.Stat(source.getchannel("A"))
            alpha = {
                "mean": round(float(alpha_stats.mean[0]), 3),
                "min": int(alpha_stats.extrema[0][0]),
                "max": int(alpha_stats.extrema[0][1]),
            }

        issues: list[dict[str, Any]] = []
        if width < min_width or height < min_height:
            issues.append({
                "code": "insufficient_reference_resolution",
                "severity": "error",
                "message": f"Reference is {width}×{height}; minimum is {min_width}×{min_height}.",
            })
        if contrast < min_contrast:
            issues.append({
                "code": "low_reference_contrast",
                "severity": "warning",
                "message": f"Luminance standard deviation is {contrast:.2f}; minimum is {min_contrast:.2f}.",
            })
        dynamic_range = p95 - p05
        if dynamic_range < min_contrast * 2:
            issues.append({
                "code": "low_reference_dynamic_range",
                "severity": "warning",
                "message": f"The 5–95% luminance range is {dynamic_range}; the image may hide surface detail.",
            })
        status = issues[0]["code"] if issues and issues[0]["severity"] == "error" else (
            issues[0]["code"] if issues else "pass"
        )
        overlay = source.convert("RGBA")
        color = (248, 113, 113, 240) if any(item["severity"] == "error" for item in issues) else (
            (251, 191, 36, 240) if issues else (52, 211, 153, 240)
        )
        border = max(2, min(width, height) // 128)
        draw = ImageDraw.Draw(overlay, "RGBA")
        for offset in range(border):
            draw.rectangle((offset, offset, width - 1 - offset, height - 1 - offset), outline=color)
        overlay.save(overlay_path, format="PNG")

    report = {
        "schemaVersion": 1,
        "kind": "polykit.reference-quality",
        "status": status,
        "sourceImage": {
            "name": input_path.name,
            "sha256": _sha256(input_path),
            "format": source_format,
            "mode": source_mode,
            "width": width,
            "height": height,
            "megapixels": round((width * height) / 1_000_000, 4),
            "bytes": input_path.stat().st_size,
        },
        "metrics": {
            "luminanceMean": round(float(stats.mean[0]), 3),
            "luminanceStdDev": round(contrast, 3),
            "luminanceP05": p05,
            "luminanceP95": p95,
            "dynamicRange": dynamic_range,
            "edgeMean": round(edge_mean, 3),
            "alpha": alpha,
        },
        "thresholds": {
            "minWidth": min_width,
            "minHeight": min_height,
            "minContrast": min_contrast,
        },
        "issues": issues,
        "recommendations": [
            "Use a higher-resolution reference or a closer crop before judging small identity details."
            if any(item["code"] == "insufficient_reference_resolution" for item in issues)
            else "Resolution is above the configured minimum.",
            "Capture more even lighting or a higher-contrast reference before estimating material differences."
            if any(item["code"] in {"low_reference_contrast", "low_reference_dynamic_range"} for item in issues)
            else "Contrast and dynamic range are suitable for the configured gate.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(overlay_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "reference-quality",
            "schema_version": 1,
            "status": status,
            "issue_count": len(issues),
            "report": report_path.name,
        },
    }


def _run_material_palette(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for material palette: {exc}") from exc

    color_count = _bounded_int(params.get("colors", 8), 8, 4, 12)
    sample_size = _bounded_int(params.get("sample_size", 512), 512, 64, 2048)
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_material-palette.png"
    report_path = workspace_dir / f"{token}_material-palette.json"

    with Image.open(input_path) as source:
        source_format = str(source.format or input_path.suffix.lstrip(".")).lower()
        source_mode = source.mode
        source_rgb = source.convert("RGB")
        sample = source_rgb.copy()
        sample.thumbnail((sample_size, sample_size), Image.Resampling.LANCZOS)
        quantized = sample.quantize(colors=color_count, method=Image.Quantize.MEDIANCUT)
        raw_colors = quantized.getcolors(maxcolors=color_count * 4) or []
        raw_colors.sort(key=lambda item: item[0], reverse=True)
        palette_data = quantized.getpalette() or []
        total = max(1, sum(count for count, _index in raw_colors))
        palette: list[dict[str, Any]] = []
        for rank, (count, index) in enumerate(raw_colors[:color_count], start=1):
            start = int(index) * 3
            rgb = tuple(int(max(0, min(255, value))) for value in palette_data[start:start + 3])
            if len(rgb) != 3:
                continue
            red, green, blue = rgb
            hue, saturation, value = colorsys.rgb_to_hsv(red / 255.0, green / 255.0, blue / 255.0)
            palette.append({
                "rank": rank,
                "hex": "#%02x%02x%02x" % rgb,
                "rgb": [red, green, blue],
                "share": round(count / total, 6),
                "pixels": int(count),
                "hsv": [round(hue * 360.0, 3), round(saturation, 6), round(value, 6)],
                "luminance": round((0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255.0, 6),
            })
        swatch_height = 96
        board = Image.new("RGB", (max(source_rgb.width, color_count * 96), source_rgb.height + swatch_height), "white")
        board.paste(source_rgb, (0, 0))
        draw = ImageDraw.Draw(board)
        swatch_width = board.width / max(1, len(palette))
        for index, item in enumerate(palette):
            left = int(index * swatch_width)
            right = int((index + 1) * swatch_width)
            rgb = tuple(item["rgb"])
            draw.rectangle((left, source_rgb.height, max(left, right - 1), board.height - 1), fill=rgb)
            # Keep the board useful without requiring a font package; the JSON
            # sidecar is the canonical machine-readable label for each swatch.
            draw.line((left, source_rgb.height, left, board.height - 1), fill=(255, 255, 255), width=2)
        board.save(output_path, format="PNG")

    report = {
        "schemaVersion": 1,
        "kind": "polykit.material-palette",
        "status": "pass" if palette else "needs_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceImage": {
            "name": input_path.name,
            "sha256": _sha256(input_path),
            "format": source_format,
            "mode": source_mode,
            "width": source_rgb.width,
            "height": source_rgb.height,
        },
        "sampling": {
            "maxDimension": sample_size,
            "sampleWidth": sample.width,
            "sampleHeight": sample.height,
            "requestedColors": color_count,
        },
        "palette": palette,
        "reviewNotes": [
            "Palette colors are image evidence, not calibrated PBR values or a substitute for material-node inspection.",
            "Use color shares to prioritize material regions, then validate roughness, metallic, and normal response separately.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "material-palette",
            "schema_version": 1,
            "status": report["status"],
            "color_count": len(palette),
            "report": report_path.name,
        },
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
        node_id = str(params.get("_node_id") or "detail-inventory")
        if node_id == "reference-quality":
            progress(20, "Measuring reference quality…")
            result = _run_reference_quality(input_path, workspace_dir, params)
        elif node_id == "material-palette":
            progress(20, "Extracting material palette…")
            result = _run_material_palette(input_path, workspace_dir, params)
        else:
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
