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
import math
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


def _run_material_region(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Extract localized material evidence from a normalized image region."""
    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for material region analysis: {exc}") from exc

    def bounded_fraction(name: str, default: float) -> float:
        try:
            value = float(params.get(name, default) or default)
        except (TypeError, ValueError):
            value = default
        return max(0.0, min(1.0, value))

    x, y = bounded_fraction("x", 0.0), bounded_fraction("y", 0.0)
    region_width = bounded_fraction("width", 1.0)
    region_height = bounded_fraction("height", 1.0)
    region_width = max(0.001, min(1.0 - x, region_width))
    region_height = max(0.001, min(1.0 - y, region_height))
    material_id = str(params.get("material_id") or "material-region").strip()[:120] or "material-region"
    color_count = _bounded_int(params.get("colors", 5), 5, 2, 8)
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_material-region.png"
    report_path = workspace_dir / f"{token}_material-region.json"

    with Image.open(input_path) as source:
        source_rgb = source.convert("RGB")
        left = max(0, min(source_rgb.width - 1, round(x * source_rgb.width)))
        top = max(0, min(source_rgb.height - 1, round(y * source_rgb.height)))
        right = max(left + 1, min(source_rgb.width, round((x + region_width) * source_rgb.width)))
        bottom = max(top + 1, min(source_rgb.height, round((y + region_height) * source_rgb.height)))
        crop = source_rgb.crop((left, top, right, bottom))
        crop.save(output_path, format="PNG")
        gray = crop.convert("L")
        luminance_stats = ImageStat.Stat(gray)
        edge_mean = float(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0])
        pixels = list(crop.getdata())
        saturation_mean = 0.0
        for red, green, blue in pixels:
            _hue, saturation, _value = colorsys.rgb_to_hsv(red / 255.0, green / 255.0, blue / 255.0)
            saturation_mean += saturation
        saturation_mean /= max(1, len(pixels))
        quantized = crop.quantize(colors=color_count, method=Image.Quantize.MEDIANCUT)
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
            palette.append({
                "rank": rank,
                "hex": "#%02x%02x%02x" % rgb,
                "rgb": list(rgb),
                "share": round(count / total, 6),
            })

    report = {
        "schemaVersion": 1,
        "kind": "polykit.material-region",
        "status": "needs_visual_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "materialId": material_id,
        "sourceImage": {"name": input_path.name, "sha256": _sha256(input_path), "width": source_rgb.width, "height": source_rgb.height},
        "region": {
            "normalized": [round(x, 6), round(y, 6), round(region_width, 6), round(region_height, 6)],
            "pixels": [left, top, right, bottom],
            "coverage": round(((right - left) * (bottom - top)) / max(1, source_rgb.width * source_rgb.height), 6),
        },
        "metrics": {
            "luminanceMean": round(float(luminance_stats.mean[0]), 3),
            "luminanceStdDev": round(float(luminance_stats.stddev[0]), 3),
            "saturationMean": round(saturation_mean, 6),
            "edgeMean": round(edge_mean, 3),
        },
        "palette": palette,
        "pbrEvidence": {
            "baseColor": {"source": "image-palette", "confidence": 0.4 if palette else 0.0},
            "roughness": {"source": "not-observable-from-single-crop", "confidence": 0.0},
            "metallic": {"source": "not-observable-from-single-crop", "confidence": 0.0},
            "normal": {"source": "not-observable-from-single-crop", "confidence": 0.0},
        },
        "reviewNotes": [
            "The crop provides localized color and frequency evidence; it is not an inverse-rendered PBR estimate.",
            "Confirm the region belongs to the intended component before assigning base color, roughness, metallic, or normal values.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "material-region",
            "schema_version": 1,
            "status": report["status"],
            "material_id": material_id,
            "region_coverage": report["region"]["coverage"],
            "report": report_path.name,
        },
    }


def _run_landmark_guide(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for landmark guide: {exc}") from exc

    subject_type = str(params.get("subject_type") or "character").strip()[:32] or "character"
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_landmark-guide.png"
    report_path = workspace_dir / f"{token}_landmark-guide.json"
    guides = {
        "head_top": (0.50, 0.10),
        "hairline": (0.50, 0.24),
        "eye_line": (0.50, 0.40),
        "nose_base": (0.50, 0.57),
        "mouth_line": (0.50, 0.68),
        "chin": (0.50, 0.80),
        "shoulder_line": (0.50, 0.86),
        "left_shoulder": (0.28, 0.86),
        "right_shoulder": (0.72, 0.86),
        "left_hip": (0.37, 0.98),
        "right_hip": (0.63, 0.98),
    }
    with Image.open(input_path) as source:
        width, height = source.size
        overlay = source.convert("RGBA")
        draw = ImageDraw.Draw(overlay, "RGBA")
        line_width = max(1, min(width, height) // 320)
        for fraction in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
            x = int(width * fraction)
            y = int(height * fraction)
            draw.line((x, 0, x, height - 1), fill=(148, 163, 184, 105), width=1)
            draw.line((0, y, width - 1, y), fill=(148, 163, 184, 105), width=1)
        for name, (x_fraction, y_fraction) in guides.items():
            x = int(round(width * x_fraction))
            y = int(round(height * y_fraction))
            if name.endswith("_line"):
                draw.line((0, y, width - 1, y), fill=(34, 211, 238, 215), width=line_width)
            else:
                radius = max(3, min(width, height) // 80)
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(251, 191, 36, 230), width=line_width)
        overlay.save(output_path, format="PNG")

    report = {
        "schemaVersion": 1,
        "kind": "polykit.landmark-guide",
        "status": "needs_visual_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "subjectType": subject_type,
        "sourceImage": {
            "name": input_path.name,
            "sha256": _sha256(input_path),
            "width": width,
            "height": height,
        },
        "guide": {
            "grid": "10-percent",
            "landmarks": [
                {"id": name, "guide": [x, y], "x": None, "y": None, "confidence": 0.0, "status": "unreviewed"}
                for name, (x, y) in guides.items()
            ],
        },
        "reviewNotes": [
            "Guide positions are scaffolding only; replace them with measured normalized coordinates from the reference.",
            "Use the reviewed landmarks to drive head-unit proportions, feature placement, and pose-silhouette checks.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "landmark-guide",
            "schema_version": 1,
            "status": report["status"],
            "landmark_count": len(guides),
            "report": report_path.name,
        },
    }


def _run_camera_guide(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Emit a reviewable starting camera descriptor from image dimensions."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for camera guide: {exc}") from exc

    def bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(params.get(name, default) or default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    fov_supplied = params.get("fov_degrees") not in (None, "")
    distance_supplied = params.get("distance") not in (None, "")
    fov_degrees = bounded_float("fov_degrees", 35.0, 10.0, 120.0)
    distance = bounded_float("distance", 2.5, 0.01, 10000.0)
    yaw = bounded_float("yaw", 0.0, -180.0, 180.0)
    pitch = bounded_float("pitch", 0.0, -89.0, 89.0)
    roll = bounded_float("roll", 0.0, -180.0, 180.0)
    height_offset = bounded_float("height_offset", 0.0, -10000.0, 10000.0)
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_camera-guide.png"
    report_path = workspace_dir / f"{token}_camera-guide.json"

    with Image.open(input_path) as source:
        width, height = source.size
        aspect = round(width / height, 4) if height else 1.0
        if not fov_supplied:
            fov_degrees = 38.0 if aspect < 0.75 else 35.0
        overlay = source.convert("RGBA")
        draw = ImageDraw.Draw(overlay, "RGBA")
        center_x, center_y = width / 2.0, height / 2.0
        line_width = max(1, min(width, height) // 320)
        color = (34, 211, 238, 220)
        draw.line((center_x, 0, center_x, height - 1), fill=color, width=line_width)
        draw.line((0, center_y, width - 1, center_y), fill=color, width=line_width)
        margin_x = int(round(width * 0.1))
        margin_y = int(round(height * 0.1))
        draw.rectangle((margin_x, margin_y, width - 1 - margin_x, height - 1 - margin_y), outline=(251, 191, 36, 210), width=line_width)
        # Show a conservative perspective frustum hint without claiming that
        # the image itself contains enough evidence to calibrate a camera.
        half_fov = math.radians(fov_degrees / 2.0)
        frustum = max(0.05, min(0.45, math.tan(half_fov) / max(1.0, math.tan(math.radians(60.0)))) )
        top = center_y - height * frustum
        bottom = center_y + height * frustum
        draw.line((center_x, center_y, 0, top), fill=(248, 113, 113, 190), width=line_width)
        draw.line((center_x, center_y, width - 1, top), fill=(248, 113, 113, 190), width=line_width)
        draw.line((center_x, center_y, 0, bottom), fill=(248, 113, 113, 190), width=line_width)
        draw.line((center_x, center_y, width - 1, bottom), fill=(248, 113, 113, 190), width=line_width)
        overlay.save(output_path, format="PNG")

    camera = {
        "version": "1.0",
        "sourceImage": input_path.name,
        "method": "heuristic-default-guess",
        "imageWidth": width,
        "imageHeight": height,
        "fovDegrees": {"value": round(fov_degrees, 2), "source": "user-supplied" if fov_supplied else "default-guess", "agentFill": not fov_supplied},
        "aspect": {"value": aspect, "source": "image-dimensions", "agentFill": False},
        "orientation": {
            "yawDegrees": {"value": round(yaw, 3), "source": "user-supplied" if params.get("yaw") not in (None, "") else "placeholder", "agentFill": params.get("yaw") in (None, "")},
            "pitchDegrees": {"value": round(pitch, 3), "source": "user-supplied" if params.get("pitch") not in (None, "") else "placeholder", "agentFill": params.get("pitch") in (None, "")},
            "rollDegrees": {"value": round(roll, 3), "source": "user-supplied" if params.get("roll") not in (None, "") else "placeholder", "agentFill": params.get("roll") in (None, "")},
        },
        "position": {
            "hint": [0.0, round(height_offset, 3), round(distance, 3)],
            "distance": {"value": round(distance, 3), "source": "user-supplied" if distance_supplied else "placeholder", "agentFill": not distance_supplied},
        },
        "confidence": 0.35,
        "limitations": [
            "No true camera calibration is performed; focal length, distortion, and 6-DoF pose are not recovered from pixels.",
            "Confirm the descriptor by rendering the fitted mesh and reviewing a silhouette/landmark overlay.",
        ],
    }
    report = {
        "schemaVersion": 1,
        "kind": "polykit.reference-camera",
        "status": "needs_visual_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceImage": {"name": input_path.name, "sha256": _sha256(input_path), "width": width, "height": height},
        "referenceCamera": camera,
        "overlay": output_path.name,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "reference-camera",
            "schema_version": 1,
            "status": report["status"],
            "fov_degrees": round(fov_degrees, 2),
            "aspect": aspect,
            "report": report_path.name,
        },
    }


def _run_projection_plan(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Package a validated projection-texture plan without baking mesh pixels."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for projection planning: {exc}") from exc

    projection_modes = {"perspective-camera-projection", "orthographic-front-projection", "triplanar-fallback"}
    unseen_strategies = {"mirror-symmetry", "palette-continue", "request-additional-view", "leave-unprojected"}
    projection_mode = str(params.get("projection_mode") or "perspective-camera-projection")
    if projection_mode not in projection_modes:
        projection_mode = "perspective-camera-projection"
    unseen_strategy = str(params.get("unseen_strategy") or "mirror-symmetry")
    if unseen_strategy not in unseen_strategies:
        unseen_strategy = "mirror-symmetry"
    mesh_id = str(params.get("mesh_id") or "target-mesh").strip()[:120] or "target-mesh"
    texture_size = _bounded_int(params.get("texture_size", 1024), 1024, 64, 8192)
    unseen_confidence = {
        "mirror-symmetry": 0.45,
        "palette-continue": 0.3,
        "request-additional-view": 0.0,
        "leave-unprojected": 0.0,
    }[unseen_strategy]
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_projection-plan.png"
    report_path = workspace_dir / f"{token}_projection-plan.json"

    with Image.open(input_path) as source:
        width, height = source.size
        overlay = source.convert("RGBA")
        draw = ImageDraw.Draw(overlay, "RGBA")
        line_width = max(1, min(width, height) // 320)
        draw.rectangle((0, 0, width - 1, height - 1), outline=(52, 211, 153, 230), width=line_width)
        draw.line((width / 2.0, 0, width / 2.0, height - 1), fill=(52, 211, 153, 160), width=line_width)
        draw.line((0, height / 2.0, width - 1, height / 2.0), fill=(52, 211, 153, 160), width=line_width)
        overlay.save(output_path, format="PNG")

    report = {
        "schemaVersion": 1,
        "kind": "polykit.projected-texture-plan",
        "status": "needs_runtime_bake",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "targetMeshId": mesh_id,
        "projectionMode": projection_mode,
        "textureSize": texture_size,
        "sourceImages": {"reference": input_path.name, "delit": None},
        "unseenRegionStrategy": {
            "mode": unseen_strategy,
            "confidence": unseen_confidence,
            "note": "Back, occluded, and underside regions are inferred rather than observed from this image.",
        },
        "bakeSteps": [
            "Load the target mesh and its UV layout in the renderer.",
            "Prefer a reviewed de-lit image as the projection source.",
            f"Construct a {projection_mode} camera from the reviewed referenceCamera block.",
            "Project only visible, front-facing surfaces within the camera frustum.",
            f"Handle unseen regions with the '{unseen_strategy}' strategy and record uncovered texels.",
            f"Rasterize the projection into a {texture_size}×{texture_size} UV texture.",
            "Render an overlay against the reference before accepting the bake.",
        ],
        "runtimeApproach": "Three.js projective ShaderMaterial or an equivalent camera-space projection shader must perform the actual sampling and UV bake.",
        "limitations": [
            "This node validates and records the plan; it does not sample a mesh, rasterize UVs, or write a baked texture.",
            "Camera accuracy and unseen-region quality must be reviewed before using the plan for likeness-critical work.",
        ],
        "overlay": output_path.name,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "projected-texture-plan",
            "schema_version": 1,
            "status": report["status"],
            "projection_mode": projection_mode,
            "texture_size": texture_size,
            "report": report_path.name,
        },
    }


def _run_reference_compare(reference_path: Path, candidate_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Create a deterministic reference/candidate contact sheet and metrics."""
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for reference comparison: {exc}") from exc

    threshold = _bounded_int(params.get("pixel_threshold", 16), 16, 0, 255)
    cutoff = max(1, threshold)
    token = f"{_slug(reference_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_reference-compare.png"
    report_path = workspace_dir / f"{token}_reference-compare.json"

    with Image.open(reference_path) as reference_source, Image.open(candidate_path) as candidate_source:
        reference = reference_source.convert("RGB")
        candidate_original_size = candidate_source.size
        candidate = candidate_source.convert("RGB")
        resized = candidate.size != reference.size
        if resized:
            candidate = candidate.resize(reference.size, Image.Resampling.LANCZOS)
        difference = ImageChops.difference(reference, candidate)
        gray_difference = difference.convert("L")
        statistics = ImageStat.Stat(difference)
        extrema = difference.getextrema()
        histogram = gray_difference.histogram()
        changed_pixels = sum(count for index, count in enumerate(histogram) if index >= cutoff)
        total_pixels = max(1, reference.width * reference.height)
        mean_absolute_error = sum(float(value) for value in statistics.mean) / (len(statistics.mean) * 255.0)
        heatmap = ImageOps.colorize(gray_difference, black=(15, 23, 42), white=(239, 68, 68))
        canvas = Image.new("RGB", (reference.width * 3, reference.height), "white")
        canvas.paste(reference, (0, 0))
        canvas.paste(candidate, (reference.width, 0))
        canvas.paste(heatmap, (reference.width * 2, 0))
        draw = ImageDraw.Draw(canvas)
        for index in range(3):
            left = index * reference.width
            draw.rectangle((left, 0, left + reference.width - 1, reference.height - 1), outline=(15, 23, 42), width=max(1, min(reference.size) // 320))
        canvas.save(output_path, format="PNG")

    report = {
        "schemaVersion": 1,
        "kind": "polykit.reference-compare",
        "status": "pass" if mean_absolute_error == 0.0 else "needs_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "reference": {"name": reference_path.name, "sha256": _sha256(reference_path), "width": reference.width, "height": reference.height},
        "candidate": {"name": candidate_path.name, "sha256": _sha256(candidate_path), "originalWidth": candidate_original_size[0], "originalHeight": candidate_original_size[1], "resizedToReference": resized},
        "metrics": {
            "meanAbsoluteError": round(mean_absolute_error, 6),
            "maxChannelDifference": max(int(pair[1]) for pair in extrema),
            "changedPixelRatio": round(changed_pixels / total_pixels, 6),
            "pixelThreshold": threshold,
        },
        "panels": ["reference", "candidate-resized-to-reference", "difference-heatmap"],
        "reviewNotes": [
            "Metrics measure pixel differences only; they do not establish semantic or geometric correctness.",
            "Use the heatmap to localize camera, silhouette, material, and lighting discrepancies before editing the mesh.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "reference-compare",
            "schema_version": 1,
            "status": report["status"],
            "changed_pixel_ratio": report["metrics"]["changedPixelRatio"],
            "report": report_path.name,
        },
    }


def _run_delight_albedo(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageMath
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for de-lighting: {exc}") from exc

    blur_radius = max(1.0, min(128.0, float(params.get("blur_radius", 24.0) or 24.0)))
    try:
        strength = float(params.get("strength", 0.85) or 0.85)
    except (TypeError, ValueError):
        strength = 0.85
    strength = max(0.0, min(1.0, strength))
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_delighted-albedo.png"
    report_path = workspace_dir / f"{token}_delighted-albedo.json"

    with Image.open(input_path) as source:
        source_rgb = source.convert("RGB")
        illumination = source_rgb.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        # Divide out low-frequency lighting, then restore a mid-range exposure.
        corrected_channels = []
        for source_channel, illumination_channel in zip(source_rgb.split(), illumination.split()):
            # Pillow 12 removed ImageChops.divide. ImageMath's lambda evaluator
            # keeps the operation dependency-free and works on single-band data.
            divided = ImageMath.lambda_eval(
                lambda operands: (operands["source"] * 255) / (operands["illumination"] + 1),
                source=source_channel,
                illumination=illumination_channel,
            )
            corrected_channels.append(divided.convert("L"))
        corrected = Image.merge("RGB", corrected_channels)
        corrected = ImageEnhance.Brightness(corrected).enhance(0.5)
        albedo = Image.blend(source_rgb, corrected, strength)
        albedo.save(output_path, format="PNG")

    report = {
        "schemaVersion": 1,
        "kind": "polykit.delighted-albedo",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceImage": {"name": input_path.name, "sha256": _sha256(input_path), "width": source_rgb.width, "height": source_rgb.height},
        "settings": {"blurRadius": blur_radius, "strength": strength, "method": "low-frequency-division"},
        "output": {"name": output_path.name, "format": "png"},
        "reviewNotes": [
            "This is a deterministic approximation that suppresses broad illumination; it does not recover physically correct albedo from a photograph.",
            "Inspect highlights, shadows, and color drift before using the result for projection texturing.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "delighted-albedo",
            "schema_version": 1,
            "status": "pass",
            "strength": strength,
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
    node_id = str(params.get("_node_id") or "detail-inventory")
    input_raw = input_data.get("filePath")
    input_path = Path(str(input_raw)) if input_raw else None
    workspace_dir = Path(str(data.get("workspaceDir") or ""))
    try:
        progress(5, "Reading reference image…")
        if node_id == "reference-compare":
            raw_paths = input_data.get("filePaths")
            paths = [Path(str(value)) for value in raw_paths] if isinstance(raw_paths, list) else []
            if len(paths) != 2 or any(not path.is_file() for path in paths):
                error(f"reference-evidence: comparison requires exactly two image files: {raw_paths}")
                return
            progress(20, "Comparing reference and candidate…")
            result = _run_reference_compare(paths[0], paths[1], workspace_dir, params)
        elif input_path is None or not input_path.is_file():
            error(f"reference-evidence: input image not found: {input_raw}")
            return
        elif node_id == "reference-quality":
            progress(20, "Measuring reference quality…")
            result = _run_reference_quality(input_path, workspace_dir, params)
        elif node_id == "material-palette":
            progress(20, "Extracting material palette…")
            result = _run_material_palette(input_path, workspace_dir, params)
        elif node_id == "material-region":
            progress(20, "Analyzing material region…")
            result = _run_material_region(input_path, workspace_dir, params)
        elif node_id == "landmark-guide":
            progress(20, "Drawing landmark guide…")
            result = _run_landmark_guide(input_path, workspace_dir, params)
        elif node_id == "camera-guide":
            progress(20, "Estimating reference camera…")
            result = _run_camera_guide(input_path, workspace_dir, params)
        elif node_id == "projection-plan":
            progress(20, "Validating texture projection plan…")
            result = _run_projection_plan(input_path, workspace_dir, params)
        elif node_id == "delight-albedo":
            progress(20, "Estimating illumination…")
            result = _run_delight_albedo(input_path, workspace_dir, params)
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
