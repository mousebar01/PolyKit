from __future__ import annotations

import math
from pathlib import Path

try:
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except Exception:  # pragma: no cover
    bpy = None
    Vector = None

from .assets import build_all_assets
from .config import GrasslandConfig
from .geometry_nodes import attach_grassland_nodes
from .terrain import TerrainBuild, build_terrain


def _ensure_terrain_material(obj):
    mat_name = "Grassland_TerrainMaterial"
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.88

    vertex_color = nodes.new("ShaderNodeVertexColor")
    vertex_color.layer_name = "TerrainColor"

    noise = nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "3D"
    noise.inputs["Scale"].default_value = 0.055
    noise.inputs["Detail"].default_value = 2.2
    noise.inputs["Roughness"].default_value = 0.55

    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.30
    ramp.color_ramp.elements[0].color = (0.78, 0.78, 0.78, 1.0)
    ramp.color_ramp.elements[1].position = 0.72
    ramp.color_ramp.elements[1].color = (1.10, 1.08, 0.94, 1.0)

    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 0.22

    bump_noise = nodes.new("ShaderNodeTexNoise")
    bump_noise.noise_dimensions = "3D"
    bump_noise.inputs["Scale"].default_value = 1.6
    bump_noise.inputs["Detail"].default_value = 2.0
    bump_noise.inputs["Roughness"].default_value = 0.62
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.22

    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(vertex_color.outputs["Color"], mix.inputs[1])
    links.new(ramp.outputs["Color"], mix.inputs[2])
    links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bump_noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    if not obj.data.materials:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat
    return mat


def _look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _camera(name: str, location, target, lens: float = 48.0):
    old = bpy.data.objects.get(name)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    data = bpy.data.cameras.new(f"{name}Data")
    data.lens = lens
    data.sensor_width = 36.0
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    _look_at(obj, target)
    return obj


def _setup_lighting(size: float):
    for name in ("Grassland_Sun", "Grassland_Fill"):
        old = bpy.data.objects.get(name)
        if old:
            bpy.data.objects.remove(old, do_unlink=True)

    sun_data = bpy.data.lights.new("Grassland_SunData", type="SUN")
    sun_data.energy = 2.4
    sun_data.angle = math.radians(12.0)
    sun = bpy.data.objects.new("Grassland_Sun", sun_data)
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(28.0), math.radians(-18.0), math.radians(-38.0))

    fill_data = bpy.data.lights.new("Grassland_FillData", type="AREA")
    fill_data.energy = 900.0
    fill_data.shape = "DISK"
    fill_data.size = size * 0.32
    fill = bpy.data.objects.new("Grassland_Fill", fill_data)
    bpy.context.collection.objects.link(fill)
    fill.location = (-size * 0.22, -size * 0.30, size * 0.42)
    _look_at(fill, (0.0, 0.0, 0.0))

    world = bpy.context.scene.world or bpy.data.worlds.new("GrasslandWorld")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.19, 0.31, 0.48, 1.0)
        bg.inputs["Strength"].default_value = 0.52


def setup_diagnostic_cameras(config: GrasslandConfig, terrain: TerrainBuild):
    size = config.size
    z = terrain.max_height
    cameras = {
        "hero": _camera(
            "Grassland_Camera_Hero",
            (size * 0.38, -size * 0.44, z + size * 0.12),
            (-size * 0.05, size * 0.04, z * 0.20),
            46.0,
        ),
        "low": _camera(
            "Grassland_Camera_Low",
            (-size * 0.10, -size * 0.36, z * 0.08 + 5.0),
            (size * 0.08, size * 0.20, z * 0.20 + 4.0),
            39.0,
        ),
        "detail": _camera(
            "Grassland_Camera_Detail",
            (-size * 0.04, -size * 0.26, z * 0.03 + 2.6),
            (size * 0.02, -size * 0.08, z * 0.06 + 1.7),
            52.0,
        ),
    }

    top = _camera(
        "Grassland_Camera_Top",
        (0.0, 0.0, z + size * 0.95),
        (0.0, 0.0, 0.0),
        50.0,
    )
    top.data.type = "ORTHO"
    top.data.ortho_scale = size * 1.08
    cameras["top"] = top
    return cameras


def build_grassland_demo(
    config: GrasslandConfig | None = None,
    *,
    quality: str = "preview",
):
    """Build the complete benchmark scene inside Blender.

    quality: ``preview`` or ``quality`` when no explicit config is supplied.
    """
    if bpy is None:
        raise RuntimeError("build_grassland_demo must run inside Blender")
    if config is None:
        config = GrasslandConfig.preview() if quality == "preview" else GrasslandConfig.quality()

    terrain = build_terrain(config)
    _ensure_terrain_material(terrain.object)
    assets = build_all_assets(grass_height=config.grass_height)
    attach_grassland_nodes(terrain.object, assets, config)
    _setup_lighting(config.size)
    cameras = setup_diagnostic_cameras(config, terrain)

    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = 180
    scene.frame_set(1)
    scene.camera = cameras["hero"]

    return {
        "config": config,
        "terrain": terrain,
        "assets": assets,
        "cameras": cameras,
    }


def render_diagnostics(result, output_dir: str | Path, *, resolution: int | None = None):
    if bpy is None:
        raise RuntimeError("render_diagnostics must run inside Blender")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.image_settings.file_format = "PNG"
    px = int(resolution or result["config"].render_resolution)
    scene.render.resolution_x = px
    scene.render.resolution_y = px
    scene.render.resolution_percentage = 100

    paths = {}
    for label, cam in result["cameras"].items():
        scene.camera = cam
        path = output_dir / f"grassland_{label}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        paths[label] = str(path)
    return paths


def render_wind_preview(result, output_dir: str | Path, *, frames=(1, 35, 70), resolution: int = 640):
    """Render a few frames so wind motion can be checked without a full animation."""
    if bpy is None:
        raise RuntimeError("render_wind_preview must run inside Blender")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.image_settings.file_format = "PNG"
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.camera = result["cameras"]["detail"]
    paths = []
    for frame in frames:
        scene.frame_set(int(frame))
        path = output_dir / f"wind_{int(frame):04d}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        paths.append(str(path))
    scene.frame_set(1)
    return paths
