"""Lightweight textured 3D previews for the asset library.

Meshy-style cards render a small interactive 3D preview client-side with
three.js. The server produces a web-friendly GLB once per asset — geometry
simplified to a few tens of thousands of triangles and textures downscaled —
so the browser downloads a few hundred KB instead of the full mesh (which
can easily be 100MB+).

Cached next to the workspace; the cache key includes mtime + size + render
version, so a preview is invalidated exactly when the source file changes.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Optional

from services.mesh_utils import face_count
from services.runtime_paths import runtime_paths
from services.workspace_paths import resolve_workspace_path

_MAX_SOURCE_BYTES = 256 * 1024 * 1024
_MAX_SOURCE_FACES = 8_000_000
_TARGET_FACES = 30000
_MAX_TEX = 512
_PREVIEW_LOCK = threading.Lock()
_PREWARM_LOCK = threading.Lock()
_PREWARM_RUNNING = False
_PREVIEW_VERSION = 1

_ROOTS = {"Workflows"}


def _preview_dir() -> Path:
    directory = runtime_paths.workspace / "thumbnails" / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cache_key(workspace_path: str, mtime_ns: int, size: int) -> str:
    raw = f"{workspace_path}:{mtime_ns}:{size}:v{_PREVIEW_VERSION}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + ".glb"


def _resolve(workspace_path: str) -> Optional[Path]:
    normalized = str(workspace_path or "").replace("\\", "/").strip()
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts or parts[0] not in _ROOTS or any(part == ".." for part in parts):
        return None
    return resolve_workspace_path(runtime_paths.workspace, "/".join(parts))


def _generate(mesh_path: Path, out_glb: Path) -> None:
    import trimesh

    try:
        import fast_simplification as fs
    except ImportError:
        fs = None

    scene = trimesh.load(str(mesh_path), force="scene")
    mesh = next((geometry for geometry in scene.geometry.values() if isinstance(geometry, trimesh.Trimesh)), None)
    if mesh is None or len(mesh.faces) == 0:
        raise ValueError("Mesh has no triangle geometry")

    visual = getattr(mesh, "visual", None)
    uv = getattr(visual, "uv", None) if visual is not None else None
    verts, faces = mesh.vertices, mesh.faces

    if fs is not None and len(faces) > _TARGET_FACES:
        target_verts = max(2000, int(_TARGET_FACES * 0.5))
        verts, faces = fs.simplify(verts, faces, target_count=target_verts, agg=7)

    simplified = trimesh.Trimesh(vertices=verts, faces=faces, process=False)

    def _nearest_indices(source_verts, target_verts):
        from scipy.spatial import cKDTree

        tree = cKDTree(source_verts)
        _, indices = tree.query(target_verts, k=1)
        return indices

    material = getattr(visual, "material", None) if visual is not None else None
    if uv is not None and material is not None and getattr(material, "baseColorTexture", None) is not None:
        uv_out = uv if len(verts) == len(mesh.vertices) else uv[_nearest_indices(mesh.vertices, verts)]
        texture = material.baseColorTexture
        if max(texture.size) > _MAX_TEX:
            texture = texture.convert("RGB").resize((_MAX_TEX, _MAX_TEX))
        material = trimesh.visual.material.PBRMaterial(
            baseColorTexture=texture,
            baseColorFactor=getattr(material, "baseColorFactor", None),
            metallicFactor=getattr(material, "metallicFactor", 0.0),
            roughnessFactor=getattr(material, "roughnessFactor", 1.0),
        )
        simplified.visual = trimesh.visual.TextureVisuals(uv=uv_out, material=material)
    elif isinstance(visual, trimesh.visual.ColorVisuals):
        colors = getattr(visual, "vertex_colors", None)
        if colors is not None and len(colors) == len(mesh.vertices):
            colors = colors if len(verts) == len(mesh.vertices) else colors[_nearest_indices(mesh.vertices, verts)]
            simplified.visual = trimesh.visual.ColorVisuals(vertex_colors=colors)

    simplified.vertex_normals
    out_glb.parent.mkdir(parents=True, exist_ok=True)
    simplified.export(str(out_glb))


def invalidate(workspace_path: str, mesh_path: Path) -> None:
    try:
        stat = mesh_path.stat()
        key = _cache_key(workspace_path, stat.st_mtime_ns, stat.st_size)
        for name in (key, key + ".failed"):
            cached = _preview_dir() / name
            if cached.is_file():
                cached.unlink()
    except OSError:
        pass


def ensure_preview(workspace_path: str, mesh_path: Path) -> Optional[Path]:
    try:
        stat = mesh_path.stat()
    except OSError:
        return None
    if stat.st_size > _MAX_SOURCE_BYTES or stat.st_size == 0:
        return None
    if mesh_path.suffix.lower().lstrip(".") not in {"glb", "gltf"}:
        return None
    if mesh_path.suffix.lower() == ".glb":
        try:
            with mesh_path.open("rb") as source:
                if source.read(4) != b"glTF":
                    return None
        except OSError:
            return None
    faces = face_count(mesh_path)
    if faces is not None and faces > _MAX_SOURCE_FACES:
        return None

    key = _cache_key(workspace_path, stat.st_mtime_ns, stat.st_size)

    with _PREVIEW_LOCK:
        # Resolve the cache directory only after confirming the source still
        # exists. This prevents a daemon prewarm from recreating a temporary
        # workspace after its owner has started cleanup.
        if not mesh_path.is_file():
            return None
        cached = _preview_dir() / key
        failed = _preview_dir() / (key + ".failed")
        if cached.is_file():
            return cached
        if failed.is_file():
            return None
        try:
            _generate(mesh_path, cached)
        except Exception as exc:
            if not mesh_path.is_file():
                return None
            print(f"[Previews] generation failed for {workspace_path}: {exc}")
            try:
                failed.write_text("failed", encoding="utf-8")
            except OSError:
                pass
            return None
    return cached if cached.is_file() else None


def start_prewarm(workspace_paths: list[str]) -> None:
    global _PREWARM_RUNNING
    pending = [path for path in workspace_paths if path]
    if not pending:
        return
    with _PREWARM_LOCK:
        if _PREWARM_RUNNING:
            return
        _PREWARM_RUNNING = True
    threading.Thread(target=_prewarm_worker, args=(pending,), daemon=True).start()


def _prewarm_worker(workspace_paths: list[str]) -> None:
    global _PREWARM_RUNNING
    try:
        for workspace_path in workspace_paths:
            mesh_path = _resolve(workspace_path)
            if mesh_path is None:
                continue
            try:
                ensure_preview(workspace_path, mesh_path)
            except Exception:
                continue
    finally:
        with _PREWARM_LOCK:
            _PREWARM_RUNNING = False
