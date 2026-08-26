"""Headless 3D-asset thumbnail rendering.

Renders a mesh (GLB/GLTF/OBJ/STL/PLY) to a small static PNG hero view
using Open3D's EGL/OSMesa offscreen renderer, cached next to the workspace so the library
grid can show real previews without re-rendering on every list call.

Textured meshes keep their base-color texture and UVs through the thumbnail
path. Untextured meshes continue to use the neutral fallback material.

Cache key = workspace-relative path + mtime_ns + size, so a thumbnail is
invalidated exactly when the source file changes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from services.mesh_utils import face_count
from services.runtime_paths import runtime_paths

_DEFAULT_SIZE = 256
_MAX_SOURCE_BYTES = 256 * 1024 * 1024  # skip monsters; the card falls back to an icon
_MAX_RENDER_FACES = 2_000_000          # bigger meshes keep the icon; thumbnails must stay fast
_MAX_TEXTURE_SIZE = 512
_FALLBACK_COLOR = [0.72, 0.76, 0.8, 1.0]
# Open3D offscreen renders run in an isolated child process with a hard
# timeout, so a wedged EGL/GL context can never block the API (and with it the
# browser's connection pool) the way a process-wide render lock once did.
_RENDER_TIMEOUT_S = 120
# Bump when the render pipeline changes (framing, lighting, materials…) so
# stale cached thumbnails are regenerated instead of being served forever.
_RENDER_VERSION = 7


def _cache_dir() -> Path:
    d = runtime_paths.workspace / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(workspace_path: str, mtime_ns: int, size: int, px: int) -> str:
    raw = f"{workspace_path}:{mtime_ns}:{size}:{px}:v{_RENDER_VERSION}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + ".png"


def _representative_source_vertices(index_mapping, output_count: int):
    """Invert an original->simplified vertex map to one source vertex per output vertex."""
    import numpy as np

    mapping = np.asarray(index_mapping, dtype=np.int64).reshape(-1)
    representatives = np.full(output_count, -1, dtype=np.int64)
    for source_index, output_index in enumerate(mapping):
        if 0 <= output_index < output_count and representatives[output_index] < 0:
            representatives[output_index] = source_index
    if np.any(representatives < 0):
        return None
    return representatives


def _simplify_geometry(vertices, faces):
    """Return render geometry plus optional source-vertex correspondence for UVs."""
    if len(faces) <= 40000:
        return vertices, faces, None

    import fast_simplification as fs

    # UV-correspondence replay runs np.unique over the collapse mapping and is
    # pathological on very large meshes (minutes for multi-million-vertex
    # scans). A 256px thumbnail does not need per-vertex UV truth at that
    # scale, so big meshes take the fast geometric-decimation path instead.
    _MAX_REPLAY_VERTICES = 250_000

    if len(vertices) <= _MAX_REPLAY_VERTICES:
        try:
            simplified = fs.simplify(
                vertices,
                faces,
                target_count=15000,
                agg=7,
                return_collapses=True,
            )
            if len(simplified) != 3:
                raise ValueError("fast_simplification did not return collapse data")
            out_vertices, out_faces, collapses = simplified
            _, _, index_mapping = fs.replay_simplification(vertices, faces, collapses)
            source_vertices = _representative_source_vertices(index_mapping, len(out_vertices))
            return out_vertices, out_faces, source_vertices
        except Exception:
            pass  # fall through to the fast path below

    # Fast path: geometric decimation only. Texture fidelity degrades to the
    # neutral fallback material, which is acceptable at thumbnail size.
    # Scale the target down for very large meshes so renders stay in seconds.
    target_count = 15000 if len(vertices) <= 500_000 else 8000
    try:
        out_vertices, out_faces = fs.simplify(vertices, faces, target_count=target_count, agg=7)
        return out_vertices, out_faces, False
    except Exception:
        return vertices, faces, None


def _normalized_rgba(value):
    import numpy as np

    if value is None:
        return [1.0, 1.0, 1.0, 1.0]
    values = np.asarray(value, dtype=np.float64).reshape(-1)
    if values.size < 3:
        return [1.0, 1.0, 1.0, 1.0]
    if float(np.max(values)) > 1.0:
        values = values / 255.0
    rgba = [float(values[0]), float(values[1]), float(values[2]), float(values[3]) if values.size >= 4 else 1.0]
    return [max(0.0, min(1.0, component)) for component in rgba]


def _texture_payload(tm, render_vertices, render_faces, source_vertices):
    """Return triangle UVs, texture pixels and PBR scalar factors when available."""
    import numpy as np

    visual = getattr(tm, "visual", None)
    material = getattr(visual, "material", None) if visual is not None else None
    uv = getattr(visual, "uv", None) if visual is not None else None
    texture = getattr(material, "baseColorTexture", None) if material is not None else None
    if uv is None or material is None or texture is None or source_vertices is False:
        return None

    uv_vertices = np.asarray(uv, dtype=np.float64)
    if source_vertices is not None:
        source_vertices = np.asarray(source_vertices, dtype=np.int64)
        if source_vertices.size != len(render_vertices) or source_vertices.size == 0:
            return None
        if int(np.max(source_vertices)) >= len(uv_vertices):
            return None
        uv_vertices = uv_vertices[source_vertices]
    if len(uv_vertices) != len(render_vertices):
        return None

    faces_array = np.asarray(render_faces, dtype=np.int64)
    triangle_uvs = uv_vertices[faces_array].reshape(-1, 2)

    if hasattr(texture, "convert"):
        image = texture.convert("RGB")
    else:
        from PIL import Image
        image = Image.fromarray(np.asarray(texture)).convert("RGB")
    width, height = image.size
    largest = max(width, height)
    if largest > _MAX_TEXTURE_SIZE:
        scale = _MAX_TEXTURE_SIZE / float(largest)
        image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))))

    return {
        "triangle_uvs": triangle_uvs,
        "pixels": np.asarray(image, dtype=np.uint8),
        "base_color": _normalized_rgba(getattr(material, "baseColorFactor", None)),
        "metallic": float(getattr(material, "metallicFactor", 0.0) or 0.0),
        "roughness": float(getattr(material, "roughnessFactor", 1.0) if getattr(material, "roughnessFactor", None) is not None else 1.0),
    }


def _render(mesh_path: Path, out_png: Path, px: int) -> None:
    import numpy as np
    import open3d as o3d
    from PIL import Image

    # Load with trimesh (fast, low-memory) and decimate with fast_simplification
    # instead of pulling the full mesh into Open3D — a 200MB scan would otherwise
    # take minutes and several GB of RAM just to produce a 256px thumbnail.
    import trimesh

    scene = trimesh.load(str(mesh_path), force="scene")
    tm = next((g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)), None)
    if tm is None or len(tm.faces) == 0:
        raise ValueError("Mesh has no triangle geometry")

    verts, faces, source_vertices = _simplify_geometry(tm.vertices, tm.faces)
    texture_payload = _texture_payload(tm, verts, faces, source_vertices)

    mesh = o3d.geometry.TriangleMesh(
        vertices=o3d.utility.Vector3dVector(np.asarray(verts, dtype=np.float64)),
        triangles=o3d.utility.Vector3iVector(np.asarray(faces, dtype=np.int32)),
    )
    if len(mesh.vertices) == 0:
        raise ValueError("Mesh has no vertices")
    if texture_payload is not None:
        mesh.triangle_uvs = o3d.utility.Vector2dVector(texture_payload["triangle_uvs"])
    mesh.compute_vertex_normals()

    verts = np.asarray(mesh.vertices)
    mesh.translate(-verts.mean(axis=0))
    scale = float(np.max(np.abs(np.asarray(mesh.vertices)))) if len(verts) else 0.0
    if scale > 0:
        mesh.scale(1.0 / scale, center=(0, 0, 0))
    mesh.compute_vertex_normals()

    renderer = o3d.visualization.rendering.OffscreenRenderer(px, px)
    mat = o3d.visualization.rendering.MaterialRecord()
    mat.shader = "defaultLit"
    if texture_payload is not None:
        mat.albedo_img = o3d.geometry.Image(texture_payload["pixels"])
        mat.base_color = texture_payload["base_color"]
        mat.base_metallic = texture_payload["metallic"]
        mat.base_roughness = texture_payload["roughness"]
    else:
        mat.base_color = _FALLBACK_COLOR
    renderer.scene.add_geometry("mesh", mesh, mat)
    renderer.scene.set_background([0.06, 0.06, 0.07, 1.0])

    # Frame the whole model: 3/4 front-top view from a distance derived from
    # the bounding-box diagonal, so every asset is fully visible and centered.
    bbox = mesh.get_axis_aligned_bounding_box()
    center = bbox.get_center()
    diag = float(np.linalg.norm(np.asarray(bbox.get_extent()))) or 1.0
    # Model front is +Z; look from the +Z side (front-right-top 3/4 hero angle).
    view_dir = np.array([1.0, 0.7, 0.9])
    view_dir = view_dir / np.linalg.norm(view_dir)
    eye = center + view_dir * (diag * 2.0)
    renderer.setup_camera(50.0, center, eye, np.array([0.0, 1.0, 0.0]))

    # Static hero frame: generated models are exported Y-up with the front
    # facing +Z (remapped at export), so a 3/4 front-right-top view from the
    # +Z side shows the asset's front on every card. Hover reveals the full
    # turntable via the lightweight 3D preview.
    out_png.parent.mkdir(parents=True, exist_ok=True)
    # np.array() copies the Open3D image buffer; np.asarray() returns a view
    # that makes PIL's fromarray take seconds per frame.
    img = renderer.render_to_image()
    Image.fromarray(np.array(img)).save(out_png)


def invalidate(workspace_path: str, mesh_path: Path) -> None:
    """Drop cached thumbnails for a workspace mesh (called before delete/rename)."""
    try:
        stat = mesh_path.stat()
        cached = _cache_dir() / _cache_key(workspace_path, stat.st_mtime_ns, stat.st_size, _DEFAULT_SIZE)
        if cached.is_file():
            cached.unlink()
    except OSError:
        pass


def _render_worker(mesh_path: Path, out_png: Path, px: int) -> int:
    """Entry point run inside the isolated render child process."""
    try:
        _render(mesh_path, out_png, px)
        return 0
    except Exception as exc:
        print(f"[Thumbnails] render worker failed: {exc}")
        return 1


def _render_isolated(mesh_path: Path, out_png: Path, px: int) -> bool:
    """Render in a separate process with a hard timeout.

    Each child owns its own Open3D/EGL context, so a wedged render can never
    hold a server-side lock: the child is killed on timeout, the API keeps
    responding, and the next request simply retries the render.
    """
    import subprocess
    import sys

    script = (
        "from pathlib import Path\n"
        "from services.asset_thumbnails import _render_worker\n"
        "raise SystemExit(_render_worker(Path(__import__('sys').argv[1]), "
        "Path(__import__('sys').argv[2]), int(__import__('sys').argv[3])))"
    )
    try:
        subprocess.run(
            [sys.executable, "-c", script, str(mesh_path), str(out_png), str(px)],
            cwd=str(Path(__file__).parent.parent),
            timeout=_RENDER_TIMEOUT_S,
            capture_output=True,
            text=True,
        )
        return out_png.is_file()
    except subprocess.TimeoutExpired:
        print(f"[Thumbnails] render timed out (>{_RENDER_TIMEOUT_S}s): {mesh_path.name}")
        return False
    except Exception as exc:
        print(f"[Thumbnails] render subprocess failed: {exc}")
        return False


def ensure_thumbnail(workspace_path: str, mesh_path: Path, px: int = _DEFAULT_SIZE) -> Optional[Path]:
    """Return a cached/render PNG for the mesh, or None if it can't be rendered."""
    try:
        stat = mesh_path.stat()
    except OSError:
        return None
    key = _cache_key(workspace_path, stat.st_mtime_ns, stat.st_size, px)
    cached = _cache_dir() / key
    if cached.is_file():
        return cached  # already rendered before; caps only gate NEW renders

    if stat.st_size > _MAX_SOURCE_BYTES or stat.st_size == 0:
        return None
    if not mesh_path.suffix.lower().lstrip(".") in {"glb", "gltf", "obj", "stl", "ply"}:
        return None
    faces = face_count(mesh_path)
    if faces is not None and faces > _MAX_RENDER_FACES:
        return None  # would take minutes / many GB to render; the card keeps its icon

    # No process-wide lock: renders run in isolated children with hard
    # timeouts, so the library grid can never be blocked by one bad mesh.
    if _render_isolated(mesh_path, cached, px):
        return cached
    return None
