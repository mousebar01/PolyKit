"""Game-ready mesh derivative process nodes.

The processor follows PolyKit's line-delimited process protocol. Collision
meshes and LOD files are generated in the run-private process workspace; the
normal workflow engine publishes the primary mesh and any JSON/GLB sidecars at
the selected sink.
"""
from __future__ import annotations

import json
import math
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": percent, "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _slug(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+", "_", value)
    return result.strip("_").lower()[:48] or "mesh"


def _round(value: Any) -> float:
    number = float(value)
    return round(number, 6) if math.isfinite(number) else 0.0


def _vector(values: Any) -> list[float]:
    return [_round(value) for value in values]


def _load_scene(input_path: Path) -> Any:
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError(f"trimesh is required for mesh production: {exc}") from exc

    loaded = trimesh.load(input_path, force="scene", process=False)
    if isinstance(loaded, trimesh.Scene):
        return loaded
    if isinstance(loaded, trimesh.Trimesh):
        return trimesh.Scene(loaded)
    raise RuntimeError(f"unsupported mesh payload: {type(loaded).__name__}")


def _components(scene: Any) -> list[tuple[Any, Any, str]]:
    components: list[tuple[Any, Any, str]] = []
    for node_name in scene.graph.nodes_geometry:
        try:
            transform, geometry_name = scene.graph.get(node_name)
            geometry = scene.geometry[geometry_name]
            if not hasattr(geometry, "vertices") or not hasattr(geometry, "faces"):
                continue
            components.append((geometry, transform, str(node_name)))
        except (KeyError, TypeError, ValueError):
            continue
    if not components:
        raise ValueError("mesh contains no renderable components")
    return components


def _world_mesh(scene: Any) -> Any:
    import numpy as np
    import trimesh

    meshes = []
    for geometry, transform, _name in _components(scene):
        vertices = np.asarray(geometry.vertices, dtype=float)
        matrix = np.asarray(transform, dtype=float)
        world_vertices = (vertices @ matrix[:3, :3].T) + matrix[:3, 3]
        meshes.append(trimesh.Trimesh(vertices=world_vertices, faces=np.asarray(geometry.faces, dtype=int), process=False))
    return trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]


def _token(input_path: Path, suffix: str) -> str:
    return f"{_slug(input_path.stem)}_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}_{suffix}"


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _collision_mesh(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    scene = _load_scene(input_path)
    source = _world_mesh(scene)
    requested = str(params.get("method") or "convex_hull").strip().lower()
    if requested not in {"convex_hull", "box"}:
        requested = "convex_hull"
    method = requested
    warnings: list[str] = []
    if requested == "box":
        collider = source.bounding_box
    else:
        try:
            collider = source.convex_hull
        except Exception as exc:
            collider = source.bounding_box
            method = "box-fallback"
            warnings.append(f"convex hull failed ({type(exc).__name__}); used the bounding box fallback")
    if len(getattr(collider, "faces", [])) == 0:
        raise ValueError("collision proxy contains no faces")

    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = _token(input_path, "collision")
    output_path = workspace_dir / f"{token}.glb"
    report_path = workspace_dir / f"{token}.json"
    collider.export(output_path)
    source_bounds = source.bounds
    report = {
        "schemaVersion": 1,
        "kind": "polykit.collision-mesh",
        "status": "pass" if not warnings else "needs_review",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown"},
        "method": {"requested": requested, "used": method},
        "source": {
            "componentCount": len(_components(scene)),
            "vertices": int(len(source.vertices)),
            "faces": int(len(source.faces)),
            "bounds": [_vector(source_bounds[0]), _vector(source_bounds[1])],
        },
        "collision": {
            "vertices": int(len(collider.vertices)),
            "faces": int(len(collider.faces)),
            "bounds": [_vector(collider.bounds[0]), _vector(collider.bounds[1])],
            "volume": _round(abs(float(getattr(collider, "volume", 0.0)))),
        },
        "warnings": warnings,
        "reviewNotes": [
            "Collision geometry is an interaction proxy, not a render mesh; inspect clearance around thin or concave parts.",
            "The proxy uses world-space bounds and therefore preserves the source asset coordinate frame.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)],
        "metadata": {
            "evidence_kind": "collision-mesh",
            "schema_version": 1,
            "status": report["status"],
            "method": method,
            "source_faces": int(len(source.faces)),
            "collision_faces": int(len(collider.faces)),
            "report": report_path.name,
        },
    }


def _ratio(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _decimate(mesh: Any, target_faces: int, work_dir: Path) -> Any:
    import pymeshlab
    import trimesh

    if len(mesh.faces) <= target_faces:
        return mesh.copy()
    source_path = work_dir / f"source-{uuid.uuid4().hex}.ply"
    output_path = work_dir / f"output-{uuid.uuid4().hex}.ply"
    mesh.export(source_path)
    mesh_set = pymeshlab.MeshSet()
    mesh_set.load_new_mesh(str(source_path))
    mesh_set.meshing_decimation_quadric_edge_collapse(
        targetfacenum=int(max(4, target_faces)),
        preservenormal=True,
        preserveboundary=True,
    )
    mesh_set.save_current_mesh(str(output_path))
    result = trimesh.load(output_path, force="mesh", process=False)
    if not isinstance(result, trimesh.Trimesh):
        raise RuntimeError("pymeshlab did not return a triangle mesh")
    return result


def _lod_generate(input_path: Path, workspace_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    import trimesh

    scene = _load_scene(input_path)
    components = _components(scene)
    ratios = {
        "LOD1": _ratio(params.get("lod1_ratio"), 0.5, 0.05, 1.0),
        "LOD2": _ratio(params.get("lod2_ratio"), 0.2, 0.02, 1.0),
    }
    try:
        min_faces = int(params.get("min_faces", 32) or 32)
    except (TypeError, ValueError):
        min_faces = 32
    min_faces = max(4, min(100000, min_faces))
    source_faces = sum(int(len(geometry.faces)) for geometry, _transform, _name in components)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    token = _token(input_path, "lod")
    output_path = workspace_dir / f"{token}_LOD0.glb"
    report_path = workspace_dir / f"{token}.json"
    lod_files: list[dict[str, Any]] = []
    temp_dir = Path(tempfile.mkdtemp(prefix="polykit-lod-", dir=str(workspace_dir)))
    try:
        base_scene = _load_scene(input_path)
        base_scene.export(output_path)
        lod_files.append({"level": "LOD0", "path": output_path, "faces": source_faces, "ratio": 1.0})
        for level, ratio in ratios.items():
            lod_scene = trimesh.Scene()
            level_faces = 0
            for geometry, transform, name in components:
                target = max(min_faces, int(round(len(geometry.faces) * ratio)))
                reduced = _decimate(geometry, target, temp_dir)
                lod_scene.add_geometry(reduced, geom_name=name, node_name=name, transform=transform)
                level_faces += int(len(reduced.faces))
            lod_path = workspace_dir / f"{token}_{level}.glb"
            lod_scene.export(lod_path)
            lod_files.append({"level": level, "path": lod_path, "faces": level_faces, "ratio": ratio})
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    report = {
        "schemaVersion": 1,
        "kind": "polykit.lod-generation",
        "status": "pass",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sourceMesh": {"name": input_path.name, "format": input_path.suffix.lower().lstrip(".") or "unknown"},
        "levels": [
            {"level": item["level"], "file": Path(item["path"]).name, "faces": item["faces"], "ratio": item["ratio"]}
            for item in lod_files
        ],
        "settings": {"lod1Ratio": ratios["LOD1"], "lod2Ratio": ratios["LOD2"], "minFacesPerComponent": min_faces},
        "reviewNotes": [
            "LOD generation is triangle reduction for runtime distance tiers; validate silhouette, UVs, and material seams at each distance.",
            "LOD0 is the primary mesh output; LOD1 and LOD2 are published as GLB sidecars beside it.",
        ],
    }
    _write_report(report_path, report)
    return {
        "filePath": str(output_path),
        "sidecars": [str(report_path)] + [str(item["path"]) for item in lod_files[1:]],
        "metadata": {
            "evidence_kind": "lod-generation",
            "schema_version": 1,
            "status": "pass",
            "level_count": len(lod_files),
            "source_faces": source_faces,
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
        error(f"mesh-production: input mesh not found: {input_raw}")
        return
    workspace_dir = Path(str(data.get("workspaceDir") or ""))
    node_id = str(params.get("_node_id") or "collision-mesh")
    try:
        progress(5, "Loading mesh…")
        if node_id == "collision-mesh":
            progress(25, "Building collision proxy…")
            result = _collision_mesh(input_path, workspace_dir, params)
        elif node_id == "lod-generate":
            progress(25, "Generating LOD levels…")
            result = _lod_generate(input_path, workspace_dir, params)
        else:
            raise RuntimeError(f"unsupported mesh production node '{node_id}'")
        progress(90, "Writing mesh derivatives…")
        progress(100, "Mesh derivative ready")
        emit({"type": "done", "result": result})
    except Exception as exc:
        error(f"mesh-production: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"mesh-production: {exc}")
