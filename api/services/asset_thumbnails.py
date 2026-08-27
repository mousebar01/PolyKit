"""Headless 3D-asset thumbnail rendering.

Renders a mesh (GLB/GLTF/OBJ/STL/PLY) to a small static front-facing PNG
thumbnail, cached next to the workspace so the library grid can show real previews
without re-rendering on every list call. Open3D is preferred when available; a
small Pillow/NumPy software rasterizer keeps the Web path functional without EGL.

Textured meshes keep their base-color texture and UVs through the thumbnail
path. Untextured meshes continue to use the neutral fallback material.

Cache key = workspace-relative path + mtime_ns + size, so a thumbnail is
invalidated exactly when the source file changes.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from services.mesh_utils import face_count
from services.runtime_paths import runtime_paths

_DEFAULT_SIZE = 256
_LIBRARY_SIZE = 192  # card media is ~112–144px; avoid decoding oversized images
_MAX_SOURCE_BYTES = 512 * 1024 * 1024  # keep thumbnail work bounded while covering large GLBs
_MAX_RENDER_FACES = 2_000_000          # serialize heavy renders; fallback above full-render ceiling
# Open3D can render the source mesh without a geometry reduction, but a
# multi-million-face asset can temporarily use several GB while the renderer
# uploads its buffers. Keep the full-fidelity path bounded and serialize those
# heavy child processes so opening a library with many large assets cannot
# exhaust the host.
_MAX_FULL_RENDER_FACES = 8_000_000
_HEAVY_RENDER_LOCK = threading.BoundedSemaphore(1)
_MAX_TEXTURE_SIZE = 512
# Neutral studio gray for untextured meshes; pure white clips against the
# default key/fill rig and makes silhouette detail disappear in cards.
_FALLBACK_COLOR = [0.52, 0.56, 0.62, 1.0]
# Dark authored textures need a little lift after the renderer's ACES pass to
# remain legible at card size.  This is an exposure ceiling, not a blanket
# brightness multiplier: already bright assets are left unchanged.
_DARK_THUMBNAIL_EXPOSURE_MAX = 2.8
_DARK_THUMBNAIL_LUMA_TARGET = 0.18
# Open3D offscreen renders run in an isolated child process with a hard
# timeout, so a wedged EGL/GL context can never block the API (and with it the
# browser's connection pool) the way a process-wide render lock once did.
_RENDER_TIMEOUT_S = 120
# Bump when the render pipeline changes (framing, lighting, materials…) so
# stale cached thumbnails are regenerated instead of being served forever.
_RENDER_VERSION = 22

# Thumbnail work is deliberately separate from the model-generation executor.
# Two workers keep ordinary uploads responsive while the heavy-render semaphore
# below still serializes multi-million-face assets.  The executor is only used
# for fire-and-forget prewarming; visible requests continue to use the normal
# on-demand path when a prewarm fails.
_PREWARM_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="polykit-thumbnail")
_PREWARM_LOCK = threading.Lock()
_PREWARM_IN_FLIGHT: set[str] = set()
_PREWARM_FAILURE_UNTIL: dict[str, float] = {}
_PREWARM_RETRY_DELAY_S = 0.2
_PREWARM_FAILURE_COOLDOWN_S = 30.0

# On-demand requests and background prewarming share this lock map.  Without
# it, a card request arriving while the prewarm is still running can launch a
# second Open3D child for the same source and consume another GPU context.
_CACHE_LOCKS_GUARD = threading.Lock()
_CACHE_LOCKS: dict[str, threading.Lock] = {}


def _cache_dir() -> Path:
    d = runtime_paths.workspace / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(workspace_path: str, mtime_ns: int, size: int, px: int) -> str:
    raw = f"{workspace_path}:{mtime_ns}:{size}:{px}:v{_RENDER_VERSION}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + ".png"


def _cache_lock(cached: Path) -> threading.Lock:
    """Return the process-local lock for one immutable cache artifact."""
    lock_key = str(cached)
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.Lock()
            _CACHE_LOCKS[lock_key] = lock
        return lock


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

    try:
        import fast_simplification as fs
    except ImportError:
        # The software renderer below can draw the original geometry; do not
        # make thumbnail availability depend on an optional decimator.
        return vertices, faces, None

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


def _srgb_to_linear(value):
    import numpy as np

    values = np.clip(np.asarray(value, dtype=np.float64), 0.0, 1.0)
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(value):
    import numpy as np

    values = np.clip(np.asarray(value, dtype=np.float64), 0.0, 1.0)
    return np.where(values <= 0.0031308, values * 12.92, 1.055 * (values ** (1.0 / 2.4)) - 0.055)


def _aces_filmic_tonemap(value):
    """Apply the same ACES filmic curve as the WebGL viewer.

    React Three Fiber configures a non-flat canvas with
    ``ACESFilmicToneMapping`` by default.  The software thumbnail path used to
    convert linear light straight to sRGB, which clipped any highlight above
    1.0 to pure white and made the card image visibly brighter than the model
    in the viewer.  Keep this implementation vectorised so it is cheap for a
    small raster while preserving Three.js' channel-preserving ACES fit.
    """
    import numpy as np

    colors = np.asarray(value, dtype=np.float64)
    input_matrix = np.asarray(
        [
            [0.59719, 0.35458, 0.04823],
            [0.07600, 0.90834, 0.01566],
            [0.02840, 0.13383, 0.83777],
        ],
        dtype=np.float64,
    )
    output_matrix = np.asarray(
        [
            [1.60475, -0.53108, -0.07367],
            [-0.10208, 1.10813, -0.00605],
            [-0.00327, -0.07276, 1.07602],
        ],
        dtype=np.float64,
    )

    # Three.js applies toneMappingExposure / 0.6 before the ACES RRT/ODT fit.
    transformed = np.matmul(colors, input_matrix.T) / 0.6
    a = transformed * (transformed + 0.0245786) - 0.000090537
    b = transformed * (0.983729 * transformed + 0.4329510) + 0.238081
    transformed = np.divide(a, b, out=np.zeros_like(a), where=np.abs(b) > 1e-8)
    transformed = np.matmul(transformed, output_matrix.T)
    return np.clip(transformed, 0.0, 1.0)


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


def _save_open3d_image(rendered, out_png: Path, target_size: Optional[int] = None) -> None:
    """Save an Open3D render as RGBA, with a color-key fallback if needed."""
    import numpy as np
    from PIL import Image

    pixels = np.asarray(rendered)
    if pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        raise ValueError("Unexpected Open3D image shape")
    pixels = np.clip(pixels, 0, 255).astype(np.uint8)
    rgb = pixels[:, :, :3]

    if pixels.shape[2] == 4 and not np.all(pixels[:, :, 3] == 255):
        image = Image.fromarray(pixels, mode="RGBA")
    else:
        # Some Open3D builds expose an RGB image even when the scene background
        # alpha is zero. Remove only the uniform corner background in that case.
        corners = np.asarray([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]], dtype=np.float32)
        background = np.median(corners, axis=0)
        distance = np.linalg.norm(rgb.astype(np.float32) - background, axis=2)
        alpha = np.clip((distance - 3.0) * 32.0, 0.0, 255.0).astype(np.uint8)
        rgba = np.dstack((rgb, alpha))
        image = Image.fromarray(rgba, mode="RGBA")

    if target_size is not None and image.size != (target_size, target_size):
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        image = image.resize((target_size, target_size), resampling)

    image = _lift_dark_thumbnail(image)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_png)


def _lift_dark_thumbnail(image):
    """Lift only genuinely dark subject pixels after tone mapping.

    Open3D and the software fallback both return an sRGB image.  Applying a
    small linear-light exposure to the foreground (rather than multiplying
    sRGB bytes) keeps the transparent background black and preserves ACES'
    highlight roll-off.  Bright materials therefore stay below the old
    clipped-white look while dark textured assets remain readable in a 192px
    card.
    """
    import numpy as np
    from PIL import Image

    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    rgb = rgba[:, :, :3].astype(np.float64) / 255.0
    alpha = rgba[:, :, 3]
    if np.any(alpha > 10):
        subject = alpha > 10
    else:
        corners = np.asarray([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]], dtype=np.float64)
        background = np.median(corners, axis=0)
        subject = np.linalg.norm(rgb - background, axis=2) > (3.0 / 255.0)
    if not np.any(subject):
        return image

    linear = _srgb_to_linear(rgb)
    luminance = linear[subject] @ np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float64)
    p90 = float(np.percentile(luminance, 90)) if luminance.size else 0.0
    if p90 <= 1e-5 or p90 >= _DARK_THUMBNAIL_LUMA_TARGET:
        return image

    exposure = min(_DARK_THUMBNAIL_EXPOSURE_MAX, _DARK_THUMBNAIL_LUMA_TARGET / p90)
    lifted = _linear_to_srgb(np.clip(linear * exposure, 0.0, 1.0))
    rgba[:, :, :3] = np.clip(lifted * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def _configure_open3d_lighting(renderer, o3d, np) -> None:
    """Use a no-shadow studio rig close to Viewer3D's ambient/key/fill setup."""
    try:
        renderer.scene.set_lighting(
            o3d.visualization.rendering.Open3DScene.LightingProfile.NO_SHADOWS,
            np.array([0.45, 0.72, 0.45], dtype=np.float32),
        )
    except Exception:
        # set_lighting is unavailable in older Open3D releases; their default
        # profile is still a valid fallback for the thumbnail.
        pass


def _software_texture_payload(tm, render_vertices, render_faces, source_vertices):
    """Return texture pixels and per-face UVs for the software rasterizer."""
    import numpy as np
    from PIL import Image

    visual = getattr(tm, "visual", None)
    material = getattr(visual, "material", None) if visual is not None else None
    uv = getattr(visual, "uv", None) if visual is not None else None
    texture = getattr(material, "baseColorTexture", None) if material is not None else None
    if texture is None and visual is not None:
        texture = getattr(visual, "image", None)
    if uv is None or texture is None or source_vertices is False:
        return None

    uv_array = np.asarray(uv, dtype=np.float64)
    if source_vertices is not None:
        source_vertices = np.asarray(source_vertices, dtype=np.int64)
        if source_vertices.size != len(render_vertices) or source_vertices.size == 0:
            return None
        if int(np.max(source_vertices)) >= len(uv_array):
            return None
        uv_array = uv_array[source_vertices]
    if uv_array.ndim != 2 or uv_array.shape[0] != len(render_vertices) or uv_array.shape[1] < 2:
        return None

    if hasattr(texture, "convert"):
        image = texture.convert("RGBA")
    else:
        image = Image.fromarray(np.asarray(texture)).convert("RGBA")
    width, height = image.size
    largest = max(width, height)
    if largest > _MAX_TEXTURE_SIZE:
        scale = _MAX_TEXTURE_SIZE / float(largest)
        image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))))

    base_color = _normalized_rgba(getattr(material, "baseColorFactor", None) if material is not None else None)
    uv_faces = uv_array[np.asarray(render_faces, dtype=np.int64)]
    return np.asarray(image, dtype=np.uint8), uv_faces[:, :, :2], np.asarray(base_color, dtype=np.float64)


def _load_pymeshlab_geometry(mesh_path: Path, expected_face_count: Optional[int] = None):
    """Load/decimate a mesh while retaining per-wedge UVs when possible."""
    try:
        import numpy as np
        import pymeshlab
        from PIL import Image
    except ImportError:
        return None

    try:
        from tempfile import TemporaryDirectory

        mesh_set = pymeshlab.MeshSet()
        mesh_set.load_new_mesh(str(mesh_path))
        mesh = mesh_set.current_mesh()
        if mesh.face_number() == 0:
            return None
        # PyMeshLab's GLB loader can expose only one primitive from a
        # multi-primitive scene. Fall back to trimesh's scene merge instead of
        # silently returning a thumbnail for only one part.
        if expected_face_count is not None and mesh.face_number() < max(1000, expected_face_count // 2):
            return None

        texture_pixels = None
        if mesh.has_wedge_tex_coord() and mesh.texture_number() > 0:
            with TemporaryDirectory(prefix="polykit-thumbnail-texture-") as temp_dir:
                texture_path = Path(temp_dir) / "texture.png"
                mesh.texture(0).save(str(texture_path))
                with Image.open(texture_path) as image:
                    image = image.convert("RGBA")
                    largest = max(image.size)
                    if largest > _MAX_TEXTURE_SIZE:
                        scale = _MAX_TEXTURE_SIZE / float(largest)
                        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
                    texture_pixels = np.asarray(image, dtype=np.uint8).copy()

        # Clustering is much faster than quadric edge-collapse for multi-million
        # face assets and retains the wedge UV component. The original texture
        # pixels above are reused because clustering may drop the texture handle.
        if mesh.face_number() > 180_000:
            try:
                mesh_set.meshing_decimation_clustering(threshold=pymeshlab.PureValue(0.02))
                mesh = mesh_set.current_mesh()
            except Exception:
                pass

        vertices = np.asarray(mesh.vertex_matrix(), dtype=np.float64)
        faces = np.asarray(mesh.face_matrix(), dtype=np.int64)
        uv_faces = None
        if texture_pixels is not None and mesh.has_wedge_tex_coord():
            wedge_uv = np.asarray(mesh.wedge_tex_coord_matrix(), dtype=np.float64)
            if wedge_uv.ndim == 2 and wedge_uv.shape[0] >= len(faces) * 3 and wedge_uv.shape[1] >= 2:
                uv_faces = wedge_uv[:len(faces) * 3, :2].reshape(-1, 3, 2)

        vertex_normals = None
        try:
            normals = np.asarray(mesh.vertex_normal_matrix(), dtype=np.float64)
            if normals.ndim == 2 and len(normals) == len(vertices) and normals.shape[1] >= 3:
                vertex_normals = normals[:, :3]
        except Exception:
            pass

        face_colors = None
        if mesh.has_vertex_color():
            colors = np.asarray(mesh.vertex_color_matrix(), dtype=np.float64)
            if colors.ndim == 2 and len(colors) == len(vertices) and colors.shape[1] >= 3:
                if float(np.max(colors)) > 1.0:
                    colors /= 255.0
                face_colors = colors[faces[:, :]].mean(axis=1)[:, :3]

        return {
            "vertices": vertices,
            "faces": faces,
            "uv_faces": uv_faces,
            "texture_pixels": texture_pixels,
            "face_colors": face_colors,
            "vertex_normals": vertex_normals,
        }
    except Exception as exc:
        print(f"[Thumbnails] PyMeshLab load unavailable; using trimesh fallback: {exc}")
        return None


def _render_software(mesh_path: Path, out_png: Path, px: int, expected_face_count: Optional[int] = None) -> None:
    """Render a small front-facing thumbnail without an OpenGL dependency.

    The production Web path must still produce a useful image when Open3D/EGL
    is not installed. This intentionally uses a small painter's rasterizer:
    thumbnails only need a readable silhouette, not a second full 3D viewer.
    """
    import numpy as np
    from PIL import Image
    import trimesh

    tm = None
    pymeshlab_geometry = _load_pymeshlab_geometry(mesh_path, expected_face_count)
    if pymeshlab_geometry is not None:
        vertices = pymeshlab_geometry["vertices"]
        faces = pymeshlab_geometry["faces"]
        texture_pixels = pymeshlab_geometry["texture_pixels"]
        texture_uv_faces = pymeshlab_geometry["uv_faces"]
        texture_payload = (
            texture_pixels,
            texture_uv_faces,
            np.ones(4, dtype=np.float64),
        ) if texture_pixels is not None and texture_uv_faces is not None else None
        face_colors = pymeshlab_geometry["face_colors"]
        vertex_normals = pymeshlab_geometry["vertex_normals"]
    else:
        scene = trimesh.load(str(mesh_path), force="scene")
        try:
            tm = scene.to_geometry()
        except Exception:
            tm = next((geometry for geometry in scene.geometry.values() if isinstance(geometry, trimesh.Trimesh)), None)
        if tm is None or len(tm.faces) == 0:
            raise ValueError("Mesh has no triangle geometry")
        vertices, faces, source_vertices = _simplify_geometry(tm.vertices, tm.faces)
        vertices = np.asarray(vertices, dtype=np.float64)
        faces = np.asarray(faces, dtype=np.int64)
        texture_payload = _software_texture_payload(tm, vertices, faces, source_vertices)
        face_colors = None
        vertex_normals = None

    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    valid = np.all((faces >= 0) & (faces < len(vertices)), axis=1)
    faces = faces[valid]
    if texture_payload is not None:
        texture_payload = (
            texture_payload[0],
            np.asarray(texture_payload[1])[valid],
            texture_payload[2],
        )
    if face_colors is not None:
        face_colors = np.asarray(face_colors)[valid]
    if len(faces) == 0:
        raise ValueError("Mesh has no valid triangles")

    # Keep software rendering bounded for very dense meshes when the optional
    # decimator is unavailable. Evenly sampling faces preserves a useful rough
    # silhouette at thumbnail resolution and avoids blocking the API.
    max_faces = 300_000 if texture_payload is not None else 120_000
    if len(faces) > max_faces:
        sampled_indices = np.linspace(0, len(faces) - 1, max_faces, dtype=np.int64)
        faces = faces[sampled_indices]
        if texture_payload is not None:
            texture_payload = (
                texture_payload[0],
                texture_payload[1][sampled_indices],
                texture_payload[2],
            )
        if face_colors is not None:
            face_colors = face_colors[sampled_indices]

    bounds_min = vertices.min(axis=0)
    bounds_max = vertices.max(axis=0)
    center = (bounds_min + bounds_max) * 0.5
    extent = bounds_max - bounds_min
    frame_extent = max(float(extent[0]), float(extent[1]), float(extent[2]) * 0.35, 1e-6)
    # Dense assets already spend most of their budget in mesh decoding; render
    # them at the requested card size. Small meshes still get 2x supersampling
    # for cleaner silhouette edges.
    render_px = max(128, int(px) * (2 if len(faces) <= 20_000 else 1))
    scale = render_px * 0.84 / frame_extent
    centered = vertices - center
    projected = np.column_stack((
        centered[:, 0] * scale + render_px * 0.5,
        render_px * 0.5 - centered[:, 1] * scale,
    ))

    triangles = centered[faces]
    face_normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    face_lengths = np.linalg.norm(face_normals, axis=1)
    face_lengths[face_lengths == 0] = 1.0
    face_normals /= face_lengths[:, None]
    if vertex_normals is None or np.asarray(vertex_normals).shape != vertices.shape:
        vertex_normals = np.zeros_like(vertices)
        np.add.at(vertex_normals, faces[:, 0], face_normals)
        np.add.at(vertex_normals, faces[:, 1], face_normals)
        np.add.at(vertex_normals, faces[:, 2], face_normals)
    vertex_normals = np.asarray(vertex_normals, dtype=np.float64)
    vertex_lengths = np.linalg.norm(vertex_normals, axis=1)
    vertex_lengths[vertex_lengths == 0] = 1.0
    vertex_normals /= vertex_lengths[:, None]
    # Keep the software path close to Viewer3D's default studio rig:
    # ambient 0.45, key [5, 8, 5] at 0.8, and fill [-4, 2, -4] at 0.35.
    # Double-sided materials flip back-face normals in Three.js, hence abs().
    key_light = np.array([5.0, 8.0, 5.0], dtype=np.float64)
    key_light /= np.linalg.norm(key_light)
    fill_light = np.array([-4.0, 2.0, -4.0], dtype=np.float64)
    fill_light /= np.linalg.norm(fill_light)

    if face_colors is None:
        visual = getattr(tm, "visual", None)
        vertex_colors = getattr(visual, "vertex_colors", None) if visual is not None else None
        if vertex_colors is not None:
            colors = np.asarray(vertex_colors, dtype=np.float64)
            if colors.ndim == 2 and len(colors) == len(vertices) and colors.shape[1] >= 3:
                if float(np.max(colors)) > 1.0:
                    colors /= 255.0
                face_colors = colors[faces[:, :]].mean(axis=1)[:, :3]
            else:
                face_colors = np.tile(np.asarray(_FALLBACK_COLOR[:3], dtype=np.float64), (len(faces), 1))
        else:
            material = getattr(visual, "material", None) if visual is not None else None
            material_color = getattr(material, "baseColorFactor", None) if material is not None else None
            base_color = _normalized_rgba(material_color) if material_color is not None else _FALLBACK_COLOR
            face_colors = np.tile(np.asarray(base_color[:3], dtype=np.float64), (len(faces), 1))

    face_colors = np.clip(np.asarray(face_colors, dtype=np.float64), 0.0, 1.0)
    texture_pixels = texture_uv = texture_base = None
    if texture_payload is not None:
        texture_pixels, texture_uv, texture_base = texture_payload

    # A small z-buffered triangle rasterizer keeps both the silhouette and the
    # original base-color texture when Open3D is unavailable. Rendering at 2x
    # and downsampling gives edges that remain clean at card size.
    canvas = np.zeros((render_px, render_px, 4), dtype=np.uint8)
    z_buffer = np.full((render_px, render_px), -np.inf, dtype=np.float64)
    depths = triangles[:, :, 2].mean(axis=1)
    for face_index in np.argsort(depths):
        face_vertices = faces[face_index]
        points = projected[face_vertices]
        denominator = (
            (points[1, 1] - points[2, 1]) * (points[0, 0] - points[2, 0])
            + (points[2, 0] - points[1, 0]) * (points[0, 1] - points[2, 1])
        )
        if abs(float(denominator)) < 1e-8:
            continue

        min_x = max(0, int(np.floor(np.min(points[:, 0]))))
        max_x = min(render_px - 1, int(np.ceil(np.max(points[:, 0]))))
        min_y = max(0, int(np.floor(np.min(points[:, 1]))))
        max_y = min(render_px - 1, int(np.ceil(np.max(points[:, 1]))))
        if min_x > max_x or min_y > max_y:
            continue

        grid_x, grid_y = np.meshgrid(
            np.arange(min_x, max_x + 1, dtype=np.float64),
            np.arange(min_y, max_y + 1, dtype=np.float64),
        )
        weight_0 = (
            (points[1, 1] - points[2, 1]) * (grid_x - points[2, 0])
            + (points[2, 0] - points[1, 0]) * (grid_y - points[2, 1])
        ) / denominator
        weight_1 = (
            (points[2, 1] - points[0, 1]) * (grid_x - points[2, 0])
            + (points[0, 0] - points[2, 0]) * (grid_y - points[2, 1])
        ) / denominator
        weight_2 = 1.0 - weight_0 - weight_1
        inside = (weight_0 >= 0.0) & (weight_1 >= 0.0) & (weight_2 >= 0.0)
        if not np.any(inside):
            continue

        face_depth = (
            weight_0 * centered[face_vertices[0], 2]
            + weight_1 * centered[face_vertices[1], 2]
            + weight_2 * centered[face_vertices[2], 2]
        )
        local_z = z_buffer[min_y:max_y + 1, min_x:max_x + 1]
        visible = inside & (face_depth >= local_z)
        if not np.any(visible):
            continue
        local_y, local_x = np.where(visible)
        pixel_y = local_y + min_y
        pixel_x = local_x + min_x
        visible_w0 = weight_0[visible]
        visible_w1 = weight_1[visible]
        visible_w2 = weight_2[visible]
        pixel_normals = (
            visible_w0[:, None] * vertex_normals[face_vertices[0]]
            + visible_w1[:, None] * vertex_normals[face_vertices[1]]
            + visible_w2[:, None] * vertex_normals[face_vertices[2]]
        )
        pixel_normal_lengths = np.linalg.norm(pixel_normals, axis=1)
        pixel_normal_lengths[pixel_normal_lengths == 0] = 1.0
        pixel_normals /= pixel_normal_lengths[:, None]
        pixel_shade = 0.45 + 0.8 * np.abs(pixel_normals @ key_light) + 0.35 * np.abs(pixel_normals @ fill_light)
        pixel_shade = np.clip(pixel_shade, 0.0, 1.5)

        if texture_pixels is not None and texture_uv is not None and texture_base is not None:
            uv_triangle = texture_uv[face_index]
            uv = (
                visible_w0[:, None] * uv_triangle[0]
                + visible_w1[:, None] * uv_triangle[1]
                + visible_w2[:, None] * uv_triangle[2]
            )
            tex_height, tex_width = texture_pixels.shape[:2]
            tex_x = np.clip(np.mod(uv[:, 0], 1.0) * (tex_width - 1), 0, tex_width - 1).astype(np.int64)
            # glTF uses the browser loader's top-left image convention. OBJ
            # exporters generally keep the legacy bottom-left V convention.
            texture_v = np.mod(uv[:, 1], 1.0)
            if mesh_path.suffix.lower() == ".obj":
                texture_v = 1.0 - texture_v
            tex_y = np.clip(texture_v * (tex_height - 1), 0, tex_height - 1).astype(np.int64)
            sampled = texture_pixels[tex_y, tex_x].astype(np.float64) / 255.0
            # glTF base-color images are sRGB while baseColorFactor is a
            # linear multiplier.  Decode the image before applying the factor
            # so the thumbnail follows the same color-management path as
            # GLTFLoader in the browser.
            base_color_linear = _srgb_to_linear(sampled[:, :3]) * texture_base[:3]
            shaded_linear = np.clip(base_color_linear * pixel_shade[:, None], 0.0, None)
            source_rgb = _linear_to_srgb(_aces_filmic_tonemap(shaded_linear))
            source_alpha = sampled[:, 3] * texture_base[3]
        else:
            base_color_linear = _srgb_to_linear(face_colors[face_index])
            shaded_linear = np.clip(base_color_linear * pixel_shade[:, None], 0.0, None)
            source_rgb = _linear_to_srgb(_aces_filmic_tonemap(shaded_linear))
            source_alpha = np.ones(len(pixel_x), dtype=np.float64)

        # Composite translucent texture pixels over what has already been drawn.
        destination = canvas[pixel_y, pixel_x].astype(np.float64) / 255.0
        source_alpha = np.clip(source_alpha, 0.0, 1.0)
        destination_alpha = destination[:, 3]
        output_alpha = source_alpha + destination_alpha * (1.0 - source_alpha)
        output_rgb = np.zeros_like(source_rgb)
        nonzero = output_alpha > 1e-6
        output_rgb[nonzero] = (
            source_rgb[nonzero] * source_alpha[nonzero, None]
            + destination[nonzero, :3] * destination_alpha[nonzero, None] * (1.0 - source_alpha[nonzero, None])
        ) / output_alpha[nonzero, None]
        canvas[pixel_y, pixel_x, :3] = np.clip(output_rgb * 255.0, 0.0, 255.0).astype(np.uint8)
        canvas[pixel_y, pixel_x, 3] = np.clip(output_alpha * 255.0, 0.0, 255.0).astype(np.uint8)
        z_buffer[pixel_y, pixel_x] = face_depth[visible]

    resampling = getattr(Image, "Resampling", Image).LANCZOS
    thumbnail = Image.fromarray(canvas, mode="RGBA").resize((int(px), int(px)), resampling)
    _lift_dark_thumbnail(thumbnail).save(out_png)


def _render_open3d_full(mesh_path: Path, out_png: Path, px: int) -> None:
    """Render the source model with Open3D, without reducing its geometry.

    ``read_triangle_model`` keeps all primitives, transforms, UVs and material
    records from a GLB/GLTF/OBJ.  The previous thumbnail path converted the
    asset through trimesh and decimated it before rendering, which made thin
    parts disappear and guaranteed a different silhouette from Viewer3D.
    Rendering at a small supersampled target and resizing the final image is
    both more faithful and cheaper than uploading a second browser preview.
    """
    import numpy as np
    import open3d as o3d

    model = o3d.io.read_triangle_model(str(mesh_path))
    if not model.meshes:
        raise ValueError("Mesh has no triangle geometry")

    # Generated untextured assets often have an implicit white material. Use
    # the same neutral studio fallback as the software path so cards retain
    # detail instead of turning into a clipped white silhouette. Explicit
    # textures and vertex colors remain untouched.
    for material_index, material in enumerate(model.materials):
        material.shader = "defaultLit"
        if material.albedo_img is None:
            has_vertex_colors = any(
                len(getattr(mesh_info.mesh, "vertex_colors", ())) > 0
                for mesh_info in model.meshes
                if mesh_info.material_idx == material_index
            )
            if not has_vertex_colors:
                material.base_color = _FALLBACK_COLOR

    for mesh_info in model.meshes:
        mesh = mesh_info.mesh
        if len(mesh.vertices) == 0 or len(mesh.triangles) == 0:
            continue
        # GLTFLoader uses authored vertex normals when present. Recomputing
        # them here would be both expensive for multi-million-face assets and
        # another source of shading differences between the card and viewer.
        if len(mesh.vertex_normals) != len(mesh.vertices):
            mesh.compute_vertex_normals()

    # Keep the same bounded framing as the decimated renderer, but render at
    # 2x for cleaner edges before reducing to the card's requested size.
    render_px = max(128, int(px) * 2)
    renderer = o3d.visualization.rendering.OffscreenRenderer(render_px, render_px)
    renderer.scene.add_model("mesh", model)
    renderer.scene.set_background([0.06, 0.06, 0.07, 0.0])
    _configure_open3d_lighting(renderer, o3d, np)
    try:
        grading = o3d.visualization.rendering.ColorGrading(
            o3d.visualization.rendering.ColorGrading.Quality.HIGH,
            o3d.visualization.rendering.ColorGrading.ToneMapping.ACES,
        )
        renderer.scene.view.set_color_grading(grading)
    except Exception:
        pass

    bbox = renderer.scene.bounding_box
    center = bbox.get_center()
    diag = float(np.linalg.norm(np.asarray(bbox.get_extent()))) or 1.0
    view_dir = np.array([0.0, 0.0, 1.0])
    eye = center + view_dir * (diag * 1.35)
    renderer.setup_camera(50.0, center, eye, np.array([0.0, 1.0, 0.0]))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img = renderer.render_to_image()
    _save_open3d_image(img, out_png, int(px))


def _render_open3d(mesh_path: Path, out_png: Path, px: int) -> None:
    import numpy as np
    import open3d as o3d

    # Load with trimesh (fast, low-memory) and decimate with fast_simplification
    # instead of pulling the full mesh into Open3D — a 200MB scan would otherwise
    # take minutes and several GB of RAM just to produce a 256px thumbnail.
    import trimesh

    scene = trimesh.load(str(mesh_path), force="scene")
    try:
        tm = scene.to_geometry()
    except Exception:
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

    # Supersample the thumbnail, then downsample with Lanczos. This preserves
    # thin parts and diagonal edges without changing the source geometry.
    render_px = max(128, int(px) * 2 if len(faces) <= 200_000 else int(px))
    renderer = o3d.visualization.rendering.OffscreenRenderer(render_px, render_px)
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
    renderer.scene.set_background([0.06, 0.06, 0.07, 0.0])
    _configure_open3d_lighting(renderer, o3d, np)
    try:
        grading = o3d.visualization.rendering.ColorGrading(
            o3d.visualization.rendering.ColorGrading.Quality.HIGH,
            o3d.visualization.rendering.ColorGrading.ToneMapping.ACES,
        )
        renderer.scene.view.set_color_grading(grading)
    except Exception:
        # Older Open3D builds do not expose the color-grading API. Their
        # defaultLit renderer still produces a valid thumbnail, so keep the
        # compatibility fallback rather than failing the request.
        pass

    # Frame the whole model in a straight-on front view. Generated models are
    # exported Y-up with their front facing +Z; keeping the camera on +Z gives
    # the library a stable, comparable silhouette instead of a rotating hero
    # angle. The shorter distance makes the model readable at card size while
    # the diagonal-based margin keeps tall and wide assets fully visible.
    bbox = mesh.get_axis_aligned_bounding_box()
    center = bbox.get_center()
    diag = float(np.linalg.norm(np.asarray(bbox.get_extent()))) or 1.0
    view_dir = np.array([0.0, 0.0, 1.0])
    eye = center + view_dir * (diag * 1.35)
    renderer.setup_camera(50.0, center, eye, np.array([0.0, 1.0, 0.0]))

    # Static front-facing thumbnail. The URL is versioned above so existing
    # cached 3/4 thumbnails are regenerated on the next library load.
    out_png.parent.mkdir(parents=True, exist_ok=True)
    # np.array() copies the Open3D image buffer; np.asarray() returns a view
    # that makes PIL's fromarray take seconds per frame.
    img = renderer.render_to_image()
    _save_open3d_image(img, out_png, int(px))


def _render(mesh_path: Path, out_png: Path, px: int) -> None:
    """Prefer full-fidelity Open3D; keep a bounded fallback for huge assets."""
    try:
        faces = face_count(mesh_path)
    except Exception:
        faces = None

    # A real render of the original mesh is the default.  Downsampling the
    # resulting image preserves the asset's silhouette and texture far better
    # than reducing geometry first.  Only assets beyond the explicit safety
    # ceiling take the bounded path below.
    if faces is None or faces <= _MAX_FULL_RENDER_FACES:
        try:
            _render_open3d_full(mesh_path, out_png, px)
            return
        except Exception as exc:
            print(f"[Thumbnails] Full Open3D render unavailable; trying fallback: {exc}")

    if faces is not None and faces > _MAX_RENDER_FACES:
        print(f"[Thumbnails] {mesh_path.name} has {faces} faces; using bounded software rendering")
        _render_software(mesh_path, out_png, px, faces)
        return
    try:
        _render_open3d(mesh_path, out_png, px)
    except Exception as exc:
        print(f"[Thumbnails] Open3D render unavailable; using software fallback: {exc}")
        _render_software(mesh_path, out_png, px, faces)


def invalidate(workspace_path: str, mesh_path: Path) -> None:
    """Drop cached thumbnails for a workspace mesh (called before delete/rename)."""
    try:
        stat = mesh_path.stat()
        for size in {_DEFAULT_SIZE, _LIBRARY_SIZE}:
            cached = _cache_dir() / _cache_key(workspace_path, stat.st_mtime_ns, stat.st_size, size)
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
        completed = subprocess.run(
            [sys.executable, "-c", script, str(mesh_path), str(out_png), str(px)],
            cwd=str(Path(__file__).parent.parent),
            timeout=_RENDER_TIMEOUT_S,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            if detail:
                print(f"[Thumbnails] render subprocess exited {completed.returncode}: {detail[-1]}")
            return False
        return out_png.is_file()
    except subprocess.TimeoutExpired:
        print(f"[Thumbnails] render timed out (>{_RENDER_TIMEOUT_S}s): {mesh_path.name}")
        return False
    except Exception as exc:
        print(f"[Thumbnails] render subprocess failed: {exc}")
        return False


def _prewarm_worker(workspace_path: str, mesh_path: Path, px: int, cache_key: str) -> None:
    """Render one thumbnail in the bounded background pool.

    A single retry covers transient EGL startup failures without turning a bad
    asset into a tight retry loop.  A short cooldown then lets a later visible
    request try again naturally (and keeps the fallback icon available).
    """
    rendered = False
    try:
        for attempt in range(2):
            try:
                rendered = ensure_thumbnail(workspace_path, mesh_path, px) is not None
            except Exception as exc:
                print(f"[Thumbnails] prewarm failed for {mesh_path.name}: {exc}")
                rendered = False
            if rendered or attempt == 1:
                break
            time.sleep(_PREWARM_RETRY_DELAY_S)
    finally:
        with _PREWARM_LOCK:
            _PREWARM_IN_FLIGHT.discard(cache_key)
            if rendered:
                _PREWARM_FAILURE_UNTIL.pop(cache_key, None)
            else:
                _PREWARM_FAILURE_UNTIL[cache_key] = time.monotonic() + _PREWARM_FAILURE_COOLDOWN_S


def prewarm_thumbnail(workspace_path: str, mesh_path: Path, px: int = _LIBRARY_SIZE) -> None:
    """Schedule a best-effort thumbnail render without blocking the caller.

    This is intentionally a no-op for unsupported/missing/oversized files.
    The cache fingerprint includes source mtime/size and render version, so a
    changed model gets a new task while unchanged assets are deduplicated.
    """
    try:
        stat = mesh_path.stat()
    except OSError:
        return
    if stat.st_size == 0 or stat.st_size > _MAX_SOURCE_BYTES:
        return
    if mesh_path.suffix.lower().lstrip(".") not in {"glb", "gltf", "obj", "stl", "ply"}:
        return

    try:
        cached = _cache_dir() / _cache_key(workspace_path, stat.st_mtime_ns, stat.st_size, int(px))
    except OSError:
        return
    if cached.is_file():
        return
    cache_key = str(cached)
    now = time.monotonic()
    with _PREWARM_LOCK:
        if cached.is_file() or cache_key in _PREWARM_IN_FLIGHT:
            return
        if _PREWARM_FAILURE_UNTIL.get(cache_key, 0.0) > now:
            return
        _PREWARM_IN_FLIGHT.add(cache_key)
    try:
        _PREWARM_EXECUTOR.submit(_prewarm_worker, workspace_path, mesh_path, int(px), cache_key)
    except RuntimeError:
        # The interpreter may be shutting down.  Do not make an upload or
        # generation request fail just because the optional prewarm pool closed.
        with _PREWARM_LOCK:
            _PREWARM_IN_FLIGHT.discard(cache_key)


def ensure_thumbnail(workspace_path: str, mesh_path: Path, px: int = _DEFAULT_SIZE) -> Optional[Path]:
    """Return a cached/render PNG for the mesh, or None if it can't be rendered."""
    try:
        stat = mesh_path.stat()
    except OSError:
        return None
    key = _cache_key(workspace_path, stat.st_mtime_ns, stat.st_size, px)
    cached = _cache_dir() / key
    if stat.st_size > _MAX_SOURCE_BYTES or stat.st_size == 0:
        return None
    if not mesh_path.suffix.lower().lstrip(".") in {"glb", "gltf", "obj", "stl", "ply"}:
        return None
    # The lock is shared by lazy requests and prewarm workers.  Rendering into
    # a unique .png beside the final key and replacing it atomically prevents a
    # browser request from reading a partially-written PNG.
    with _cache_lock(cached):
        if cached.is_file():
            return cached  # another request/prewarm may have completed it

        faces = face_count(mesh_path)
        # Very dense assets are still eligible: the renderer caps the face count
        # before rasterizing so the library shows a real silhouette instead of
        # silently falling back to the generic mesh icon.
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{cached.stem}.",
            suffix=".png",
            dir=str(cached.parent),
        )
        os.close(fd)
        temporary = Path(temp_name)
        try:
            # Renders still run in isolated children with hard timeouts, but full
            # multi-million-face Open3D renders can use several GB during upload.
            # A single heavy render at a time keeps concurrent lazy card requests
            # from exhausting the host while leaving small assets fully parallel.
            if faces is not None and faces > _MAX_RENDER_FACES:
                with _HEAVY_RENDER_LOCK:
                    if cached.is_file():
                        return cached
                    rendered = _render_isolated(mesh_path, temporary, px)
            else:
                rendered = _render_isolated(mesh_path, temporary, px)
            if not rendered or not temporary.is_file() or temporary.stat().st_size == 0:
                return None
            os.replace(temporary, cached)
            return cached
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
