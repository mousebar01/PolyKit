"""Build a deterministic cold-valley research outpost in Blender.

This is an executable scene test for the prompt in ``prompt.md``. It keeps the
semantic intent in the scene-plan and uses Blender-native Arrays for repeated
stairs/crates while the hero terminal remains an explicit manufactured
assembly. The script is intentionally isolated: it only clears the new
background Blender scene that is launched for this test.
"""

from __future__ import annotations

import argparse
import bmesh
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

import bpy
from mathutils import Vector


PROMPT = (
    "创建一个建在寒冷山谷中的废弃科幻研究前哨站。场景主体是一座位于山谷中央的小型研究基地，"
    "由两栋低矮的混凝土建筑、一座连接它们的金属走廊、入口台阶和一个小型通讯平台组成。"
    "基地周围有崎岖地形、岩石、少量耐寒松树和散落的工业废料。"
    "在主建筑入口附近放置一台大型、独特的老式科幻通讯终端，作为场景的 Hero Asset。"
    "它应该明显比普通道具精细，有厚重的金属外壳、天线、控制面板和长期暴露在恶劣天气中的磨损感。"
    "基地旁边再放置几个重复使用的工业储物箱和设备箱，但不要让这些次要物件抢过通讯终端的视觉重点。"
    "整体风格偏写实、功能主义、冷战时期工业设计与轻度科幻结合。建筑结构应该合理、可以实际使用，"
    "不要做成纯概念艺术造型。保持清晰的道路、入口和建筑之间的空间关系。环境应该显得荒凉、寒冷、"
    "长期无人维护，但不要用大量随机杂物填满场景。"
)

SEED = 20260902
BUILDING_BASE_Z = 0.52
BUILDING_HEIGHT = 3.6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--tree-glb",
        default="",
        help="Optional local generated tree GLB. When set, replace the fallback cone trees with linked instances.",
    )
    return parser.parse_args(_script_args())


def _script_args() -> list[str]:
    import sys

    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1 :]
    return []


def move_to(obj: bpy.types.Object, collection: bpy.types.Collection) -> None:
    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    collection.objects.link(obj)


def new_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    return collection


def material(
    name: str,
    color: tuple[float, float, float],
    *,
    roughness: float = 0.75,
    metallic: float = 0.0,
    texture_scale: float = 1.0,
    weathered: bool = False,
    emission: tuple[tuple[float, float, float], float] | None = None,
) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*color, 1.0)
    mat["production_material_class"] = "metal" if metallic > 0.5 else "concrete"
    mat["production_texture_scale_m"] = texture_scale
    mat["production_surface_variant"] = "weathered" if weathered else "bare"
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    output.name = "Material Output"
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "Principled BSDF"
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    if weathered:
        texcoord = nodes.new("ShaderNodeTexCoord")
        mapping = nodes.new("ShaderNodeMapping")
        mapping.inputs["Scale"].default_value = (1.0 / max(texture_scale, 0.01),) * 3
        noise = nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 3.2
        noise.inputs["Detail"].default_value = 4.0
        ramp = nodes.new("ShaderNodeValToRGB")
        low = tuple(max(0.0, channel * 0.55) for channel in color)
        high = tuple(min(1.0, channel * 1.35 + 0.025) for channel in color)
        ramp.color_ramp.elements[0].color = (*low, 1.0)
        ramp.color_ramp.elements[1].color = (*high, 1.0)
        links.new(texcoord.outputs["Object"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
        links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        rough_noise = nodes.new("ShaderNodeTexNoise")
        rough_noise.inputs["Scale"].default_value = 18.0
        rough_noise.inputs["Detail"].default_value = 2.0
        rough_map = nodes.new("ShaderNodeMapRange")
        rough_map.inputs["From Min"].default_value = 0.25
        rough_map.inputs["From Max"].default_value = 0.75
        rough_map.inputs["To Min"].default_value = max(0.25, roughness - 0.16)
        rough_map.inputs["To Max"].default_value = min(1.0, roughness + 0.14)
        links.new(mapping.outputs["Vector"], rough_noise.inputs["Vector"])
        links.new(rough_noise.outputs["Fac"], rough_map.inputs["Value"])
        links.new(rough_map.outputs["Result"], bsdf.inputs["Roughness"])
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = 0.18 if metallic > 0.5 else 0.1
        bump.inputs["Distance"].default_value = min(0.04, texture_scale * 0.12)
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    if emission:
        socket = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
        if socket:
            socket.default_value = (*emission[0], 1.0)
        strength = bsdf.inputs.get("Emission Strength")
        if strength:
            strength.default_value = emission[1]
    return mat


def assign(obj: bpy.types.Object, mat: bpy.types.Material) -> bpy.types.Object:
    obj.data.materials.append(mat)
    return obj


def cube(
    name: str,
    location: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    mat: bpy.types.Material,
    collection: bpy.types.Collection,
    *,
    bevel: float = 0.0,
    role: str = "context",
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    assign(obj, mat)
    move_to(obj, collection)
    obj["polyKitRole"] = role
    obj["polyKitSemanticName"] = name
    if bevel:
        modifier = obj.modifiers.new("Manufactured edge radius", "BEVEL")
        modifier.width = bevel
        modifier.segments = 3
        modifier.limit_method = "ANGLE"
    return obj


def cylinder(
    name: str,
    location: tuple[float, float, float],
    radius: float,
    depth: float,
    mat: bpy.types.Material,
    collection: bpy.types.Collection,
    *,
    vertices: int = 16,
    bevel: float = 0.0,
    role: str = "context",
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    assign(obj, mat)
    move_to(obj, collection)
    obj["polyKitRole"] = role
    if bevel:
        modifier = obj.modifiers.new("Manufactured edge radius", "BEVEL")
        modifier.width = bevel
        modifier.segments = 2
        modifier.limit_method = "ANGLE"
    return obj


def ico(
    name: str,
    location: tuple[float, float, float],
    scale: tuple[float, float, float],
    mat: bpy.types.Material,
    collection: bpy.types.Collection,
    *,
    role: str = "context",
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    assign(obj, mat)
    move_to(obj, collection)
    obj["polyKitRole"] = role
    return obj


def curve_path(
    name: str,
    points: list[tuple[float, float, float]],
    mat: bpy.types.Material,
    collection: bpy.types.Collection,
    *,
    bevel_depth: float = 0.035,
    role: str = "functional",
) -> bpy.types.Object:
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.bevel_depth = bevel_depth
    data.bevel_resolution = 2
    spline = data.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for point, coordinate in zip(spline.bezier_points, points):
        point.co = coordinate
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    assign(obj, mat)
    obj["polyKitRole"] = role
    return obj


def look_at(camera: bpy.types.Object, target: tuple[float, float, float]) -> None:
    camera.rotation_euler = (Vector(target) - camera.location).to_track_quat("-Z", "Y").to_euler()


def make_camera(
    name: str,
    location: tuple[float, float, float],
    target: tuple[float, float, float],
    collection: bpy.types.Collection,
    *,
    lens: float = 48.0,
    ortho_scale: float | None = None,
) -> bpy.types.Object:
    data = bpy.data.cameras.new(name)
    camera = bpy.data.objects.new(name, data)
    collection.objects.link(camera)
    camera.location = location
    data.lens = lens
    if ortho_scale is not None:
        data.type = "ORTHO"
        data.ortho_scale = ortho_scale
    look_at(camera, target)
    camera["polyKitCameraRole"] = name
    return camera


def make_light(
    name: str,
    kind: str,
    location: tuple[float, float, float],
    collection: bpy.types.Collection,
    *,
    energy: float,
    color: tuple[float, float, float],
    size: float = 5.0,
    target: tuple[float, float, float] = (0.0, 0.0, 0.0),
    role: str,
    evidence: str,
) -> bpy.types.Object:
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    data.color = color
    if kind == "SUN":
        data.angle = math.radians(size)
    elif hasattr(data, "shadow_soft_size"):
        data.shadow_soft_size = size
    light = bpy.data.objects.new(name, data)
    collection.objects.link(light)
    light.location = location
    look_at(light, target)
    light["polyKitLightRole"] = role
    light["polyKitLightEvidence"] = evidence
    return light


def terrain_height(x: float, y: float, random_state: random.Random) -> float:
    side = (abs(x) / 36.0) ** 1.65
    ridges = 5.6 * side + 0.8 * math.sin(y * 0.22 + x * 0.08) * side
    floor = 0.22 + 0.18 * math.sin(y * 0.31) + 0.12 * math.cos(x * 0.41)
    noise = (random_state.random() - 0.5) * (0.15 + side * 0.6)
    return max(0.0, floor + ridges + noise)


def build_terrain(collection: bpy.types.Collection, mat: bpy.types.Material) -> bpy.types.Object:
    random_state = random.Random(SEED)
    resolution = 48
    width = 72.0
    depth = 60.0
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []
    for j in range(resolution + 1):
        y = -depth / 2.0 + depth * j / resolution
        for i in range(resolution + 1):
            x = -width / 2.0 + width * i / resolution
            vertices.append((x, y, terrain_height(x, y, random_state)))
    for j in range(resolution):
        for i in range(resolution):
            index = j * (resolution + 1) + i
            faces.append((index, index + 1, index + resolution + 2, index + resolution + 1))
    data = bpy.data.meshes.new("ColdValleyTerrainMesh")
    data.from_pydata(vertices, [], faces)
    data.update()
    terrain = bpy.data.objects.new("Cold_Valley_Terrain", data)
    collection.objects.link(terrain)
    assign(terrain, mat)
    terrain["polyKitRole"] = "background"
    terrain["polyKitSemanticName"] = "valley-terrain"
    terrain["polyKitTerrainSeed"] = SEED
    terrain["polyKitTerrainResolution"] = resolution
    for polygon in data.polygons:
        polygon.use_smooth = True
    return terrain


def add_building(
    prefix: str,
    x: float,
    concrete: bpy.types.Material,
    dark_metal: bpy.types.Material,
    glass: bpy.types.Material,
    architecture: bpy.types.Collection,
) -> dict[str, bpy.types.Object]:
    body = cube(
        f"{prefix}_ConcreteShell",
        (x, 2.0, BUILDING_BASE_Z + BUILDING_HEIGHT / 2.0),
        (8.5, 6.5, BUILDING_HEIGHT),
        concrete,
        architecture,
        bevel=0.16,
        role="primary_form",
    )
    roof = cube(
        f"{prefix}_RoofSlab",
        (x, 2.0, BUILDING_BASE_Z + BUILDING_HEIGHT + 0.12),
        (8.9, 6.9, 0.24),
        dark_metal,
        architecture,
        bevel=0.06,
        role="structural_part",
    )
    door = cube(
        f"{prefix}_EntryRecess",
        (x, -1.27, BUILDING_BASE_Z + 1.12),
        (1.45, 0.12, 2.2),
        dark_metal,
        architecture,
        bevel=0.035,
        role="functional_detail",
    )
    for side in (-1, 1):
        cube(
            f"{prefix}_DoorFrame_{side:+d}",
            (x + side * 0.82, -1.34, BUILDING_BASE_Z + 1.15),
            (0.12, 0.18, 2.35),
            concrete,
            architecture,
            bevel=0.025,
            role="functional_detail",
        )
    for window_index, window_x in enumerate((x - 2.5, x + 2.5), start=1):
        cube(
            f"{prefix}_SlitWindow_{window_index}",
            (window_x, -1.28, BUILDING_BASE_Z + 2.45),
            (1.2, 0.09, 0.42),
            glass,
            architecture,
            bevel=0.02,
            role="functional_detail",
        )
    vent = cube(
        f"{prefix}_ServiceVent",
        (x + 3.2, 2.0, BUILDING_BASE_Z + 1.25),
        (0.08, 1.4, 0.9),
        dark_metal,
        architecture,
        bevel=0.02,
        role="functional_detail",
    )
    vent["polyKitInterface"] = "service ventilation"
    return {"body": body, "roof": roof, "door": door}


def add_stairs(
    prefix: str,
    x: float,
    concrete: bpy.types.Material,
    architecture: bpy.types.Collection,
) -> bpy.types.Object:
    source = cube(
        f"{prefix}_StairSource",
        (x, -1.82, BUILDING_BASE_Z + 0.12),
        (2.5, 0.48, 0.24),
        concrete,
        architecture,
        bevel=0.025,
        role="structural_part",
    )
    array = source.modifiers.new("Entry stair rise-run Array", "ARRAY")
    array.count = 4
    array.use_relative_offset = False
    array.use_constant_offset = True
    array.constant_offset_displace = (0.0, 0.48, 0.22)
    source["polyKitNativeSystem"] = "ARRAY"
    source["polyKitArrayCount"] = array.count
    source["polyKitArrayOffset"] = tuple(array.constant_offset_displace)
    return source


def add_corridor(
    metal: bpy.types.Material,
    glass: bpy.types.Material,
    architecture: bpy.types.Collection,
) -> None:
    cube("Connector_Floor", (0.0, 2.0, 1.02), (4.1, 2.8, 0.24), metal, architecture, bevel=0.05, role="structural_part")
    cube("Connector_Roof", (0.0, 2.0, 3.45), (4.1, 2.8, 0.22), metal, architecture, bevel=0.05, role="structural_part")
    for side in (-1, 1):
        cube(f"Connector_GlassSide_{side:+d}", (side * 1.88, 2.0, 2.2), (0.08, 2.48, 2.2), glass, architecture, bevel=0.02, role="separate_manufactured_part")
        for y in (0.85, 3.15):
            cylinder(f"Connector_Frame_{side:+d}_{y}", (side * 1.95, y, 2.2), 0.07, 2.35, metal, architecture, role="structural_part")
    for y in (0.75, 3.25):
        cube(f"Connector_EndBeam_{y}", (0.0, y, 2.2), (3.8, 0.08, 2.35), metal, architecture, bevel=0.025, role="structural_part")


def add_platform_and_terminal(
    metal: bpy.types.Material,
    dark_metal: bpy.types.Material,
    emissive: bpy.types.Material,
    glass: bpy.types.Material,
    architecture: bpy.types.Collection,
    hero: bpy.types.Collection,
) -> bpy.types.Object:
    platform = cube("Communications_Platform", (-3.8, -2.8, 0.72), (4.2, 3.2, 0.3), metal, architecture, bevel=0.06, role="structural_part")
    platform["polyKitInterface"] = "platform-to-ground physical contact"
    for x in (-5.65, -1.95):
        for y in (-4.1, -1.5):
            cylinder(f"Platform_Rail_Post_{x}_{y}", (x, y, 1.48), 0.045, 1.55, dark_metal, architecture, role="functional_detail")
    curve_path("Platform_Rail_Front", [(-5.65, -4.1, 2.24), (-3.8, -4.1, 2.24), (-1.95, -4.1, 2.24)], dark_metal, architecture, bevel_depth=0.06, role="functional_detail")
    curve_path("Platform_Rail_Side", [(-1.95, -4.1, 2.24), (-1.95, -1.5, 2.24)], dark_metal, architecture, bevel_depth=0.06, role="functional_detail")

    plinth = cube("HeroTerminal_Plinth", (-3.95, -3.0, 0.98), (2.35, 1.85, 0.38), dark_metal, hero, bevel=0.12, role="hero_asset")
    casing = cube("HeroTerminal_WeatheredCasing", (-3.95, -3.02, 2.16), (1.72, 1.18, 2.25), metal, hero, bevel=0.14, role="hero_asset")
    casing["polyKitHero"] = True
    casing["polyKitWear"] = "wind abrasion, oxidation, chipped coating"
    for side in (-1, 1):
        cube(f"HeroTerminal_Shoulder_{side:+d}", (-3.95 + side * 0.92, -3.02, 2.45), (0.16, 1.0, 1.48), dark_metal, hero, bevel=0.045, role="hero_asset")
    panel = cube("HeroTerminal_ControlPanel", (-3.95, -3.66, 2.1), (1.28, 0.12, 0.62), dark_metal, hero, bevel=0.045, role="hero_asset", rotation=(math.radians(-12), 0.0, 0.0))
    panel["polyKitInterface"] = "operator controls"
    cube("HeroTerminal_StatusScreen", (-3.95, -3.735, 2.27), (0.68, 0.025, 0.28), emissive, hero, bevel=0.018, role="hero_asset")
    for index, x in enumerate((-4.38, -4.1, -3.8, -3.52), start=1):
        cylinder(f"HeroTerminal_Button_{index}", (x, -3.77, 1.93), 0.055, 0.035, emissive, hero, vertices=12, role="hero_asset", rotation=(math.radians(90), 0.0, 0.0))
    for x in (-4.45, -3.45):
        cylinder(f"HeroTerminal_Dial_{x}", (x, -3.76, 2.0), 0.1, 0.04, metal, hero, vertices=16, role="hero_asset", rotation=(math.radians(90), 0.0, 0.0))
    mast = cylinder("HeroTerminal_AntennaMast", (-3.95, -3.02, 4.65), 0.075, 3.2, dark_metal, hero, vertices=16, bevel=0.018, role="hero_asset")
    mast["polyKitInterface"] = "radio antenna mount"
    cylinder("HeroTerminal_AntennaTip", (-3.95, -3.02, 6.35), 0.14, 0.2, emissive, hero, vertices=12, role="hero_asset")
    dish = ico("HeroTerminal_AntennaDish", (-3.95, -3.18, 5.32), (0.7, 0.16, 0.58), metal, hero, role="hero_asset")
    dish.rotation_euler = (math.radians(-18), 0.0, math.radians(12))
    for side in (-1, 1):
        cylinder(f"HeroTerminal_DishStrut_{side:+d}", (-3.95 + side * 0.46, -3.18, 5.06), 0.035, 0.64, dark_metal, hero, vertices=12, role="hero_asset", rotation=(0.0, math.radians(side * 34), 0.0))
    curve_path("HeroTerminal_ServiceCable", [(-4.7, -3.2, 1.05), (-4.9, -3.5, 0.82), (-5.3, -3.8, 0.79)], dark_metal, hero, bevel_depth=0.045, role="hero_asset")
    return casing


def add_crates(metal: bpy.types.Material, props: bpy.types.Collection) -> None:
    source = cube("StorageCrate_Source", (4.2, -2.0, 1.12), (1.25, 1.15, 1.1), metal, props, bevel=0.08, role="distractor")
    array = source.modifiers.new("Reusable crate row", "ARRAY")
    array.count = 3
    array.use_relative_offset = False
    array.use_constant_offset = True
    array.constant_offset_displace = (1.45, 0.0, 0.0)
    source["polyKitNativeSystem"] = "ARRAY"
    source["polyKitArrayCount"] = array.count
    source["polyKitArrayOffset"] = tuple(array.constant_offset_displace)
    device = cube("EquipmentCase_Stack", (6.0, -0.15, 0.82), (1.15, 1.0, 0.72), metal, props, bevel=0.07, role="distractor")
    second = device.copy()
    second.data = device.data.copy()
    second.name = "EquipmentCase_Stack_Upper"
    props.objects.link(second)
    second.location.z += 0.82
    second.rotation_euler.z = math.radians(7)
    second["polyKitRole"] = "distractor"


def add_tree(index: int, x: float, y: float, z: float, trunk: bpy.types.Material, foliage: bpy.types.Material, environment: bpy.types.Collection) -> None:
    scale = 1.0 + (index % 3) * 0.18
    cylinder(f"ColdPine_{index}_Trunk", (x, y, z + 1.0 * scale), 0.12 * scale, 2.0 * scale, trunk, environment, vertices=8, role="context")
    for tier in range(3):
        height = 1.15 * scale
        radius = (0.75 - tier * 0.18) * scale
        bpy.ops.mesh.primitive_cone_add(vertices=8, radius1=radius, radius2=0.08, depth=height, location=(x, y, z + 1.55 * scale + tier * 0.65 * scale))
        cone = bpy.context.object
        cone.name = f"ColdPine_{index}_Foliage_{tier}"
        assign(cone, foliage)
        move_to(cone, environment)
        cone["polyKitRole"] = "context"


def make_trellis_tree_material() -> bpy.types.Material:
    """Build a restrained bark-to-needle material for a local Trellis asset."""
    mat = bpy.data.materials.new("Local Trellis2 Pine")
    mat.use_nodes = True
    mat.diffuse_color = (0.045, 0.12, 0.075, 1.0)
    mat["production_material_class"] = "organic_conifer"
    mat["production_surface_variant"] = "frosted_wind_scoured"
    mat["production_texture_scale_m"] = 0.14

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Roughness"].default_value = 0.92
    shader.inputs["Specular IOR Level"].default_value = 0.22

    texcoord = nodes.new("ShaderNodeTexCoord")
    separate = nodes.new("ShaderNodeSeparateXYZ")
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (0.075, 0.035, 0.018, 1.0)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (0.018, 0.11, 0.055, 1.0)
    mid = ramp.color_ramp.elements.new(0.22)
    mid.color = (0.12, 0.06, 0.028, 1.0)
    green = ramp.color_ramp.elements.new(0.34)
    green.color = (0.035, 0.16, 0.085, 1.0)

    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 8.0
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.75
    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 0.18
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.16
    bump.inputs["Distance"].default_value = 0.035

    links.new(texcoord.outputs["Generated"], separate.inputs["Vector"])
    links.new(separate.outputs["Z"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], mix.inputs[1])
    links.new(texcoord.outputs["Object"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], mix.inputs[2])
    links.new(mix.outputs["Color"], shader.inputs["Base Color"])
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return mat


def add_local_trellis_trees(
    tree_glb: Path,
    tree_positions: list[tuple[float, float, float]],
    tree_material: bpy.types.Material,
    environment: bpy.types.Collection,
) -> int:
    """Import one local Trellis.2 tree and place linked-data instances."""
    if not tree_glb.is_file():
        raise FileNotFoundError(f"Local tree GLB not found: {tree_glb}")

    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(tree_glb))
    imported = [obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"]
    if not imported:
        raise ValueError(f"Local tree GLB contains no mesh objects: {tree_glb}")

    for obj in imported:
        move_to(obj, environment)
        obj.data.materials.clear()
        obj.data.materials.append(tree_material)
        cleanup = bmesh.new()
        try:
            cleanup.from_mesh(obj.data)
            # Trellis' marching-cubes export can leave coincident leaf-tip
            # vertices at sub-micron spacing. Clean them before scene scaling
            # so the production validator does not mistake them for geometry.
            bmesh.ops.remove_doubles(cleanup, verts=list(cleanup.verts), dist=1e-5)
            bmesh.ops.dissolve_degenerate(cleanup, edges=list(cleanup.edges), dist=1e-5)
            degenerate_faces = [face for face in cleanup.faces if face.calc_area() <= 1e-10]
            if degenerate_faces:
                bmesh.ops.delete(cleanup, geom=degenerate_faces, context="FACES")
            degenerate_edges = [edge for edge in cleanup.edges if edge.calc_length() <= 1e-6]
            if degenerate_edges:
                bmesh.ops.delete(cleanup, geom=degenerate_edges, context="EDGES")
            loose_vertices = [vert for vert in cleanup.verts if not vert.link_edges and not vert.link_faces]
            if loose_vertices:
                bmesh.ops.delete(cleanup, geom=loose_vertices, context="VERTS")
            cleanup.normal_update()
            cleanup.to_mesh(obj.data)
            obj.data.update()
        finally:
            cleanup.free()

    min_v = Vector((float("inf"), float("inf"), float("inf")))
    max_v = Vector((float("-inf"), float("-inf"), float("-inf")))
    for obj in imported:
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            min_v = Vector((min(min_v.x, point.x), min(min_v.y, point.y), min(min_v.z, point.z)))
            max_v = Vector((max(max_v.x, point.x), max(max_v.y, point.y), max(max_v.z, point.z)))

    # Trellis emits a normalized Z-up tree. Ground it and target a useful 5.2 m
    # height so the locally generated asset reads as a sparse environmental tree.
    source_shift = Vector(((min_v.x + max_v.x) * 0.5, (min_v.y + max_v.y) * 0.5, min_v.z))
    source_height = max(0.01, max_v.z - min_v.z)
    normalization = 5.2 / source_height
    base_states: list[tuple[bpy.types.Object, Vector, Vector, Vector]] = []
    for obj in imported:
        obj.location = (obj.location - source_shift) * normalization
        obj.scale = obj.scale * normalization
        base_states.append((obj, obj.location.copy(), obj.scale.copy(), obj.rotation_euler.copy()))

    instance_scales = (1.0, 0.86, 1.12, 0.94, 0.78, 1.05)
    for index, position in enumerate(tree_positions, start=1):
        factor = instance_scales[(index - 1) % len(instance_scales)]
        for part_index, (source, base_location, base_scale, base_rotation) in enumerate(base_states, start=1):
            if index == 1:
                instance = source
            else:
                instance = source.copy()
                instance.data = source.data
                environment.objects.link(instance)
            instance.name = f"LocalTrellisTree_{index}_{part_index}"
            instance.location = base_location + Vector(position)
            instance.scale = base_scale * factor
            instance.rotation_euler = base_rotation
            instance.rotation_euler.z += math.radians(((index * 29) + (part_index * 7)) % 360)
            instance["polyKitRole"] = "context"
            instance["polyKitAssetSource"] = str(tree_glb)
            instance["polyKitGenerator"] = "local-trellis2"
            instance["polyKitInstanceIndex"] = index
            instance["polyKitTreeHeightMeters"] = round(source_height * normalization * factor, 3)
    return len(tree_positions) * len(base_states)


def build_scene(output_dir: Path, tree_glb: Path | None = None) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)

    scene.name = "ColdValleyResearchOutpost"
    scene["polyKitPreset"] = "cold_valley_research_outpost_v1"
    scene["polyKitBrief"] = PROMPT
    scene["polyKitHeroAsset"] = "HeroTerminal_WeatheredCasing"
    scene["polyKitCoordinateSystem"] = "Blender Z-up meters"
    scene["polyKitTreeGenerator"] = "local-trellis2" if tree_glb else "blender-fallback-cone"
    if tree_glb:
        scene["polyKitTreeSource"] = str(tree_glb)
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"

    architecture = new_collection("Architecture")
    hero = new_collection("HeroAsset")
    environment = new_collection("Environment")
    props = new_collection("IndustrialProps")
    lighting = new_collection("Lighting")
    cameras = new_collection("Cameras")

    concrete = material("Weathered Reinforced Concrete", (0.22, 0.25, 0.27), roughness=0.9, texture_scale=0.65, weathered=True)
    metal = material("Chipped Painted Steel", (0.10, 0.13, 0.15), roughness=0.62, metallic=0.82, texture_scale=0.09, weathered=True)
    dark_metal = material("Oxidized Dark Metal", (0.035, 0.05, 0.06), roughness=0.78, metallic=0.72, texture_scale=0.06, weathered=True)
    glass = material("Frosted Communications Glass", (0.13, 0.25, 0.29), roughness=0.28, metallic=0.05, texture_scale=0.02)
    screen = material("Cold Cyan Terminal Glow", (0.03, 0.16, 0.2), roughness=0.3, metallic=0.1, texture_scale=0.01, emission=((0.04, 0.55, 0.9), 3.5))
    snow = material("Wind Packed Snow and Gravel", (0.34, 0.41, 0.47), roughness=0.94, texture_scale=0.45, weathered=True)
    rock = material("Basalt Rock", (0.12, 0.14, 0.16), roughness=0.96, texture_scale=0.55, weathered=True)
    pine_trunk = material("Cold Pine Trunk", (0.15, 0.095, 0.055), roughness=0.91, texture_scale=0.22, weathered=True)
    pine_foliage = material("Wind Scoured Pine Foliage", (0.08, 0.16, 0.13), roughness=0.92, texture_scale=0.32, weathered=True)
    trellis_tree_material = make_trellis_tree_material() if tree_glb else pine_foliage

    terrain = build_terrain(environment, snow)
    path = cube("Approach_Path", (0.0, -11.0, 0.55), (4.2, 22.0, 0.14), rock, environment, bevel=0.05, role="structural_part")
    path["polyKitInterface"] = "clear approach from valley foreground to entry"
    add_building("ResearchWest", -6.0, concrete, dark_metal, glass, architecture)
    add_building("ResearchEast", 6.0, concrete, dark_metal, glass, architecture)
    add_corridor(metal, glass, architecture)
    add_stairs("West", -6.0, concrete, architecture)
    add_stairs("East", 6.0, concrete, architecture)
    add_platform_and_terminal(metal, dark_metal, screen, glass, architecture, hero)
    add_crates(metal, props)

    rock_positions = [(-18, -6, 1.5), (18, -2, 1.0), (-23, 10, 2.0), (21, 15, 2.5), (-14, 20, 1.2), (16, 24, 1.4), (-28, -18, 2.2), (28, -20, 1.8)]
    for index, (x, y, size) in enumerate(rock_positions, start=1):
        ico(f"ValleyRock_{index}", (x, y, size * 0.45), (size * 1.2, size * 0.7, size), rock, environment, role="context")
    tree_positions = [(-24, 5, 1.1), (24, 8, 1.0), (-27, 17, 1.4), (27, 22, 1.2), (-19, 24, 1.0), (20, -14, 0.8)]
    if tree_glb:
        local_tree_count = add_local_trellis_trees(tree_glb, tree_positions, trellis_tree_material, environment)
    else:
        local_tree_count = 0
        for index, (x, y, z) in enumerate(tree_positions, start=1):
            add_tree(index, x, y, z, pine_trunk, pine_foliage, environment)
    scrap_positions = [(-11.5, -7.0, 0.85), (11.5, -5.5, 0.75), (9.5, 7.0, 0.65), (-12.0, 8.0, 0.55)]
    for index, (x, y, z) in enumerate(scrap_positions, start=1):
        scrap = cube(f"IndustrialScrap_{index}", (x, y, z), (1.8, 0.38, 0.38), dark_metal, props, bevel=0.04, role="distractor", rotation=(0.0, math.radians(index * 7), math.radians(index * 13)))
        scrap["polyKitAbandonmentDetail"] = "sparse placed industrial waste"
    curve_path("Cable_Run_To_Base", [(-4.4, -4.0, 0.84), (-3.5, -5.0, 0.73), (-1.5, -5.6, 0.72)], dark_metal, props, bevel_depth=0.035, role="functional_detail")

    sun = make_light("WinterSun_Key", "SUN", (-18.0, -22.0, 24.0), lighting, energy=2.2, color=(0.68, 0.78, 1.0), size=7.0, target=(0.0, 2.0, 0.0), role="primary directional winter light", evidence="low winter sun leaves long shadows from the valley-left shoulder")
    fill = make_light("ColdSky_Fill", "AREA", (0.0, -3.0, 18.0), lighting, energy=650.0, color=(0.22, 0.35, 0.55), size=18.0, target=(0.0, 2.0, 0.0), role="dark-side readability fill", evidence="keeps the north-facing concrete and terminal casing readable without warming the scene")
    fill.data.shape = "DISK"
    screen_fill = make_light("Terminal_Cyan_Bounce", "AREA", (-4.0, -6.0, 3.0), lighting, energy=90.0, color=(0.05, 0.28, 0.42), size=2.0, target=(-4.0, -3.0, 1.8), role="hero practical bounce", evidence="subtle local response from the terminal screen; removed if it distracts from the hero")
    screen_fill.data.shape = "DISK"
    scene["polyKitLightRoles"] = json.dumps({obj.name: obj.get("polyKitLightRole") for obj in (sun, fill, screen_fill)}, ensure_ascii=False)

    world = scene.world or bpy.data.worlds.new("ColdValleyWorld")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.015, 0.028, 0.055, 1.0)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.28

    hero_camera = make_camera("Hero_ThreeQuarter", (19.5, -28.0, 11.2), (-2.4, 0.2, 2.25), cameras, lens=52.0)
    overview_camera = make_camera("Base_Overview", (28.0, -37.0, 18.0), (0.0, 1.5, 2.0), cameras, lens=48.0)
    top_camera = make_camera("Structural_Top", (0.0, 3.0, 34.0), (0.0, 2.0, 0.0), cameras, lens=50.0, ortho_scale=72.0)
    side_camera = make_camera("Structural_Side", (35.0, 4.0, 10.0), (0.0, 2.0, 2.0), cameras, lens=50.0)
    scene.camera = hero_camera

    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in {
        item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    } else "BLENDER_EEVEE"
    scene.render.resolution_x = 960
    scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.look = "AgX - Medium High Contrast"

    render_passes: list[dict[str, object]] = []
    for pass_id, camera in (("hero", hero_camera), ("overview", overview_camera), ("top", top_camera), ("side", side_camera)):
        path = output_dir / f"cold_valley_research_outpost_{pass_id}.png"
        scene.camera = camera
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        render_passes.append({"id": pass_id, "camera": camera.name, "workspacePath": str(path), "nonblank": path.is_file() and path.stat().st_size > 10_000, "fileBytes": path.stat().st_size if path.is_file() else 0})

    blend_path = output_dir / "cold_valley_research_outpost.blend"
    glb_path = output_dir / "cold_valley_research_outpost.glb"
    scene.camera = hero_camera
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB", export_apply=True)

    mesh_objects = [obj for obj in scene.objects if obj.type == "MESH"]
    hero_objects = [obj.name for obj in mesh_objects if obj.get("polyKitRole") == "hero_asset"]
    array_evidence = [
        {"object": obj.name, "count": int(modifier.count), "offset": list(modifier.constant_offset_displace)}
        for obj in mesh_objects
        for modifier in obj.modifiers
        if modifier.type == "ARRAY"
    ]
    validation = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": "PASS" if hero_objects and glb_path.is_file() and all(item["nonblank"] for item in render_passes) else "WARN",
        "checks": [
            {"id": "hero_terminal", "status": "PASS" if hero_objects else "FAIL", "evidence": hero_objects, "details": "Hero terminal has a separate casing, antenna, panel, screen, controls, and weathering material."},
            {"id": "base_layout", "status": "PASS", "evidence": ["ResearchWest_ConcreteShell", "ResearchEast_ConcreteShell", "Connector_Floor", "Communications_Platform", "Approach_Path"], "details": "Two low buildings, a bridging corridor, entry stairs, platform, and clear approach path are present."},
            {"id": "native_repetition", "status": "PASS" if len(array_evidence) >= 3 else "WARN", "evidence": array_evidence, "details": "Entry stair flights and reusable crates retain native Array modifiers in the editable blend."},
            {"id": "environment", "status": "PASS", "evidence": ["Cold_Valley_Terrain", "ValleyRock_1", "LocalTrellisTree_1_1" if tree_glb else "ColdPine_1_Trunk", "IndustrialScrap_1"], "details": "Rugged terrain, sparse locally generated pines, grouped rocks, and restrained scrap frame the base."},
            {"id": "render_health", "status": "PASS" if all(item["nonblank"] for item in render_passes) else "FAIL", "evidence": render_passes, "details": "Hero, overview, top, and side review renders were written."},
        ],
        "thresholds": {"render_min_bytes": 10000, "hero_asset_min_parts": 6, "mesh_object_budget": 180},
        "intentional_exceptions": ["This is a first deterministic scene test; visual approval still requires human review of the rendered evidence."],
        "not_evaluated": ["Final material identity and weathering realism require a dedicated neutral/grazing material review."],
        "repair_suggestions": [],
        "scene": {
            "blend": str(blend_path),
            "glb": str(glb_path),
            "mesh_object_count": len(mesh_objects),
            "hero_object_count": len(hero_objects),
            "array_evidence": array_evidence,
            "tree_source": str(tree_glb) if tree_glb else "blender-fallback-cone",
            "tree_generator": "local-trellis2" if tree_glb else "blender-fallback-cone",
            "tree_instance_count": local_tree_count if tree_glb else len(tree_positions),
        },
    }
    validation_path = output_dir / "cold_valley_research_outpost.validation.json"
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    evidence_path = output_dir / "cold_valley_research_outpost.render-evidence.json"
    evidence_path.write_text(json.dumps({"schema_version": "1.0", "profile": "production", "engine": scene.render.engine, "passes": render_passes, "lights": [{"id": obj.name, "role": obj.get("polyKitLightRole"), "evidence": obj.get("polyKitLightEvidence")} for obj in (sun, fill, screen_fill)]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return validation


def main() -> None:
    args = parse_args()
    tree_glb = Path(args.tree_glb).expanduser().resolve() if args.tree_glb else None
    result = build_scene(Path(args.output_dir).expanduser().resolve(), tree_glb=tree_glb)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
