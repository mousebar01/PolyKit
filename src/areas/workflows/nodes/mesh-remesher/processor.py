"""
Mesh Remesher — built-in process node pack.

Protocol: reads one JSON line from stdin, writes JSON lines to stdout.
  stdin : { input, params, workspaceDir, tempDir }
  stdout: { type: "progress"|"log"|"done"|"error", ... }
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import re
import uuid
from datetime import datetime

_MAX_SLUG = 40


def _slugify(name: str, fallback: str = "model") -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+", "_", str(name or "")).strip("_")
    slug = re.sub(r"_+", "_", slug).lower()[:_MAX_SLUG].strip("_")
    return slug or fallback


def _output_name(stem: str, tag: str, ext: str = ".glb") -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{_slugify(stem)}_{ts}_{uuid.uuid4().hex[:8]}_{_slugify(tag)}{ext}"


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def progress(pct: int, label: str) -> None:
    emit({"type": "progress", "percent": pct, "label": label})


def log(msg: str) -> None:
    emit({"type": "log", "message": msg})


def done(file_path: str) -> None:
    emit({"type": "done", "result": {"filePath": file_path}})


def error(msg: str) -> None:
    emit({"type": "error", "message": msg})


def main() -> None:
    raw   = sys.stdin.readline()
    data  = json.loads(raw)

    input_data    = data.get("input", {})
    params        = data.get("params", {})
    workspace_dir = data.get("workspaceDir", "")

    input_path = input_data.get("filePath")
    if not input_path or not Path(input_path).is_file():
        error(f"mesh-remesher: input file not found: {input_path}")
        return

    mode               = str(params.get("mode", "triangle"))
    target_edge_length = float(params.get("target_edge_length", 0.0))

    out_dir = Path(workspace_dir) / "Workflows"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / _output_name(Path(input_path).stem, tag="remesh"))

    log(f"Mode: {mode}, edge length: {target_edge_length or 'auto'}")

    if mode == "none":
        progress(50, "Passing through…")
        shutil.copy2(input_path, out_path)
        progress(100, "Done")
        done(out_path)
        return

    try:
        import pymeshlab
    except ImportError:
        error("mesh-remesher: pymeshlab is not available on this system")
        return

    import trimesh

    progress(10, "Loading mesh…")
    loaded = trimesh.load(input_path)
    if isinstance(loaded, trimesh.Scene):
        geoms = list(loaded.geometry.values())
        geom  = trimesh.util.concatenate(geoms) if len(geoms) > 1 else geoms[0]
    else:
        geom = loaded

    tmp_dir = tempfile.mkdtemp()
    try:
        ply_in  = os.path.join(tmp_dir, "input.ply")
        ply_out = os.path.join(tmp_dir, "output.ply")
        geom.export(ply_in)

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(ply_in)

        if target_edge_length <= 0:
            measures           = ms.get_geometric_measures()
            target_edge_length = float(measures.get("avg_edge_length", 0.02))
            log(f"Auto edge length: {target_edge_length:.6f}")

        progress(30, f"Remeshing ({mode})…")

        if mode == "triangle":
            ms.meshing_isotropic_explicit_remeshing(
                targetlen=pymeshlab.PureValue(target_edge_length),
                iterations=3,
            )
        elif mode == "quad":
            ms.meshing_isotropic_explicit_remeshing(
                targetlen=pymeshlab.PureValue(target_edge_length),
                iterations=3,
            )
            try:
                ms.generate_polygonal_mesh()
                ms.meshing_poly_to_tri()
            except Exception:
                pass

        progress(80, "Exporting…")
        ms.save_current_mesh(ply_out)
        result = trimesh.load(ply_out, force="mesh")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    result.export(out_path)
    log(f"Output: {out_path} ({len(result.faces)} faces)")
    progress(100, "Done")
    done(out_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        error(f"{exc}\n{traceback.format_exc()}")
