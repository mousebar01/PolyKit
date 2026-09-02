"""Versioned environment-production entry with Terrain Compiler v2 support.

Legacy descriptors keep the original processor.py terrain implementation so old
seeded worlds remain stable. terrainVersion=2 opts into the shared field math
used by the browser planner and production terrain export.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import processor as legacy
from terrain_fields import compile_fields, parse_program, surface_colors


def _requested_resolution(descriptor: dict[str, Any], params: dict[str, Any]) -> int:
    raw = params.get("resolution", descriptor.get("resolution", 64))
    try:
        return max(8, min(256, int(raw)))
    except (TypeError, ValueError):
        return 64


def _terrain_version(descriptor: dict[str, Any]) -> int:
    raw = descriptor.get("terrainVersion", descriptor.get("terrain_version", 1))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _terrain_mesh_v2(descriptor: dict[str, Any], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import trimesh

    resolution = _requested_resolution(descriptor, params)
    program = parse_program(descriptor, resolution=resolution)
    fields = compile_fields(program)
    res = program.resolution
    vertices = np.empty((res * res, 3), dtype=np.float32)
    for row in range(res):
        for column in range(res):
            index = row * res + column
            vertices[index] = (
                (column / (res - 1) - 0.5) * program.size,
                fields.heights[index],
                (row / (res - 1) - 0.5) * program.size,
            )
    faces: list[list[int]] = []
    for row in range(res - 1):
        for column in range(res - 1):
            lower_left = row * res + column
            lower_right = lower_left + 1
            upper_left = lower_left + res
            upper_right = upper_left + 1
            faces.extend(([lower_left, lower_right, upper_right], [lower_left, upper_right, upper_left]))

    terrain = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces, dtype=np.int64), process=False)
    terrain.visual = trimesh.visual.ColorVisuals(vertex_colors=surface_colors(fields))
    scene = trimesh.Scene()
    scene.add_geometry(terrain, geom_name="terrain", node_name="terrain")

    include_water = params.get("include_water", True)
    if isinstance(include_water, str):
        include_water = include_water.strip().lower() not in {"", "0", "false", "no", "off"}
    water_included = bool(include_water)
    if water_included:
        thickness = legacy._finite(params.get("water_thickness", 0.04), "water_thickness", positive=True)
        water = trimesh.creation.box(extents=(program.size, thickness, program.size))
        water.apply_translation((0.0, program.sea_level - thickness * 0.5, 0.0))
        water.visual = trimesh.visual.ColorVisuals(
            vertex_colors=np.tile(np.asarray([44, 117, 156, 210], dtype=np.uint8), (len(water.vertices), 1))
        )
        scene.add_geometry(water, geom_name="water", node_name="water")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = f"terrain_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    scene.export(output_path)

    vertex_hash = hashlib.sha256(vertices.tobytes()).hexdigest()
    field_hash = hashlib.sha256(fields.heights.tobytes()).hexdigest()
    dominant_counts = {
        region.id: int(np.count_nonzero(fields.dominant == index))
        for index, region in enumerate(program.regions)
    }
    unassigned_count = int(np.count_nonzero(fields.dominant < 0))
    report = {
        "schemaVersion": 2,
        "kind": "polykit.terrain-mesh",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "seed": program.seed,
            "size": round(program.size, 6),
            "resolution": res,
            "terrainVersion": 2,
            "regionCount": len(program.regions),
            "riverCount": len(program.rivers),
        },
        "compiler": {
            "version": 2,
            "reference": "worlds/runtime/terrain.ts",
            "fieldHash": field_hash,
        },
        "terrain": {
            "vertexCount": int(len(vertices)),
            "faceCount": int(len(faces)),
            "bounds": [[round(float(value), 6) for value in row] for row in scene.geometry["terrain"].bounds],
            "minHeight": round(fields.min_height, 6),
            "maxHeight": round(fields.max_height, 6),
            "vertexHash": vertex_hash,
        },
        "surface": {
            "materialMode": "region-vertex-blend",
            "dominantRegionCounts": dominant_counts,
            "unassignedCount": unassigned_count,
        },
        "water": {
            "included": water_included,
            "seaLevel": round(program.sea_level, 6),
            "geometryName": "water" if water_included else None,
        },
        "regions": [
            {
                "id": region.id,
                "kind": region.kind,
                "center": list(region.center),
                "radius": round(region.radius, 6),
                "irregularity": round(region.irregularity, 6),
                "amplitude": round(region.amplitude, 6),
                "color": "#%02x%02x%02x" % region.color,
            }
            for region in program.regions
        ],
        "rivers": [
            {
                "id": river.id,
                "pointCount": len(river.path),
                "width": round(river.width, 6),
                "depth": round(river.depth, 6),
            }
            for river in program.rivers
        ],
        "reviewNotes": [
            "Terrain Compiler v2 mirrors browser world-field math so preview and production sample the same seeded terrain.",
            "Region material colors are blended from the same influence weights used by elevation and placement; texture splatting remains a later production stage.",
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "terrain-mesh",
            "schema_version": 2,
            "status": "pass",
            "terrain_version": 2,
            "compiler_version": 2,
            "vertex_count": len(vertices),
            "face_count": len(faces),
            "water_included": water_included,
            "vertex_hash": vertex_hash,
            "field_hash": field_hash,
            "material_mode": "region-vertex-blend",
            "report": report_path.name,
        },
    }


def _terrain_mesh(descriptor: dict[str, Any], workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    if _terrain_version(descriptor) == 2:
        return _terrain_mesh_v2(descriptor, workspace_dir, params)
    return legacy._terrain_mesh(descriptor, workspace_dir, params)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.readline())
        input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        node_id = str(params.get("_node_id") or "terrain-mesh")
        if node_id not in {"terrain-mesh", "city-blockout", "vegetation-scatter", "room-blockout", "multi-room-blockout"}:
            legacy.error(f"environment-production: unsupported node '{node_id}'")
            return
        text = input_data.get("text")
        if not isinstance(text, str) or not text.strip():
            legacy.error("environment-production: the selected node requires a JSON descriptor on the text input")
            return
        descriptor = json.loads(text)
        if not isinstance(descriptor, dict):
            raise ValueError("terrain descriptor must be a JSON object")
        workspace_raw = payload.get("workspaceDir")
        if not isinstance(workspace_raw, str) or not workspace_raw.strip():
            raise ValueError("workspaceDir is required")
        legacy.progress(5, "Reading terrain specification…")
        if node_id == "terrain-mesh":
            result = _terrain_mesh(descriptor, Path(workspace_raw), params)
            legacy.progress(90, "Writing terrain and water meshes…")
            legacy.progress(100, "Terrain mesh ready")
        elif node_id == "city-blockout":
            result = legacy._city_blockout(descriptor, Path(workspace_raw), params)
            legacy.progress(90, "Writing roads and building masses…")
            legacy.progress(100, "City blockout ready")
        elif node_id == "room-blockout":
            result = legacy._room_blockout(descriptor, Path(workspace_raw), params)
            legacy.progress(90, "Writing room shell and openings…")
            legacy.progress(100, "Room blockout ready")
        elif node_id == "multi-room-blockout":
            result = legacy._multi_room_blockout(descriptor, Path(workspace_raw), params)
            legacy.progress(90, "Composing room shells…")
            legacy.progress(100, "Multi-room blockout ready")
        else:
            result = legacy._vegetation_scatter(descriptor, Path(workspace_raw), params)
            legacy.progress(90, "Writing vegetation instances…")
            legacy.progress(100, "Vegetation scatter ready")
        legacy.emit({"type": "done", "result": result})
    except json.JSONDecodeError as exc:
        legacy.error(f"environment-production: invalid terrain JSON ({exc.msg})")
    except Exception as exc:
        legacy.error(f"environment-production: {exc}")


if __name__ == "__main__":
    main()
