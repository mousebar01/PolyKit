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


def _hue_name(degrees: float) -> str:
    for upper, name in ((15, "red"), (45, "orange"), (70, "yellow"), (165, "green"), (195, "cyan"), (255, "blue"), (290, "violet"), (345, "magenta"), (360, "red")):
        if degrees < upper:
            return name
    return "red"


def _median_channel(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return int(ordered[len(ordered) // 2])


def _run_gradient_stops(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Extract band-median RGB stops from a localized reference region."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for gradient extraction: {exc}") from exc
    axis = str(params.get("axis") or "u").strip().lower()
    if axis not in {"u", "v"}:
        axis = "u"
    stop_count = _bounded_int(params.get("stops", 6), 6, 2, 16)
    def fraction(name: str, default: float) -> float:
        try:
            value = float(params.get(name, default) or default)
        except (TypeError, ValueError):
            value = default
        return max(0.0, min(1.0, value))
    x, y = fraction("x", 0.0), fraction("y", 0.0)
    region_width = max(0.001, min(1.0 - x, fraction("width", 1.0)))
    region_height = max(0.001, min(1.0 - y, fraction("height", 1.0)))
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_gradient-stops.png"
    report_path = workspace_dir / f"{token}_gradient-stops.json"
    with Image.open(input_path) as source:
        source_rgb = source.convert("RGB")
        left = max(0, min(source_rgb.width - 1, round(x * source_rgb.width)))
        top = max(0, min(source_rgb.height - 1, round(y * source_rgb.height)))
        right = max(left + 1, min(source_rgb.width, round((x + region_width) * source_rgb.width)))
        bottom = max(top + 1, min(source_rgb.height, round((y + region_height) * source_rgb.height)))
        crop = source_rgb.crop((left, top, right, bottom))
        span = crop.width if axis == "u" else crop.height
        stops: list[dict[str, Any]] = []
        for index in range(stop_count):
            start = (index * span) // stop_count
            end = span if index == stop_count - 1 else max(start + 1, ((index + 1) * span) // stop_count)
            samples: list[tuple[int, int, int]] = []
            for py in range(crop.height):
                for px in range(crop.width):
                    coordinate = px if axis == "u" else py
                    if start <= coordinate < end:
                        samples.append(crop.getpixel((px, py)))
            red = _median_channel([sample[0] for sample in samples])
            green = _median_channel([sample[1] for sample in samples])
            blue = _median_channel([sample[2] for sample in samples])
            hue, saturation, value = colorsys.rgb_to_hsv(red / 255.0, green / 255.0, blue / 255.0)
            hue_degrees = hue * 360.0
            stop: dict[str, Any] = {
                "t": round((index + 0.5) / stop_count, 4),
                "rgb": [red, green, blue],
                "hex": "#%02x%02x%02x" % (red, green, blue),
                "hsv": [round(hue_degrees, 2), round(saturation, 6), round(value, 6)],
                "hueName": _hue_name(hue_degrees),
            }
            if stop["hueName"] in {"violet", "magenta", "blue"} and blue > red and saturation > 0.15:
                stop["hueRisk"] = "blue-collapse"
                stop["suggestedRgb"] = [min(255, blue), max(green, int(blue * 0.25)), red]
            stops.append(stop)
        board_width = max(256, crop.width)
        board_height = 96
        board = Image.new("RGB", (board_width, board_height), "white")
        draw = ImageDraw.Draw(board)
        for index, stop in enumerate(stops):
            start = (index * board_width) // stop_count
            end = board_width if index == stop_count - 1 else ((index + 1) * board_width) // stop_count
            draw.rectangle((start, 0, max(start, end - 1), board_height - 1), fill=tuple(stop["rgb"]))
        board.save(output_path, format="PNG")
    zones: list[dict[str, Any]] = []
    for stop in stops:
        if zones and zones[-1]["hueName"] == stop["hueName"]:
            zones[-1]["tEnd"] = stop["t"]
        else:
            zones.append({"hueName": stop["hueName"], "tStart": stop["t"], "tEnd": stop["t"]})
    report = {
        "schemaVersion": 1,
        "kind": "polykit.gradient-stops",
        "status": "pass" if stops else "needs_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceImage": {"name": input_path.name, "sha256": _sha256(input_path), "width": source_rgb.width, "height": source_rgb.height},
        "region": {"normalized": [round(x, 6), round(y, 6), round(region_width, 6), round(region_height, 6)], "pixels": [left, top, right, bottom]},
        "axis": axis,
        "requestedStops": stop_count,
        "stops": stops,
        "hueZones": zones,
        "riskFlags": [stop for stop in stops if stop.get("hueRisk")],
        "reviewNotes": [
            "Band medians resist isolated highlights better than means, but the crop must still isolate one material or finish.",
            "Use hue-risk suggestions as review prompts; do not treat them as a tone-mapping correction guarantee.",
        ],
        "overlay": output_path.name,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "gradient-stops",
            "schema_version": 1,
            "status": report["status"],
            "stop_count": len(stops),
            "risk_count": len(report["riskFlags"]),
            "report": report_path.name,
        },
    }


def _run_pbr_evidence(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Derive reviewable, low-confidence PBR map evidence from one image."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageMath, ImageOps, ImageStat
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for PBR evidence: {exc}") from exc
    try:
        max_dimension = int(params.get("max_dimension", 1024) or 1024)
    except (TypeError, ValueError):
        max_dimension = 1024
    max_dimension = max(64, min(4096, max_dimension))
    try:
        blur_radius = float(params.get("ao_blur_radius", 6.0) or 6.0)
    except (TypeError, ValueError):
        blur_radius = 6.0
    blur_radius = max(1.0, min(64.0, blur_radius))
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_pbr-evidence.png"
    albedo_path = workspace_dir / f"{token}_albedo.png"
    roughness_path = workspace_dir / f"{token}_roughness.png"
    height_path = workspace_dir / f"{token}_height.png"
    normal_path = workspace_dir / f"{token}_normal.png"
    ao_path = workspace_dir / f"{token}_ao.png"
    report_path = workspace_dir / f"{token}_pbr-evidence.json"
    with Image.open(input_path) as source:
        image = source.convert("RGB")
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        gray = image.convert("L")
        illumination = image.filter(ImageFilter.GaussianBlur(radius=max(4.0, blur_radius * 2.0)))
        corrected_channels = []
        for source_channel, illumination_channel in zip(image.split(), illumination.split()):
            divided = ImageMath.lambda_eval(
                lambda operands: (operands["source"] * 255) / (operands["illumination"] + 1),
                source=source_channel,
                illumination=illumination_channel,
            )
            corrected_channels.append(ImageEnhance.Brightness(divided.convert("L")).enhance(0.5))
        # Keep a bounded de-lit approximation as the albedo evidence. The
        # source image remains available to reviewers as the canonical input.
        albedo = Image.merge("RGB", corrected_channels)
        height_map = ImageOps.autocontrast(gray)
        roughness_map = ImageOps.autocontrast(ImageOps.invert(gray.filter(ImageFilter.GaussianBlur(radius=1.5))))
        ao_map = ImageOps.autocontrast(ImageOps.invert(gray.filter(ImageFilter.GaussianBlur(radius=blur_radius))))
        sobel_x = gray.filter(ImageFilter.Kernel((3, 3), (-1, 0, 1, -2, 0, 2, -1, 0, 1), scale=8.0, offset=128))
        sobel_y = gray.filter(ImageFilter.Kernel((3, 3), (-1, -2, -1, 0, 0, 0, 1, 2, 1), scale=8.0, offset=128))
        normal_map = Image.merge("RGB", (sobel_x, sobel_y, Image.new("L", image.size, 255)))
        albedo.save(albedo_path, format="PNG")
        roughness_map.save(roughness_path, format="PNG")
        height_map.save(height_path, format="PNG")
        normal_map.save(normal_path, format="PNG")
        ao_map.save(ao_path, format="PNG")
        tile_width, tile_height = image.width, image.height
        canvas = Image.new("RGB", (tile_width * 4, tile_height), "#0f172a")
        for index, tile in enumerate((albedo, roughness_map.convert("RGB"), normal_map, ao_map.convert("RGB"))):
            canvas.paste(tile, (index * tile_width, 0))
        canvas.save(output_path, format="PNG")
        luminance_std = float(ImageStat.Stat(gray).stddev[0])
    report = {
        "schemaVersion": 1,
        "kind": "polykit.pbr-evidence",
        "status": "needs_visual_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceImage": {"name": input_path.name, "sha256": _sha256(input_path), "width": image.width, "height": image.height},
        "maps": {
            "albedo": {"file": albedo_path.name, "method": "low-frequency-division", "confidence": 0.25},
            "roughness": {"file": roughness_path.name, "method": "inverse-smoothed-luminance", "confidence": 0.15},
            "height": {"file": height_path.name, "method": "autocontrasted-luminance", "confidence": 0.2},
            "normal": {"file": normal_path.name, "space": "image-gradient-encoded", "confidence": 0.1},
            "ambientOcclusion": {"file": ao_path.name, "method": "inverse-low-frequency-luminance", "confidence": 0.1},
        },
        "sampling": {"maxDimension": max_dimension, "aoBlurRadius": blur_radius, "luminanceStdDev": round(luminance_std, 3)},
        "reviewNotes": [
            "These maps are image-derived evidence and are not calibrated inverse-rendered PBR channels.",
            "Normal is an image-gradient encoding, not a tangent-space bake; review all maps before using them in a material.",
        ],
        "contactSheet": output_path.name,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(albedo_path), str(roughness_path), str(height_path), str(normal_path), str(ao_path), str(report_path)],
        "metadata": {
            "evidence_kind": "pbr-evidence",
            "schema_version": 1,
            "status": report["status"],
            "map_count": len(report["maps"]),
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


def _camera_project(point: tuple[float, float, float], camera: list[float], width: int, height: int) -> tuple[float, float] | None:
    fov, yaw, pitch, roll, position_x, position_y, position_z = camera
    if not 10.0 <= fov <= 120.0:
        return None
    tangent = math.tan(math.radians(fov) / 2.0)
    if not math.isfinite(tangent) or abs(tangent) <= 1e-8:
        return None
    focal_length = (height / 2.0) / tangent
    translated_x = point[0] - position_x
    translated_y = point[1] - position_y
    translated_z = position_z - point[2]
    pitch_radians = math.radians(pitch)
    pitched_x = translated_x
    pitched_y = translated_y * math.cos(pitch_radians) - translated_z * math.sin(pitch_radians)
    pitched_z = translated_y * math.sin(pitch_radians) + translated_z * math.cos(pitch_radians)
    yaw_radians = math.radians(yaw)
    yawed_x = pitched_x * math.cos(yaw_radians) + pitched_z * math.sin(yaw_radians)
    yawed_y = pitched_y
    yawed_z = -pitched_x * math.sin(yaw_radians) + pitched_z * math.cos(yaw_radians)
    roll_radians = math.radians(roll)
    rolled_x = yawed_x * math.cos(roll_radians) - yawed_y * math.sin(roll_radians)
    rolled_y = yawed_x * math.sin(roll_radians) + yawed_y * math.cos(roll_radians)
    if yawed_z <= 1e-8:
        return None
    projected_x = (width / 2.0) + (focal_length * rolled_x / yawed_z)
    projected_y = (height / 2.0) - (focal_length * rolled_y / yawed_z)
    if not math.isfinite(projected_x) or not math.isfinite(projected_y):
        return None
    return projected_x, projected_y


def _camera_residuals(correspondences: list[dict[str, Any]], camera: list[float], width: int, height: int) -> list[float] | None:
    residuals: list[float] = []
    for item in correspondences:
        projected = _camera_project(tuple(item["world"]), camera, width, height)
        if projected is None:
            return None
        residuals.extend((projected[0] - item["observed"][0], projected[1] - item["observed"][1]))
    return residuals


def _camera_rms(residuals: list[float]) -> float:
    return math.sqrt(sum(value * value for value in residuals) / max(1, len(residuals) // 2))


def _solve_linear_system(matrix: list[list[float]], right_hand_side: list[float]) -> list[float] | None:
    dimension = len(right_hand_side)
    augmented = [row[:] + [right_hand_side[index]] for index, row in enumerate(matrix)]
    for column in range(dimension):
        pivot_row = max(range(column, dimension), key=lambda row: abs(augmented[row][column]))
        pivot = augmented[pivot_row][column]
        if abs(pivot) <= 1e-12:
            return None
        augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]
        for row in range(column + 1, dimension):
            factor = augmented[row][column] / pivot
            if factor == 0.0:
                continue
            for secondary in range(column, dimension + 1):
                augmented[row][secondary] -= factor * augmented[column][secondary]
    solution = [0.0] * dimension
    for row in range(dimension - 1, -1, -1):
        remainder = sum(augmented[row][column] * solution[column] for column in range(row + 1, dimension))
        solution[row] = (augmented[row][dimension] - remainder) / augmented[row][row]
    return solution


def _run_camera_fit(input_path: Path, correspondence_text: str, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Fit a seven-parameter camera from validated 3D-to-2D correspondences."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for camera fitting: {exc}") from exc
    try:
        raw = json.loads(correspondence_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("camera-fit correspondence text must be valid JSON") from exc
    if isinstance(raw, dict):
        raw = raw.get("correspondences")
    if not isinstance(raw, list) or len(raw) < 6:
        raise ValueError("camera-fit requires at least six correspondences")
    correspondences: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ValueError(f"camera-fit correspondence {index} must be an object")
        world = value.get("world")
        observed = value.get("observed", value.get("image"))
        if not isinstance(world, (list, tuple)) or len(world) != 3 or not isinstance(observed, (list, tuple)) or len(observed) != 2:
            raise ValueError(f"camera-fit correspondence {index} must contain world[3] and observed[2]")
        world_values = [float(item) for item in world]
        observed_values = [float(item) for item in observed]
        if not all(math.isfinite(item) for item in (*world_values, *observed_values)):
            raise ValueError(f"camera-fit correspondence {index} contains non-finite values")
        correspondences.append({
            "name": str(value.get("name") or f"landmark-{index + 1}")[:120],
            "world": world_values,
            "observed": observed_values,
        })

    with Image.open(input_path) as source:
        width, height = source.size
    def bounded(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(params.get(name, default) or default)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))
    camera = [
        bounded("initial_fov_degrees", 35.0, 10.0, 120.0),
        bounded("initial_yaw", 0.0, -180.0, 180.0),
        bounded("initial_pitch", 0.0, -89.0, 89.0),
        bounded("initial_roll", 0.0, -180.0, 180.0),
        bounded("initial_position_x", 0.0, -100000.0, 100000.0),
        bounded("initial_position_y", 0.0, -100000.0, 100000.0),
        bounded("initial_position_z", 2.5, 0.01, 100000.0),
    ]
    initial_residuals = _camera_residuals(correspondences, camera, width, height)
    if initial_residuals is None:
        raise ValueError("initial camera cannot project every correspondence")
    initial_error = _camera_rms(initial_residuals)
    try:
        maximum_iterations = int(params.get("maximum_iterations", 60) or 60)
    except (TypeError, ValueError):
        maximum_iterations = 60
    maximum_iterations = max(1, min(200, maximum_iterations))
    finite_steps = (1e-2, 1e-2, 1e-2, 1e-2, 1e-3, 1e-3, 1e-3)
    damping = 1e-3
    residuals = initial_residuals
    accepted_steps = 0
    rejected_steps = 0
    convergence = "max-iterations"
    for iteration in range(1, maximum_iterations + 1):
        jacobian: list[list[float]] = []
        for row in range(len(residuals)):
            jacobian.append([])
        valid_jacobian = True
        for parameter_index, step in enumerate(finite_steps):
            positive = camera[:]
            negative = camera[:]
            positive[parameter_index] += step
            negative[parameter_index] -= step
            positive_residuals = _camera_residuals(correspondences, positive, width, height)
            negative_residuals = _camera_residuals(correspondences, negative, width, height)
            if positive_residuals is None or negative_residuals is None:
                valid_jacobian = False
                break
            for row, (positive_value, negative_value) in enumerate(zip(positive_residuals, negative_residuals)):
                jacobian[row].append((positive_value - negative_value) / (2.0 * step))
        if not valid_jacobian:
            convergence = "stalled"
            break
        hessian = [[0.0 for _ in range(7)] for _ in range(7)]
        gradient = [0.0 for _ in range(7)]
        for row, residual in zip(jacobian, residuals):
            for column in range(7):
                gradient[column] += row[column] * residual
                for secondary in range(column, 7):
                    hessian[column][secondary] += row[column] * row[secondary]
        for column in range(7):
            for secondary in range(column):
                hessian[column][secondary] = hessian[secondary][column]
            hessian[column][column] += damping * max(hessian[column][column], 1.0)
        delta = _solve_linear_system(hessian, [-value for value in gradient])
        if delta is None:
            damping = min(damping * 10.0, 1e12)
            rejected_steps += 1
            continue
        candidate = [camera[index] + delta[index] for index in range(7)]
        candidate[0] = max(10.0, min(120.0, candidate[0]))
        candidate[1] = max(-180.0, min(180.0, candidate[1]))
        candidate[2] = max(-89.0, min(89.0, candidate[2]))
        candidate[3] = max(-180.0, min(180.0, candidate[3]))
        candidate[6] = max(0.01, min(100000.0, candidate[6]))
        candidate_residuals = _camera_residuals(correspondences, candidate, width, height)
        candidate_error = _camera_rms(candidate_residuals) if candidate_residuals is not None else float("inf")
        current_error = _camera_rms(residuals)
        if candidate_residuals is not None and candidate_error < current_error:
            camera, residuals = candidate, candidate_residuals
            damping = max(damping / 3.0, 1e-12)
            accepted_steps += 1
            if candidate_error <= 1e-5 or max(abs(value) for value in delta) <= 1e-8:
                convergence = "converged"
                break
        else:
            damping = min(damping * 10.0, 1e12)
            rejected_steps += 1
    final_error = _camera_rms(residuals)
    max_rms = bounded("max_rms_pixels", 1.0, 0.0, 100000.0)
    status = "pass" if final_error <= max_rms else "needs_review"
    token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_camera-fit.png"
    report_path = workspace_dir / f"{token}_camera-fit.json"
    with Image.open(input_path) as source:
        overlay = source.convert("RGBA")
        draw = ImageDraw.Draw(overlay, "RGBA")
        for item in correspondences:
            observed = item["observed"]
            projected = _camera_project(tuple(item["world"]), camera, width, height)
            radius = max(3, min(width, height) // 100)
            draw.ellipse((observed[0] - radius, observed[1] - radius, observed[0] + radius, observed[1] + radius), outline=(251, 191, 36, 240), width=2)
            if projected is not None:
                draw.ellipse((projected[0] - radius, projected[1] - radius, projected[0] + radius, projected[1] + radius), outline=(34, 211, 238, 240), width=2)
                draw.line((observed[0], observed[1], projected[0], projected[1]), fill=(248, 113, 113, 210), width=1)
        overlay.save(output_path, format="PNG")
    residual_report = []
    for item in correspondences:
        projected = _camera_project(tuple(item["world"]), camera, width, height)
        error_pixels = math.hypot(projected[0] - item["observed"][0], projected[1] - item["observed"][1]) if projected else float("inf")
        residual_report.append({"name": item["name"], "errorPixels": round(error_pixels, 6)})
    report = {
        "schemaVersion": 1,
        "kind": "polykit.reference-camera-fit",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceImage": {"name": input_path.name, "sha256": _sha256(input_path), "width": width, "height": height},
        "solver": {
            "method": "central-difference-damped-least-squares",
            "correspondenceCount": len(correspondences),
            "initialReprojectionErrorPixels": round(initial_error, 6),
            "finalReprojectionErrorPixels": round(final_error, 6),
            "maximumIterations": maximum_iterations,
            "iterations": iteration,
            "acceptedSteps": accepted_steps,
            "rejectedSteps": rejected_steps,
            "convergence": convergence,
        },
        "camera": {
            "fovDegrees": round(camera[0], 6),
            "yawDegrees": round(camera[1], 6),
            "pitchDegrees": round(camera[2], 6),
            "rollDegrees": round(camera[3], 6),
            "position": [round(value, 6) for value in camera[4:]],
        },
        "residuals": residual_report,
        "limitations": [
            "The fit holds the principal point at the image center and does not estimate lens distortion or sensor size.",
            "The result is only as reliable as the supplied 3D-to-2D correspondences; inspect the overlay before projection baking.",
        ],
        "overlay": output_path.name,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "reference-camera-fit",
            "schema_version": 1,
            "status": status,
            "final_rms_pixels": round(final_error, 6),
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


def _review_luma(image: Any, size: int = 64) -> list[float]:
    """Return a deterministic square Rec.709 luma grid for visual review."""
    resized = image.convert("RGB").resize((size, size), 2)  # PIL.Image.Resampling.BILINEAR
    return [
        (0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]) / 255.0
        for pixel in resized.getdata()
    ]


def _review_ssim(reference: list[float], candidate: list[float]) -> float:
    if len(reference) != len(candidate) or not reference:
        return 0.0
    mean_ref = sum(reference) / len(reference)
    mean_candidate = sum(candidate) / len(candidate)
    variance_ref = sum((value - mean_ref) ** 2 for value in reference) / len(reference)
    variance_candidate = sum((value - mean_candidate) ** 2 for value in candidate) / len(candidate)
    covariance = sum(
        (reference[index] - mean_ref) * (candidate[index] - mean_candidate)
        for index in range(len(reference))
    ) / len(reference)
    c1, c2 = 0.01**2, 0.03**2
    denominator = (mean_ref**2 + mean_candidate**2 + c1) * (variance_ref + variance_candidate + c2)
    if denominator <= 1e-12:
        return 1.0 if reference == candidate else 0.0
    return max(0.0, min(1.0, ((2 * mean_ref * mean_candidate + c1) * (2 * covariance + c2)) / denominator))


def _review_edges(luma: list[float], size: int = 64, threshold: float = 0.12) -> list[bool]:
    edges = [False] * (size * size)
    for y in range(1, size - 1):
        for x in range(1, size - 1):
            def value(dx: int, dy: int) -> float:
                return luma[(y + dy) * size + x + dx]

            gx = (value(-1, -1) + 2 * value(-1, 0) + value(-1, 1)) - (value(1, -1) + 2 * value(1, 0) + value(1, 1))
            gy = (value(-1, -1) + 2 * value(0, -1) + value(1, -1)) - (value(-1, 1) + 2 * value(0, 1) + value(1, 1))
            edges[y * size + x] = math.hypot(gx, gy) > threshold
    return edges


def _review_edge_overlap(reference: list[float], candidate: list[float], size: int = 64) -> float:
    reference_edges = _review_edges(reference, size)
    candidate_edges = _review_edges(candidate, size)
    union = sum(1 for left, right in zip(reference_edges, candidate_edges) if left or right)
    intersection = sum(1 for left, right in zip(reference_edges, candidate_edges) if left and right)
    return intersection / union if union else 1.0


def _review_tonal_parity(reference: list[float], candidate: list[float], bins: int = 16) -> float:
    def histogram(values: list[float]) -> list[float]:
        counts = [0] * bins
        for value in values:
            counts[min(bins - 1, max(0, int(value * bins)))] += 1
        total = max(1, len(values))
        return [count / total for count in counts]

    distance = sum(abs(left - right) for left, right in zip(histogram(reference), histogram(candidate)))
    return max(0.0, min(1.0, 1.0 - distance / 2.0))


def _review_foreground(image: Any, size: int = 96) -> tuple[list[bool], str]:
    """Build a conservative foreground mask and identify its evidence source."""
    rgba = image.convert("RGBA").resize((size, size), 2)  # PIL.Image.Resampling.BILINEAR
    pixels = list(rgba.getdata())
    alpha = [pixel[3] / 255.0 for pixel in pixels]
    alpha_span = max(alpha, default=0.0) - min(alpha, default=0.0)
    if alpha_span > 0.15:
        return [value >= 0.08 for value in alpha], "alpha"
    corners = [pixels[0], pixels[size - 1], pixels[(size - 1) * size], pixels[-1]]
    background = tuple(sum(pixel[channel] for pixel in corners) / len(corners) for channel in range(3))
    mask = []
    for pixel in pixels:
        distance = math.sqrt(sum((pixel[channel] - background[channel]) ** 2 for channel in range(3)))
        mask.append(distance >= 24.0)
    return mask, "corner-distance"


def _review_bbox(mask: list[bool], size: int) -> tuple[int, int, int, int] | None:
    points = [(index % size, index // size) for index, active in enumerate(mask) if active]
    if not points:
        return None
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def _review_iou(reference: list[bool], candidate: list[bool]) -> float:
    intersection = sum(1 for left, right in zip(reference, candidate) if left and right)
    union = sum(1 for left, right in zip(reference, candidate) if left or right)
    return intersection / union if union else 1.0


def _review_interior_sample(image: Any, grid: int = 96) -> tuple[list[float], list[bool], str, tuple[int, int, int, int]]:
    """Sample mean luma and figure-majority cells inside the foreground bounding box.

    Normalising each image to its own foreground box makes the signal useful when a render has
    a small camera/scale offset.  The separate majority mask prevents background pixels from
    leaking into the interior score.
    """
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = list(rgba.getdata())
    alpha = [pixel[3] / 255.0 for pixel in pixels]
    alpha_span = max(alpha, default=0.0) - min(alpha, default=0.0)
    if alpha_span > 0.15:
        mask = [value >= 0.08 for value in alpha]
        mask_source = "alpha"
    else:
        corners = [pixels[0], pixels[width - 1], pixels[(height - 1) * width], pixels[-1]]
        background = tuple(sum(pixel[channel] for pixel in corners) / len(corners) for channel in range(3))
        mask = [math.sqrt(sum((pixel[channel] - background[channel]) ** 2 for channel in range(3))) >= 24.0 for pixel in pixels]
        mask_source = "corner-distance"
    points = [(index % width, index // width) for index, active in enumerate(mask) if active]
    if not points:
        bbox = (0, 0, width, height)
    else:
        xs, ys = zip(*points)
        bbox = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
    x0, y0, x1, y1 = bbox
    box_width = max(1, x1 - x0)
    box_height = max(1, y1 - y0)
    values = [0.0] * (grid * grid)
    solid = [False] * (grid * grid)
    for gy in range(grid):
        top = y0 + gy * box_height // grid
        bottom = max(top + 1, y0 + (gy + 1) * box_height // grid)
        for gx in range(grid):
            left = x0 + gx * box_width // grid
            right = max(left + 1, x0 + (gx + 1) * box_width // grid)
            total = 0.0
            counted = 0
            foreground = 0
            for y in range(top, min(bottom, height)):
                row = y * width
                for x in range(left, min(right, width)):
                    pixel = pixels[row + x]
                    total += 0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]
                    counted += 1
                    if mask[row + x]:
                        foreground += 1
            index = gy * grid + gx
            values[index] = total / (255.0 * counted) if counted else 0.0
            solid[index] = bool(counted) and foreground > counted / 2
    return values, solid, mask_source, bbox


def _run_interior_difference(reference_path: Path, candidate_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Measure appearance differences only where both images contain foreground."""
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageOps
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for interior difference: {exc}") from exc

    grid = _bounded_int(params.get("grid", 96), 96, 16, 192)
    try:
        band_from = float(params.get("band_from", 0.0) or 0.0)
    except (TypeError, ValueError):
        band_from = 0.0
    try:
        band_to = float(params.get("band_to", 1.0) or 1.0)
    except (TypeError, ValueError):
        band_to = 1.0
    band_from = max(0.0, min(1.0, band_from))
    band_to = max(band_from, min(1.0, band_to))
    with Image.open(reference_path) as reference_source, Image.open(candidate_path) as candidate_source:
        reference = reference_source.convert("RGBA")
        candidate = candidate_source.convert("RGBA")
        candidate_resized = candidate.resize(reference.size, Image.Resampling.LANCZOS) if candidate.size != reference.size else candidate.copy()
        reference_values, reference_solid, reference_mask_source, reference_bbox = _review_interior_sample(reference, grid)
        candidate_values, candidate_solid, candidate_mask_source, candidate_bbox = _review_interior_sample(candidate_resized, grid)
        first_row = int(band_from * grid)
        last_row = max(first_row + 1, int(band_to * grid))
        shared = [
            gy * grid + gx
            for gy in range(first_row, min(last_row, grid))
            for gx in range(grid)
            if reference_solid[gy * grid + gx] and candidate_solid[gy * grid + gx]
        ]
        score = None if not shared else sum(abs(reference_values[index] - candidate_values[index]) for index in shared) / len(shared)
        difference = ImageChops.difference(reference.convert("RGB"), candidate_resized.convert("RGB"))
        heatmap = ImageOps.colorize(difference.convert("L"), black=(15, 23, 42), white=(239, 68, 68)).convert("RGBA")
        canvas = Image.new("RGBA", (reference.width * 3, reference.height), (255, 255, 255, 255))
        canvas.paste(reference, (0, 0))
        canvas.paste(candidate_resized, (reference.width, 0))
        canvas.paste(heatmap, (reference.width * 2, 0))
        draw = ImageDraw.Draw(canvas, "RGBA")
        for index in range(3):
            left = index * reference.width
            draw.rectangle((left, 0, left + reference.width - 1, reference.height - 1), outline=(15, 23, 42, 220), width=max(1, min(reference.size) // 320))
        token = f"{_slug(reference_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        output_path = workspace_dir / f"{token}_interior-difference.png"
        report_path = workspace_dir / f"{token}_interior-difference.json"
        canvas.save(output_path, format="PNG")

    status = "measured" if shared else "no-overlapping-figure-cells"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.interior-difference",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "reference": {"name": reference_path.name, "width": reference.width, "height": reference.height, "maskSource": reference_mask_source, "foregroundBox": list(reference_bbox)},
        "candidate": {"name": candidate_path.name, "originalWidth": candidate.size[0], "originalHeight": candidate.size[1], "resizedToReference": candidate.size != reference.size, "maskSource": candidate_mask_source, "foregroundBox": list(candidate_bbox)},
        "band": {"from": round(band_from, 6), "to": round(band_to, 6)},
        "grid": grid,
        "cellsCompared": len(shared),
        "interiorDifference": None if score is None else round(score, 6),
        "panels": ["reference", "candidate-resized-to-reference", "difference-heatmap"],
        "reviewNotes": [
            "Only cells classified as foreground in both images contribute; silhouette disagreement cannot inflate this signal.",
            "This is appearance evidence, not semantic proof. Review the heatmap before changing geometry or materials.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"filePath": str(output_path), "sidecars": [str(report_path)], "metadata": {"evidence_kind": "interior-difference", "schema_version": 1, "status": status, "interior_difference": report["interiorDifference"], "cells_compared": len(shared), "report": report_path.name}}


def _run_pose_sweep_gate(input_paths: list[Path], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Check an ordered pose-capture set without claiming rig correctness."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for pose sweep gating: {exc}") from exc

    required_frames = _bounded_int(params.get("required_frames", 4), 4, 2, 32)
    try:
        collapse_ratio = max(0.0, min(1.0, float(params.get("collapse_ratio", 0.2) or 0.2)))
    except (TypeError, ValueError):
        collapse_ratio = 0.2
    try:
        min_pose_delta = max(0.0, min(1.0, float(params.get("min_pose_delta", 0.03) or 0.03)))
    except (TypeError, ValueError):
        min_pose_delta = 0.03
    labels_raw = str(params.get("pose_labels") or "")
    labels = [item.strip() for item in labels_raw.split(",") if item.strip()]
    records: list[dict[str, Any]] = []
    masks: list[list[bool]] = []
    images: list[Any] = []
    for index, input_path in enumerate(input_paths):
        if not input_path.is_file():
            raise ValueError(f"pose sweep frame not found: {input_path}")
        with Image.open(input_path) as source:
            image = source.convert("RGBA")
            mask, mask_source = _review_foreground(image, 96)
            bbox = _review_bbox(mask, 96)
            images.append(image.copy())
        masks.append(mask)
        area = sum(mask) / float(len(mask))
        if bbox is None:
            bbox_norm = None
            centroid = None
        else:
            x0, y0, x1, y1 = bbox
            bbox_norm = [round(x0 / 96.0, 6), round(y0 / 96.0, 6), round(x1 / 96.0, 6), round(y1 / 96.0, 6)]
            active = [(cell % 96, cell // 96) for cell, enabled in enumerate(mask) if enabled]
            centroid = [round(sum(point[0] for point in active) / max(1, len(active)) / 96.0, 6), round(sum(point[1] for point in active) / max(1, len(active)) / 96.0, 6)]
        records.append({
            "index": index,
            "label": labels[index] if index < len(labels) else input_path.stem,
            "name": input_path.name,
            "maskSource": mask_source,
            "areaFraction": round(area, 6),
            "bbox": bbox_norm,
            "centroid": centroid,
        })

    baseline = records[0]["areaFraction"] if records else 0.0
    collapsed: list[int] = []
    for record in records:
        relative = None if baseline <= 1e-9 else record["areaFraction"] / baseline
        record["relativeArea"] = None if relative is None else round(relative, 6)
        if relative is not None and relative < collapse_ratio:
            collapsed.append(int(record["index"]))
    adjacent: list[dict[str, Any]] = []
    for index in range(1, len(masks)):
        iou = _review_iou(masks[index - 1], masks[index])
        adjacent.append({"from": index - 1, "to": index, "silhouetteIoU": round(iou, 6), "poseDelta": round(1.0 - iou, 6)})
    max_pose_delta = max((item["poseDelta"] for item in adjacent), default=0.0)
    errors: list[str] = []
    warnings: list[str] = []
    if len(input_paths) != required_frames:
        errors.append(f"expected {required_frames} ordered frames, received {len(input_paths)}")
    if collapsed:
        errors.append(f"foreground silhouette collapsed below {collapse_ratio:.3f} of frame 0 at frame(s) {collapsed}")
    if len(labels) not in (0, len(input_paths)):
        warnings.append("pose_labels count does not match the frame count; file stems were used where labels were missing")
    if adjacent and max_pose_delta < min_pose_delta:
        warnings.append("no adjacent silhouette change exceeded min_pose_delta; this is a capture/evidence warning, not proof that the rig is static")
    status = "fail" if errors else "needs_review" if not adjacent or max_pose_delta < min_pose_delta else "pass"

    cell_width, cell_height = 256, 256
    columns = min(4, max(1, len(images)))
    rows = max(1, (len(images) + columns - 1) // columns)
    canvas = Image.new("RGBA", (columns * cell_width, rows * (cell_height + 24)), (15, 23, 42, 255))
    draw = ImageDraw.Draw(canvas, "RGBA")
    for index, image in enumerate(images):
        thumb = image.copy()
        thumb.thumbnail((cell_width - 8, cell_height - 8), Image.Resampling.LANCZOS)
        left = (index % columns) * cell_width + (cell_width - thumb.width) // 2
        top = (index // columns) * (cell_height + 24) + (cell_height - thumb.height) // 2
        canvas.alpha_composite(thumb, (left, top))
        color = (248, 113, 113, 255) if index in collapsed else (74, 222, 128, 255)
        cell_left = index % columns * cell_width
        cell_top = index // columns * (cell_height + 24)
        draw.rectangle((cell_left, cell_top, cell_left + cell_width - 1, cell_top + cell_height + 23), outline=color, width=2)
        draw.text((cell_left + 8, cell_top + cell_height + 4), records[index]["label"], fill=(226, 232, 240, 255))
    workspace_dir.mkdir(parents=True, exist_ok=True)
    stem = _slug(input_paths[0].stem if input_paths else "pose-sweep")
    token = f"{stem}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}_pose-sweep.png"
    report_path = workspace_dir / f"{token}_pose-sweep.json"
    canvas.save(output_path, format="PNG")
    report = {
        "schemaVersion": 1,
        "kind": "polykit.pose-sweep-gate",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "settings": {"requiredFrames": required_frames, "collapseRatio": round(collapse_ratio, 6), "minPoseDelta": round(min_pose_delta, 6)},
        "frames": records,
        "adjacent": adjacent,
        "summary": {"frameCount": len(records), "collapsedFrames": collapsed, "maxPoseDelta": round(max_pose_delta, 6)},
        "errors": errors,
        "warnings": warnings,
        "reviewNotes": [
            "This gate measures 2D silhouette coverage and visible change across ordered captures; it cannot prove joint placement, volume preservation, or skin-weight correctness.",
            "Use a neutral frame first and keep camera, crop, and background consistent across the sweep.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"filePath": str(output_path), "sidecars": [str(report_path)], "metadata": {"evidence_kind": "pose-sweep-gate", "schema_version": 1, "status": status, "frame_count": len(records), "max_pose_delta": report["summary"]["maxPoseDelta"], "report": report_path.name}}


def _hair_otsu(values: list[float], bins: int = 64) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return low, 0.0, 0.0
    width = (high - low) / bins
    histogram = [0] * bins
    for value in values:
        histogram[min(bins - 1, int((value - low) / width))] += 1
    total = len(values)
    total_sum = sum((low + (index + 0.5) * width) * count for index, count in enumerate(histogram))
    below_count = 0
    below_sum = 0.0
    best_index = 0
    best_variance = -1.0
    for index, count in enumerate(histogram[:-1]):
        below_count += count
        below_sum += (low + (index + 0.5) * width) * count
        above_count = total - below_count
        if not below_count or not above_count:
            continue
        mean_below = below_sum / below_count
        mean_above = (total_sum - below_sum) / above_count
        variance = below_count * above_count * (mean_below - mean_above) ** 2
        if variance > best_variance:
            best_variance = variance
            best_index = index
    threshold = low + (best_index + 1) * width
    below = [value for value in values if value <= threshold]
    above = [value for value in values if value > threshold]
    if not below or not above:
        return threshold, 0.0, 0.0
    mean_below = sum(below) / len(below)
    mean_above = sum(above) / len(above)
    separation = abs(mean_above - mean_below)
    spread = math.sqrt(sum((value - mean_below) ** 2 for value in below) / len(below)) + math.sqrt(sum((value - mean_above) ** 2 for value in above) / len(above))
    separability = min(separation / spread, 1.0e6) if spread > 1e-9 else 1.0e6
    return threshold, separation, separability


def _run_hair_evidence(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Measure dark hair coverage in the image head band without inventing lock geometry."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for hair evidence: {exc}") from exc

    with Image.open(input_path) as source:
        image = source.convert("RGBA")
        width, height = image.size
        pixels = list(image.getdata())
        alpha_values = [pixel[3] for pixel in pixels]
        alpha_authoritative = max(alpha_values, default=0) - min(alpha_values, default=0) > 32
        if alpha_authoritative:
            mask = [pixel[3] >= 20 for pixel in pixels]
            mask_source = "alpha"
        else:
            corners = [pixels[0], pixels[width - 1], pixels[(height - 1) * width], pixels[-1]]
            background = tuple(sum(pixel[channel] for pixel in corners) / len(corners) for channel in range(3))
            mask = [math.sqrt(sum((pixel[channel] - background[channel]) ** 2 for channel in range(3))) >= 24.0 for pixel in pixels]
            mask_source = "corner-distance"
        points = [(index % width, index // width) for index, active in enumerate(mask) if active]
        token = f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        output_path = workspace_dir / f"{token}_hair-evidence.png"
        report_path = workspace_dir / f"{token}_hair-evidence.json"
        if not points:
            overlay = image.copy()
            overlay.save(output_path, format="PNG")
            report = {"schemaVersion": 1, "kind": "polykit.hair-evidence", "status": "no-foreground", "createdAt": datetime.now(timezone.utc).isoformat(), "maskSource": mask_source, "warnings": ["no foreground pixels were detected"], "reviewNotes": ["This image cannot provide hair coverage evidence without a foreground region."]}
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return {"filePath": str(output_path), "sidecars": [str(report_path)], "metadata": {"evidence_kind": "hair-evidence", "schema_version": 1, "status": report["status"], "report": report_path.name}}

        x0, y0 = min(point[0] for point in points), min(point[1] for point in points)
        x1, y1 = max(point[0] for point in points), max(point[1] for point in points)
        figure_height = max(1, y1 - y0 + 1)
        head_bottom = min(height, y0 + max(1, int(figure_height * 0.15)))
        head_pixels: list[tuple[int, int, float]] = []
        for y in range(y0, head_bottom):
            for x in range(x0, x1 + 1):
                if mask[y * width + x]:
                    pixel = pixels[y * width + x]
                    head_pixels.append((x, y, 0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]))
        threshold, separation, separability = _hair_otsu([item[2] for item in head_pixels])
        status = "measured" if head_pixels and separation >= 12.0 and separability >= 2.0 else "no-hair-skin-split"
        hair = {(x, y) for x, y, luma in head_pixels if luma <= threshold}
        head_height = max(1, head_bottom - y0)
        bands: dict[str, dict[str, float]] = {}
        for band_index, name in enumerate(("crown", "mid", "jaw")):
            top = y0 + int(head_height * band_index / 3)
            bottom = y0 + int(head_height * (band_index + 1) / 3)
            region = [(x, y) for x, y, _luma in head_pixels if top <= y < max(top + 1, bottom)]
            dark = [point for point in region if point in hair]
            bands[name] = {"coverage": round(len(dark) / max(1, len(region)), 4), "pixelCount": len(region)}
        rows = sorted({y for _x, y in hair})
        hairline = None
        if rows:
            last_row = rows[0]
            for row in rows[1:]:
                if row - last_row > 2:
                    break
                last_row = row
            hairline = round((last_row - y0) / figure_height, 4)
        overlay = image.copy()
        draw = ImageDraw.Draw(overlay, "RGBA")
        draw.rectangle((x0, y0, x1, y1), outline=(35, 211, 238, 230), width=max(1, min(width, height) // 320))
        draw.rectangle((x0, y0, x1, max(y0, head_bottom - 1)), outline=(245, 158, 11, 230), width=max(1, min(width, height) // 320))
        for index in (1, 2):
            y = y0 + int(head_height * index / 3)
            draw.line((x0, y, x1, y), fill=(245, 158, 11, 180), width=max(1, min(width, height) // 320))
        overlay.save(output_path, format="PNG")

    report = {
        "schemaVersion": 1,
        "kind": "polykit.hair-evidence",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceImage": {"name": input_path.name, "width": width, "height": height},
        "maskSource": mask_source,
        "figureBox": [x0, y0, x1, y1],
        "headBox": [x0, y0, x1, head_bottom],
        "darkThreshold": round(threshold, 3),
        "classSeparation": round(separation, 3),
        "separability": round(separability, 3),
        "hairFraction": round(len(hair) / max(1, len(head_pixels)), 4),
        "bands": bands,
        "hairline": hairline,
        "warnings": [] if status == "measured" else ["head luminance does not form a reliable hair/skin split"],
        "reviewNotes": [
            "Dark coverage is evidence, not a hair-lock or hairstyle generator; a single view cannot reveal hidden geometry.",
            "Review the overlay and measured bands before choosing hair materials or geometry.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"filePath": str(output_path), "sidecars": [str(report_path)], "metadata": {"evidence_kind": "hair-evidence", "schema_version": 1, "status": status, "hair_fraction": report["hairFraction"], "report": report_path.name}}


def _run_hair_gate(reference_path: Path, candidate_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Compare hair evidence while keeping coverage shortfall separate from baldness proof."""
    try:
        shortfall = float(params.get("band_shortfall", 0.08) or 0.08)
    except (TypeError, ValueError):
        shortfall = 0.08
    try:
        hairline_limit = float(params.get("hairline_offset_max", 0.013) or 0.013)
    except (TypeError, ValueError):
        hairline_limit = 0.013
    shortfall = max(0.0, min(1.0, shortfall))
    hairline_limit = max(0.0, min(1.0, hairline_limit))
    reference_result = _run_hair_evidence(reference_path, workspace_dir, params)
    candidate_result = _run_hair_evidence(candidate_path, workspace_dir, params)
    reference_report_path = Path(str(reference_result["sidecars"][0]))
    candidate_report_path = Path(str(candidate_result["sidecars"][0]))
    reference = json.loads(reference_report_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_report_path.read_text(encoding="utf-8"))
    notes: list[str] = []
    bands: dict[str, dict[str, Any]] = {}
    comparable = reference.get("status") == "measured" and candidate.get("status") == "measured"
    if comparable:
        for name, reference_band in reference.get("bands", {}).items():
            candidate_band = candidate.get("bands", {}).get(name, {})
            reference_coverage = float(reference_band.get("coverage", 0.0))
            candidate_coverage = float(candidate_band.get("coverage", 0.0))
            delta = round(candidate_coverage - reference_coverage, 4)
            bands[name] = {"reference": round(reference_coverage, 4), "candidate": round(candidate_coverage, 4), "delta": delta, "shortfall": delta <= -shortfall}
            if delta <= -shortfall:
                notes.append(f"{name}: coverage shortfall {delta:+.4f} against reference")
        reference_hairline = reference.get("hairline")
        candidate_hairline = candidate.get("hairline")
        hairline_offset = round(candidate_hairline - reference_hairline, 4) if reference_hairline is not None and candidate_hairline is not None else None
        if hairline_offset is not None and abs(hairline_offset) > hairline_limit:
            notes.append(f"hairline offset {hairline_offset:+.4f} exceeds limit")
    else:
        hairline_offset = None
        notes.append("one or both images did not produce a measurable hair/skin split")

    # The image-only gate deliberately never claims a pass: baldness requires the optional
    # geometric scalp-exposure channel, while these signals remain soft review evidence.
    status = "needs_review"
    token = f"{_slug(reference_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace_dir / f"{token}_hair-gate.json"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.hair-gate",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "reference": {"name": reference_path.name, "report": reference_report_path.name},
        "candidate": {"name": candidate_path.name, "report": candidate_report_path.name},
        "bands": bands,
        "hairlineOffset": hairline_offset,
        "softSignals": notes,
        "hardChannelPresent": False,
        "thresholds": {"bandShortfall": shortfall, "hairlineOffsetMax": hairline_limit},
        "limitations": [
            "Image evidence cannot reliably detect a bald patch or hair sunk inside the skull; supply a geometric scalp-exposure report for a hard channel.",
            "Coverage shortfall is a review signal and does not authorize widening hair masses by itself.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sidecars = [*reference_result.get("sidecars", []), *candidate_result.get("sidecars", []), report_path]
    return {"text": json.dumps(report, ensure_ascii=False, indent=2), "sidecars": [str(path) for path in sidecars], "metadata": {"evidence_kind": "hair-gate", "schema_version": 1, "status": status, "report": report_path.name}}


def _run_divine_eye(reference_path: Path, candidate_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Score a render against a reference with deterministic multi-signal gates."""
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageOps
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for Divine Eye review: {exc}") from exc

    with Image.open(reference_path) as reference_source, Image.open(candidate_path) as candidate_source:
        reference = reference_source.convert("RGBA")
        candidate = candidate_source.convert("RGBA")
        candidate_resized = candidate.resize(reference.size, Image.Resampling.LANCZOS) if candidate.size != reference.size else candidate.copy()
        reference_luma = _review_luma(reference)
        candidate_luma = _review_luma(candidate_resized)
        reference_mask, reference_mask_source = _review_foreground(reference)
        candidate_mask, candidate_mask_source = _review_foreground(candidate_resized)
        silhouette_iou = _review_iou(reference_mask, candidate_mask)
        reference_bbox = _review_bbox(reference_mask, 96)
        candidate_bbox = _review_bbox(candidate_mask, 96)
        reference_aspect = ((reference_bbox[2] - reference_bbox[0]) / max(1, reference_bbox[3] - reference_bbox[1])) if reference_bbox else 0.0
        candidate_aspect = ((candidate_bbox[2] - candidate_bbox[0]) / max(1, candidate_bbox[3] - candidate_bbox[1])) if candidate_bbox else 0.0
        aspect_delta = abs(reference_aspect - candidate_aspect) / max(reference_aspect, 1e-6) if reference_aspect else 0.0
        mean_absolute_error = sum(abs(left - right) for left, right in zip(reference_luma, candidate_luma)) / max(1, len(reference_luma))
        ssim = _review_ssim(reference_luma, candidate_luma)
        edge_overlap = _review_edge_overlap(reference_luma, candidate_luma)
        tonal_parity = _review_tonal_parity(reference_luma, candidate_luma)
        aggregate = 0.30 * silhouette_iou + 0.30 * ssim + 0.20 * edge_overlap + 0.20 * tonal_parity
        hard_silhouette = silhouette_iou >= 0.85
        mask_is_authoritative = reference_mask_source == "alpha" and candidate_mask_source == "alpha"
        if mask_is_authoritative and not hard_silhouette:
            status = "fail"
        elif aggregate >= 0.85 and mean_absolute_error <= 0.05 and (hard_silhouette or not mask_is_authoritative):
            status = "pass"
        else:
            status = "needs_review"

        difference = ImageChops.difference(reference.convert("RGB"), candidate_resized.convert("RGB"))
        heatmap = ImageOps.colorize(difference.convert("L"), black=(15, 23, 42), white=(239, 68, 68)).convert("RGBA")
        canvas = Image.new("RGBA", (reference.width * 3, reference.height), (255, 255, 255, 255))
        canvas.paste(reference, (0, 0))
        canvas.paste(candidate_resized, (reference.width, 0))
        canvas.paste(heatmap, (reference.width * 2, 0))
        draw = ImageDraw.Draw(canvas, "RGBA")
        for index in range(3):
            left = index * reference.width
            draw.rectangle((left, 0, left + reference.width - 1, reference.height - 1), outline=(15, 23, 42, 220), width=max(1, min(reference.size) // 320))
        token = f"{_slug(reference_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        output_path = workspace_dir / f"{token}_divine-eye.png"
        report_path = workspace_dir / f"{token}_divine-eye.json"
        canvas.save(output_path, format="PNG")

    report = {
        "schemaVersion": 1,
        "kind": "polykit.divine-eye",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "reference": {"name": reference_path.name, "width": reference.width, "height": reference.height, "maskSource": reference_mask_source},
        "candidate": {"name": candidate_path.name, "originalWidth": candidate.size[0], "originalHeight": candidate.size[1], "resizedToReference": candidate.size != reference.size, "maskSource": candidate_mask_source},
        "signals": {
            "silhouetteIoU": round(silhouette_iou, 6),
            "aspectDelta": round(aspect_delta, 6),
            "globalSSIM": round(ssim, 6),
            "edgeOverlap": round(edge_overlap, 6),
            "tonalParity": round(tonal_parity, 6),
            "meanAbsoluteError": round(mean_absolute_error, 6),
            "aggregate": round(aggregate, 6),
        },
        "gates": {"silhouetteIoUHardMinimum": 0.85, "aggregatePassMinimum": 0.85, "meanAbsoluteErrorMaximum": 0.05, "silhouetteGateAuthoritative": mask_is_authoritative},
        "panels": ["reference", "candidate-resized-to-reference", "difference-heatmap"],
        "reviewNotes": [
            "This is a deterministic review signal bundle, not semantic proof of identity or geometry correctness.",
            "Alpha masks are authoritative for the silhouette gate; corner-distance masks are advisory and remain needs_review unless the aggregate is strong.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {"evidence_kind": "divine-eye", "schema_version": 1, "status": status, "aggregate": report["signals"]["aggregate"], "report": report_path.name},
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


def _run_multi_view_evidence(input_paths: list[Path], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Normalize a batch of reference views into one reviewable contact sheet."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for multi-view evidence: {exc}") from exc
    if len(input_paths) < 2:
        raise ValueError("multi-view evidence requires at least two images")
    columns = _bounded_int(params.get("columns", 3), 3, 1, 6)
    cell_height = _bounded_int(params.get("cell_height", 256), 256, 64, 2048)
    token = f"{_slug(input_paths[0].stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    output_path = workspace_dir / f"{token}_multi-view.png"
    report_path = workspace_dir / f"{token}_multi-view.json"
    cells: list[dict[str, Any]] = []
    loaded: list[Image.Image] = []
    try:
        for index, path in enumerate(input_paths):
            with Image.open(path) as source:
                image = source.convert("RGB")
                scale = cell_height / max(1, image.height)
                cell_width = max(1, round(image.width * scale))
                resized = image.resize((cell_width, cell_height), Image.Resampling.LANCZOS)
                loaded.append(resized.copy())
                cells.append({
                    "index": index,
                    "name": path.name,
                    "sha256": _sha256(path),
                    "sourceSize": [image.width, image.height],
                    "cellSize": [cell_width, cell_height],
                    "aspect": round(image.width / max(1, image.height), 6),
                })
        cell_width = max((image.width for image in loaded), default=cell_height)
        rows = (len(loaded) + columns - 1) // columns
        label_height = 24
        canvas = Image.new("RGB", (columns * cell_width, rows * (cell_height + label_height)), "#0f172a")
        draw = ImageDraw.Draw(canvas)
        for index, image in enumerate(loaded):
            column, row = index % columns, index // columns
            left = column * cell_width + (cell_width - image.width) // 2
            top = row * (cell_height + label_height)
            canvas.paste(image, (left, top))
            draw.rectangle((column * cell_width, top + cell_height, (column + 1) * cell_width - 1, top + cell_height + label_height - 1), fill="#1e293b")
            draw.text((column * cell_width + 6, top + cell_height + 5), f"View {index + 1}: {cells[index]['name'][:48]}", fill="#e2e8f0")
        canvas.save(output_path, format="PNG")
    finally:
        for image in loaded:
            image.close()
    report = {
        "schemaVersion": 1,
        "kind": "polykit.multi-view-evidence",
        "status": "needs_visual_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "viewCount": len(cells),
        "layout": {"columns": columns, "rows": rows, "cellHeight": cell_height, "cellWidth": cell_width, "labelHeight": label_height},
        "views": cells,
        "reviewNotes": [
            "Views are normalized for side-by-side inspection; no camera pose or correspondence is inferred.",
            "Use reviewed view ordering and landmarks as input to a multi-view reconstruction or projection bake.",
        ],
        "contactSheet": output_path.name,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "multi-view-evidence",
            "schema_version": 1,
            "status": report["status"],
            "view_count": len(cells),
            "report": report_path.name,
        },
    }


def _turntable_holes(mask: list[bool], size: int = 96) -> dict[str, Any]:
    """Find background components enclosed by a foreground silhouette."""
    total = size * size
    reached = [False] * total
    stack: list[int] = []
    for x in range(size):
        for y in (0, size - 1):
            index = y * size + x
            if not mask[index] and not reached[index]:
                reached[index] = True
                stack.append(index)
    for y in range(size):
        for x in (0, size - 1):
            index = y * size + x
            if not mask[index] and not reached[index]:
                reached[index] = True
                stack.append(index)
    while stack:
        index = stack.pop()
        x, y = index % size, index // size
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < size and 0 <= ny < size:
                neighbour = ny * size + nx
                if not mask[neighbour] and not reached[neighbour]:
                    reached[neighbour] = True
                    stack.append(neighbour)
    visited = [False] * total
    holes = 0
    largest = 0
    for start in range(total):
        if mask[start] or reached[start] or visited[start]:
            continue
        component = [start]
        visited[start] = True
        size_of_component = 0
        while component:
            index = component.pop()
            size_of_component += 1
            x, y = index % size, index // size
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < size and 0 <= ny < size:
                    neighbour = ny * size + nx
                    if not mask[neighbour] and not reached[neighbour] and not visited[neighbour]:
                        visited[neighbour] = True
                        component.append(neighbour)
        holes += size_of_component
        largest = max(largest, size_of_component)
    foreground = sum(1 for active in mask if active)
    return {"interiorHolePixelCount": holes, "interiorHoleFraction": holes / foreground if foreground else 0.0, "largestHolePixelCount": largest}


def _run_turntable_gate(input_paths: list[Path], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Require a minimum orbit and reject silhouette-enclosed background holes."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for turntable gate: {exc}") from exc
    raw_angles = params.get("required_azimuths", "0,90,180,270")
    if isinstance(raw_angles, list):
        angles = [float(value) for value in raw_angles]
    else:
        angles = [float(value.strip()) for value in str(raw_angles).split(",") if value.strip()]
    if len(angles) < 1:
        raise ValueError("turntable gate requires at least one required azimuth")
    if len(input_paths) < len(angles) or any(not path.is_file() for path in input_paths[: len(angles)]):
        raise ValueError(f"turntable gate requires {len(angles)} image files in required-angle order")
    try:
        collapse_ratio = float(params.get("collapse_ratio", 0.15) or 0.15)
        hole_fraction = float(params.get("hole_fraction", 0.01) or 0.01)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"turntable gate numeric parameters are invalid: {exc}") from exc
    allow_holes = params.get("allow_holes", False)
    if isinstance(allow_holes, str):
        allow_holes = allow_holes.strip().lower() in {"1", "true", "yes", "on"}
    size = 96
    measurements: list[dict[str, Any]] = []
    for angle, path in zip(angles, input_paths):
        with Image.open(path) as source:
            mask, mask_source = _review_foreground(source.convert("RGBA"), size)
        area = sum(1 for active in mask if active) / (size * size)
        holes = _turntable_holes(mask, size)
        measurements.append({"azimuth": round(angle, 4), "name": path.name, "areaFraction": round(area, 6), "maskSource": mask_source, "holes": {**holes, "interiorHoleFraction": round(holes["interiorHoleFraction"], 6)}})
    reference_area = measurements[0]["areaFraction"]
    failures: list[str] = []
    for measurement in measurements:
        ratio = measurement["areaFraction"] / reference_area if reference_area > 1e-9 else 0.0
        measurement["areaRatioToReference"] = round(ratio, 6)
        measurement["degenerateMask"] = measurement["areaFraction"] < 0.001 or measurement["areaFraction"] > 0.98
        if measurement["degenerateMask"]:
            failures.append(f"{measurement['azimuth']:g}° has a degenerate foreground mask")
        elif ratio < collapse_ratio:
            failures.append(f"{measurement['azimuth']:g}° silhouette area collapsed to {ratio:.4f} of the reference")
        if measurement["holes"]["interiorHolePixelCount"] >= 4 and measurement["holes"]["interiorHoleFraction"] >= hole_fraction and not allow_holes:
            failures.append(f"{measurement['azimuth']:g}° encloses a background hole inside the silhouette")
    status = "fail" if failures else "pass"
    token = f"{_slug(input_paths[0].stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    report_path = workspace_dir / f"{token}_turntable-gate.json"
    report = {
        "schemaVersion": 1,
        "kind": "polykit.turntable-gate",
        "status": status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "requiredAzimuths": [round(angle, 4) for angle in angles],
        "captures": measurements,
        "thresholds": {"collapseRatio": collapse_ratio, "holeFraction": hole_fraction, "allowHoles": bool(allow_holes)},
        "failures": failures,
        "passed": not failures,
        "reviewNotes": [
            "The input order maps to requiredAzimuths; missing angles are a hard input error rather than a silent warning.",
            "An enclosed background region is treated as a hole unless allow_holes is explicitly enabled for a legitimate ring/opening.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"text": json.dumps(report, ensure_ascii=False, indent=2), "sidecars": [str(report_path)], "metadata": {"evidence_kind": "turntable-gate", "schema_version": 1, "status": status, "capture_count": len(measurements), "report": report_path.name}}


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
    correspondence_text = str(input_data.get("text") or params.get("correspondences_json") or "")
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
        elif node_id == "divine-eye":
            raw_paths = input_data.get("filePaths")
            paths = [Path(str(value)) for value in raw_paths] if isinstance(raw_paths, list) else []
            if len(paths) != 2 or any(not path.is_file() for path in paths):
                error(f"reference-evidence: Divine Eye requires exactly two image files: {raw_paths}")
                return
            progress(20, "Running multi-signal visual review…")
            result = _run_divine_eye(paths[0], paths[1], workspace_dir, params)
        elif node_id == "interior-difference":
            raw_paths = input_data.get("filePaths")
            paths = [Path(str(value)) for value in raw_paths] if isinstance(raw_paths, list) else []
            if len(paths) != 2 or any(not path.is_file() for path in paths):
                error(f"reference-evidence: interior difference requires exactly two image files: {raw_paths}")
                return
            progress(20, "Measuring shared foreground appearance…")
            result = _run_interior_difference(paths[0], paths[1], workspace_dir, params)
        elif node_id == "hair-gate":
            raw_paths = input_data.get("filePaths")
            paths = [Path(str(value)) for value in raw_paths] if isinstance(raw_paths, list) else []
            if len(paths) != 2 or any(not path.is_file() for path in paths):
                error(f"reference-evidence: hair gate requires exactly two image files: {raw_paths}")
                return
            progress(20, "Comparing hair evidence…")
            result = _run_hair_gate(paths[0], paths[1], workspace_dir, params)
        elif node_id == "hair-evidence":
            if input_path is None or not input_path.is_file():
                error(f"reference-evidence: hair evidence image not found: {input_raw}")
                return
            progress(20, "Measuring hair coverage evidence…")
            result = _run_hair_evidence(input_path, workspace_dir, params)
        elif node_id == "multi-view-evidence":
            raw_paths = input_data.get("filePaths")
            paths = [Path(str(value)) for value in raw_paths] if isinstance(raw_paths, list) else []
            if len(paths) < 2 or any(not path.is_file() for path in paths):
                error(f"reference-evidence: multi-view evidence requires at least two image files: {raw_paths}")
                return
            progress(20, "Normalizing reference views…")
            result = _run_multi_view_evidence(paths, workspace_dir, params)
        elif node_id == "turntable-gate":
            raw_paths = input_data.get("filePaths")
            paths = [Path(str(value)) for value in raw_paths] if isinstance(raw_paths, list) else []
            progress(20, "Checking turntable coverage and holes…")
            try:
                result = _run_turntable_gate(paths, workspace_dir, params)
            except ValueError as exc:
                error(f"reference-evidence: {exc}")
                return
        elif node_id == "pose-sweep-gate":
            raw_paths = input_data.get("filePaths")
            paths = [Path(str(value)) for value in raw_paths] if isinstance(raw_paths, list) else []
            if len(paths) < 2:
                error(f"reference-evidence: pose sweep requires at least two image files: {raw_paths}")
                return
            progress(20, "Checking ordered pose captures…")
            try:
                result = _run_pose_sweep_gate(paths, workspace_dir, params)
            except ValueError as exc:
                error(f"reference-evidence: {exc}")
                return
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
        elif node_id == "gradient-stops":
            progress(20, "Extracting gradient stops…")
            result = _run_gradient_stops(input_path, workspace_dir, params)
        elif node_id == "pbr-evidence":
            progress(20, "Deriving PBR evidence maps…")
            result = _run_pbr_evidence(input_path, workspace_dir, params)
        elif node_id == "landmark-guide":
            progress(20, "Drawing landmark guide…")
            result = _run_landmark_guide(input_path, workspace_dir, params)
        elif node_id == "camera-guide":
            progress(20, "Estimating reference camera…")
            result = _run_camera_guide(input_path, workspace_dir, params)
        elif node_id == "camera-fit":
            progress(20, "Fitting camera to landmarks…")
            result = _run_camera_fit(input_path, correspondence_text, workspace_dir, params)
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
