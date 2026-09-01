"""General-purpose reference image evidence and comparison nodes."""
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


def main() -> None:
    raw = sys.stdin.readline()
    data = json.loads(raw)
    input_data = data.get("input") or {}
    params = data.get("params") or {}
    node_id = str(params.get("_node_id") or "reference-quality")
    input_raw = input_data.get("filePath")
    input_path = Path(str(input_raw)) if input_raw else None
    correspondence_text = str(input_data.get("text") or params.get("correspondences_json") or "")
    workspace_dir = Path(str(data.get("workspaceDir") or ""))

    try:
        progress(5, "Reading reference image…")
        if node_id in {"reference-compare", "multi-view-evidence"}:
            raw_paths = input_data.get("filePaths")
            paths = [Path(str(value)) for value in raw_paths] if isinstance(raw_paths, list) else []
            minimum = 2
            if len(paths) < minimum or any(not path.is_file() for path in paths):
                error(f"reference-evidence: {node_id} requires at least two image files: {raw_paths}")
                return
            if node_id == "reference-compare":
                if len(paths) != 2:
                    error(f"reference-evidence: comparison requires exactly two image files: {raw_paths}")
                    return
                progress(20, "Comparing reference and candidate…")
                result = _run_reference_compare(paths[0], paths[1], workspace_dir, params)
            else:
                progress(20, "Normalizing reference views…")
                result = _run_multi_view_evidence(paths, workspace_dir, params)
        else:
            if input_path is None or not input_path.is_file():
                error(f"reference-evidence: input image not found: {input_raw}")
                return
            if node_id == "reference-quality":
                progress(20, "Measuring reference quality…")
                result = _run_reference_quality(input_path, workspace_dir, params)
            elif node_id == "material-palette":
                progress(20, "Extracting material palette…")
                result = _run_material_palette(input_path, workspace_dir, params)
            elif node_id == "material-region":
                progress(20, "Analyzing material region…")
                result = _run_material_region(input_path, workspace_dir, params)
            elif node_id == "pbr-evidence":
                progress(20, "Deriving PBR evidence maps…")
                result = _run_pbr_evidence(input_path, workspace_dir, params)
            elif node_id == "camera-fit":
                progress(20, "Fitting camera to landmarks…")
                result = _run_camera_fit(input_path, correspondence_text, workspace_dir, params)
            elif node_id == "delight-albedo":
                progress(20, "Estimating illumination…")
                result = _run_delight_albedo(input_path, workspace_dir, params)
            else:
                raise RuntimeError(f"unsupported reference evidence node '{node_id}'")
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
