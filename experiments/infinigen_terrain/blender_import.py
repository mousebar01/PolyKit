"""Blender 5.2 importer for portable Infinigen terrain packages."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

import numpy as np

from .package import derive_surface_fields, load_terrain_package, resample_field

try:  # Keep package helpers importable outside Blender.
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ImportError:  # pragma: no cover - exercised in Blender.
    bpy = None
    Vector = None


@dataclass(frozen=True)
class ImportResult:
    object: object
    preset: str
    seed: int
    source_resolution: int
    mesh_resolution: int
    tile_size_m: float
    min_height: float
    max_height: float
    field_used: str


def _require_blender() -> None:
    if bpy is None or Vector is None:
        raise RuntimeError("blender_import must run inside Blender")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge1 <= edge0:
        return 0.0
    t = _clamp01((value - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _mix(a: tuple[float, float, float], b: tuple[float, float, float], t: float):
    t = _clamp01(t)
    return tuple(a[i] * (1.0 - t) + b[i] * t for i in range(3))


def _terrain_color(height01: float, slope01: float) -> tuple[float, float, float, float]:
    # Intentionally restrained diagnostic palette: terrain structure should do
    # the visual work, not an elaborate shader.
    low = (0.13, 0.23, 0.055)
    mid = (0.27, 0.34, 0.11)
    high = (0.37, 0.37, 0.25)
    rock = (0.31, 0.30, 0.29)
    summit = (0.58, 0.58, 0.52)

    base = _mix(low, mid, _smoothstep(0.08, 0.55, height01))
    base = _mix(base, high, _smoothstep(0.48, 0.82, height01))
    rock_weight = _smoothstep(0.34, 0.72, slope01)
    base = _mix(base, rock, rock_weight)
    summit_weight = _smoothstep(0.82, 0.96, height01) * (1.0 - 0.45 * rock_weight)
    base = _mix(base, summit, summit_weight * 0.55)
    return (base[0], base[1], base[2], 1.0)


def _write_float_attribute(mesh, name: str, values: np.ndarray) -> None:
    existing = mesh.attributes.get(name)
    if existing is not None:
        mesh.attributes.remove(existing)
    attr = mesh.attributes.new(name=name, type="FLOAT", domain="POINT")
    flat = np.asarray(values, dtype=np.float32).reshape(-1)
    try:
        attr.data.foreach_set("value", flat.tolist())
    except (AttributeError, TypeError, ValueError):
        for index, value in enumerate(flat):
            attr.data[index].value = float(value)


def _write_color_attribute(mesh, colors: list[tuple[float, float, float, float]]) -> None:
    existing = mesh.color_attributes.get("TerrainColor")
    if existing is not None:
        mesh.color_attributes.remove(existing)
    layer = mesh.color_attributes.new(
        name="TerrainColor",
        type="FLOAT_COLOR",
        domain="CORNER",
    )
    for loop in mesh.loops:
        layer.data[loop.index].color = colors[loop.vertex_index]


def _remove_previous(name: str) -> None:
    existing = bpy.data.objects.get(name)
    if existing is None:
        return
    data = existing.data if existing.type == "MESH" else None
    bpy.data.objects.remove(existing, do_unlink=True)
    if data is not None and data.users == 0:
        bpy.data.meshes.remove(data)


def _ensure_material(name: str):
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (420.0, 0.0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (120.0, 0.0)
    roughness = bsdf.inputs.get("Roughness")
    if roughness is not None:
        roughness.default_value = 0.82

    try:
        color = nodes.new("ShaderNodeVertexColor")
        color.layer_name = "TerrainColor"
    except RuntimeError:
        color = nodes.new("ShaderNodeAttribute")
        color.attribute_name = "TerrainColor"
    color.location = (-180.0, 0.0)
    links.new(color.outputs.get("Color"), bsdf.inputs.get("Base Color"))
    links.new(bsdf.outputs.get("BSDF"), output.inputs.get("Surface"))
    return material


def import_terrain_package(
    path: str | Path,
    *,
    name: str | None = None,
    target_resolution: int = 513,
    prefer_eroded: bool = True,
    vertical_scale: float = 1.0,
    center_height: bool = False,
) -> ImportResult:
    """Import one Infinigen package as an inspectable Blender terrain mesh."""
    _require_blender()
    package = load_terrain_package(path)
    field_used = "eroded_height" if prefer_eroded and package.eroded_height is not None else "raw_height"
    source_height = package.eroded_height if field_used == "eroded_height" else package.raw_height
    target_resolution = min(int(target_resolution), package.source_resolution)
    height = resample_field(source_height, target_resolution).astype(np.float32)
    if center_height:
        height = height - float(np.mean(height))
    height *= float(vertical_scale)

    tile_size = float(package.tile_size_m)
    fields = derive_surface_fields(height, tile_size)
    erosion_mask = None
    if package.erosion_mask is not None:
        erosion_mask = resample_field(package.erosion_mask, target_resolution)

    n = target_resolution
    half = tile_size * 0.5
    coords = np.linspace(-half, half, n, dtype=np.float32)
    vertices: list[tuple[float, float, float]] = []
    colors: list[tuple[float, float, float, float]] = []
    height01 = fields["height01"]
    slope01 = fields["slope01"]
    for row in range(n):
        y = float(coords[row])
        for column in range(n):
            x = float(coords[column])
            z = float(height[row, column])
            vertices.append((x, y, z))
            colors.append(
                _terrain_color(float(height01[row, column]), float(slope01[row, column]))
            )

    faces: list[tuple[int, int, int, int]] = []
    for row in range(n - 1):
        base = row * n
        next_base = (row + 1) * n
        for column in range(n - 1):
            a = base + column
            b = a + 1
            d = next_base + column
            c = d + 1
            faces.append((a, b, c, d))

    obj_name = name or f"Infinigen_{package.preset}_{package.seed}"
    _remove_previous(obj_name)
    mesh = bpy.data.meshes.new(f"{obj_name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for polygon in mesh.polygons:
        polygon.use_smooth = True

    for field_name, values in fields.items():
        _write_float_attribute(mesh, field_name, values)
    if erosion_mask is not None:
        _write_float_attribute(mesh, "erosion_mask", erosion_mask)
    _write_color_attribute(mesh, colors)

    obj = bpy.data.objects.new(obj_name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["infinigen_preset"] = package.preset
    obj["infinigen_seed"] = package.seed
    obj["infinigen_source_resolution"] = package.source_resolution
    obj["infinigen_mesh_resolution"] = n
    obj["infinigen_tile_size_m"] = tile_size
    obj["infinigen_field_used"] = field_used
    obj["infinigen_package"] = str(Path(path))
    mesh.materials.append(_ensure_material(f"{obj_name}_Material"))

    minimum = float(height.min())
    maximum = float(height.max())
    print(
        f"[PolyKit/Infinigen] imported {package.preset}: {n}x{n}, "
        f"{len(vertices)} verts, z={minimum:.2f}..{maximum:.2f}m, field={field_used}"
    )
    return ImportResult(
        object=obj,
        preset=package.preset,
        seed=package.seed,
        source_resolution=package.source_resolution,
        mesh_resolution=n,
        tile_size_m=tile_size,
        min_height=minimum,
        max_height=maximum,
        field_used=field_used,
    )


def _look_at(obj, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _remove_object_and_data(name: str) -> None:
    existing = bpy.data.objects.get(name)
    if existing is None:
        return
    data = existing.data
    object_type = existing.type
    bpy.data.objects.remove(existing, do_unlink=True)
    if data is None or data.users != 0:
        return
    if object_type == "LIGHT":
        bpy.data.lights.remove(data)
    elif object_type == "CAMERA":
        bpy.data.cameras.remove(data)


def setup_diagnostics(result: ImportResult) -> dict[str, object]:
    _require_blender()
    prefix = result.object.name
    size = result.tile_size_m
    vertical = max(15.0, result.max_height - result.min_height)
    target = (0.0, 0.0, result.min_height + vertical * 0.38)

    sun_name = f"{prefix}_Sun"
    _remove_object_and_data(sun_name)
    sun_data = bpy.data.lights.new(sun_name, type="SUN")
    sun_data.energy = 2.8
    sun_data.angle = math.radians(10.0)
    sun = bpy.data.objects.new(sun_name, sun_data)
    bpy.context.scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(37.0), math.radians(-24.0), math.radians(-38.0))

    area_name = f"{prefix}_Fill"
    _remove_object_and_data(area_name)
    area_data = bpy.data.lights.new(area_name, type="AREA")
    area_data.energy = 900.0
    area_data.shape = "DISK"
    area_data.size = size * 0.9
    area = bpy.data.objects.new(area_name, area_data)
    bpy.context.scene.collection.objects.link(area)
    area.location = (-size * 0.35, -size * 0.30, result.max_height + size * 0.50)
    _look_at(area, target)

    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.08, 0.12, 0.18, 1.0)
        background.inputs["Strength"].default_value = 0.55

    def camera(suffix: str, location, lens: float, target_point=target):
        name = f"{prefix}_Camera_{suffix}"
        _remove_object_and_data(name)
        data = bpy.data.cameras.new(name)
        data.lens = lens
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = location
        _look_at(obj, target_point)
        return obj

    return {
        "perspective": camera(
            "Perspective",
            (size * 0.72, -size * 1.02, result.max_height + size * 0.42),
            52.0,
        ),
        "top": camera(
            "Top",
            (0.0, 0.0, result.max_height + size * 1.15),
            52.0,
            (0.0, 0.0, result.min_height),
        ),
        "low": camera(
            "Low",
            (-size * 0.92, -size * 0.74, result.max_height + size * 0.16),
            58.0,
        ),
        "detail": camera(
            "Detail",
            (size * 0.28, -size * 0.48, result.max_height + size * 0.10),
            72.0,
            (0.0, size * 0.08, result.min_height + vertical * 0.60),
        ),
    }


def render_diagnostics(
    result: ImportResult,
    output_dir: str | Path,
    *,
    resolution: int = 896,
) -> dict[str, str]:
    _require_blender()
    cameras = setup_diagnostics(result)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except (TypeError, ValueError):
            continue
    scene.render.resolution_x = int(resolution)
    scene.render.resolution_y = int(resolution)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except (TypeError, ValueError):
        pass

    paths: dict[str, str] = {}
    for label, cam in cameras.items():
        path = output_dir / f"{result.object.name.lower()}_{label}.png"
        scene.camera = cam
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        paths[label] = str(path)
    return paths


def import_directory(
    directory: str | Path,
    *,
    target_resolution: int = 257,
    spacing: float = 1.25,
) -> list[ImportResult]:
    """Import a generated benchmark suite side-by-side for quick comparison."""
    _require_blender()
    files = sorted(Path(directory).glob("*.npz"))
    results: list[ImportResult] = []
    cursor_x = 0.0
    previous_half = 0.0
    for index, path in enumerate(files):
        result = import_terrain_package(
            path,
            name=f"InfinigenBenchmark_{index:02d}_{path.stem}",
            target_resolution=target_resolution,
        )
        half = result.tile_size_m * 0.5
        if index == 0:
            cursor_x = 0.0
        else:
            cursor_x += previous_half + half + max(previous_half, half) * (spacing - 1.0)
        result.object.location.x = cursor_x
        previous_half = half
        results.append(result)
    return results
