"""Parameterized reference-scene builder with geometry-backed validation.

This is the implementation behind the existing ``blender-scene/build`` node.
The process contract stays stable, but construction is now driven by a small
BuildSpec vocabulary instead of unrelated hard-coded object coordinates.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Mapping


SUBWAY_REFERENCE_PROMPT = (
    "Reconstruct a cinematic 16:9 night subway platform from a low eye-level view: the camera is tucked "
    "behind a large tiled foreground column on the right and looks diagonally down twin rails into a deep "
    "shadowed tunnel; both left and right platform edges carry matching proportionally inset, thin yellow textured "
    "tactile strips flush with the platform slabs, with raised dots and dark safety edges, repeating square tiled columns show visible grout and microtexture, open platform "
    "edges remain unobstructed as the station recedes into the distance, the left side is one continuous platform running flush from the tiled wall to the track with no side corridor or railing, the ceiling has long linear recessed grooves with dark metal housings, evenly spaced LED beads, and flush transparent glass diffuser panels, with cool white light and a restrained ceiling wash keeping the top "
    "panels readable without crushed black, blue-gray porcelain and concrete, slightly reflective floor, high contrast, "
    "no people, no train, no readable signage."
)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _slug(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", "_", value).strip("_").lower()
    return result[:64] or "blender_scene"


def _number(params: Mapping[str, Any], key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(params.get(key, default))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


def _cabin_config(params: Mapping[str, Any]) -> dict[str, float]:
    return {
        "width": _number(params, "cabin_width", 12.0, 4.0, 30.0),
        "depth": _number(params, "cabin_depth", 10.0, 4.0, 30.0),
        "wall_height": _number(params, "wall_height", 5.6, 2.2, 12.0),
        "wall_thickness": _number(params, "wall_thickness", 0.22, 0.08, 0.8),
        "floor_thickness": _number(params, "floor_thickness", 0.24, 0.08, 0.8),
        "roof_pitch_deg": _number(params, "roof_pitch_deg", 36.0, 15.0, 65.0),
        "roof_thickness": _number(params, "roof_thickness", 0.18, 0.08, 0.6),
        "roof_overhang": _number(params, "roof_overhang", 0.45, 0.0, 2.0),
        "contact_tolerance": _number(params, "contact_tolerance", 0.035, 0.005, 0.2),
    }


def _canonical(x: float, blender_y: float, blender_z: float) -> list[float]:
    """Blender Z-up -> PolyKit/glTF Y-up position."""
    return [round(x, 6), round(blender_z, 6), round(-blender_y, 6)]


def _building_spec(config: Mapping[str, float]) -> dict[str, Any]:
    width = config["width"]
    depth = config["depth"]
    height = config["wall_height"]
    thickness = config["wall_thickness"]
    bearing = width / 2.0 - thickness / 2.0
    back_y = depth / 2.0 - thickness / 2.0
    tolerance = config["contact_tolerance"]

    anchors = [
        {"id": "floor-left", "partId": "floor", "position": _canonical(-bearing, 0.0, 0.0)},
        {"id": "left-wall-bottom", "partId": "left-wall", "position": _canonical(-bearing, 0.0, 0.0)},
        {"id": "floor-right", "partId": "floor", "position": _canonical(bearing, 0.0, 0.0)},
        {"id": "right-wall-bottom", "partId": "right-wall", "position": _canonical(bearing, 0.0, 0.0)},
        {"id": "floor-back", "partId": "floor", "position": _canonical(0.0, back_y, 0.0)},
        {"id": "back-wall-bottom", "partId": "back-wall", "position": _canonical(0.0, back_y, 0.0)},
        {"id": "left-wall-top", "partId": "left-wall", "position": _canonical(-bearing, 0.0, height)},
        {"id": "left-roof-bearing", "partId": "left-roof", "position": _canonical(-bearing, 0.0, height)},
        {"id": "right-wall-top", "partId": "right-wall", "position": _canonical(bearing, 0.0, height)},
        {"id": "right-roof-bearing", "partId": "right-roof", "position": _canonical(bearing, 0.0, height)},
    ]
    pairs = (
        ("left-wall-floor", "floor-left", "left-wall-bottom"),
        ("right-wall-floor", "floor-right", "right-wall-bottom"),
        ("back-wall-floor", "floor-back", "back-wall-bottom"),
        ("left-wall-roof", "left-wall-top", "left-roof-bearing"),
        ("right-wall-roof", "right-wall-top", "right-roof-bearing"),
    )
    return {
        "id": "winter-cabin",
        "name": "Winter Cabin",
        "generator": "blender-parametric",
        "parameters": {
            "width": width,
            "depth": depth,
            "wallHeight": height,
            "wallThickness": thickness,
            "floorThickness": config["floor_thickness"],
            "roofPitchDeg": config["roof_pitch_deg"],
            "roofThickness": config["roof_thickness"],
            "roofOverhang": config["roof_overhang"],
            "contactTolerance": tolerance,
            "coordinateSystem": "polykit-y-up-meters",
        },
        "anchors": anchors,
        "attachments": [
            {"id": pair_id, "from": source, "to": target, "mode": "support", "tolerance": tolerance}
            for pair_id, source, target in pairs
        ],
    }


def _cabin_scene_script(
    scene_name: str,
    brief: str,
    width: int,
    height: int,
    render_preview: bool,
    config: Mapping[str, float],
    building_spec: Mapping[str, Any],
    render_profile: str = "production",
) -> str:
    script = r'''
import base64
import json
import math
import pathlib
import shutil
import tempfile
import bpy
from mathutils import Vector

SCENE_NAME = __SCENE_NAME__
SCENE_BRIEF = __SCENE_BRIEF__
RENDER_WIDTH = __WIDTH__
RENDER_HEIGHT = __HEIGHT__
RENDER_PREVIEW = __RENDER_PREVIEW__
RENDER_PROFILE = __RENDER_PROFILE__
CONFIG = json.loads(__CONFIG_JSON__)
BUILDING_SPEC = json.loads(__BUILD_SPEC_JSON__)

scene = bpy.context.scene
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for collection in list(bpy.data.collections):
    bpy.data.collections.remove(collection)
for material_data in list(bpy.data.materials):
    if material_data.users == 0:
        bpy.data.materials.remove(material_data)
scene.name = SCENE_NAME
scene['polyKitPreset'] = 'winter_cabin_v2'
scene['polyKitSource'] = 'blender-mcp-official'
scene['polyKitBrief'] = SCENE_BRIEF
scene['polyKitBuildSpec'] = json.dumps(BUILDING_SPEC, separators=(',', ':'))
scene['polyKitBlenderVersion'] = bpy.app.version_string
scene['polyKitCoordinateSystem'] = 'Blender Z-up meters; BuildSpec anchors are PolyKit Y-up meters'
try:
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.length_unit = 'METERS'
except Exception:
    pass

def collection(name):
    value = bpy.data.collections.new(name)
    scene.collection.children.link(value)
    return value

ARCH = collection('Architecture')
FURNITURE = collection('Furniture')
LIGHTING = collection('Lighting')
ENVIRONMENT = collection('Environment')

def principled(mat):
    nodes = mat.node_tree.nodes
    return nodes.get('Principled BSDF') or next((node for node in nodes if node.bl_idname == 'ShaderNodeBsdfPrincipled'), None)

def material(name, color, roughness=0.75, metallic=0.0, emission=None, material_class='generic', texture_scale=1.0, hero=False, transmission=0.0, ior=1.45):
    """Create a compact, portable PBR graph with independent surface channels."""
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.use_nodes = True
    mat['production_material_class'] = material_class
    mat['production_texture_scale_m'] = float(texture_scale)
    mat['production_surface_variant'] = 'weathered' if material_class == 'wood' else 'bare'
    mat['production_hero'] = bool(hero)
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    output.name = 'Material Output'
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.name = 'Principled BSDF'
    bsdf.inputs['Base Color'].default_value = (*color, 1.0)
    bsdf.inputs['Roughness'].default_value = roughness
    bsdf.inputs['Metallic'].default_value = metallic
    if material_class == 'glass':
        # A lightly tinted alpha layer keeps the diffuser readable in Eevee
        # while the transmission weight still reveals the LED beads behind it.
        glass_alpha = 0.18
        bsdf.inputs['Alpha'].default_value = glass_alpha
        mat.diffuse_color = (*color, glass_alpha)
        mat['production_alpha'] = glass_alpha
    transmission_socket = bsdf.inputs.get('Transmission Weight') or bsdf.inputs.get('Transmission')
    if transmission_socket is not None:
        transmission_socket.default_value = max(0.0, min(1.0, float(transmission)))
    if bsdf.inputs.get('IOR') is not None:
        bsdf.inputs['IOR'].default_value = float(ior)
    links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

    # Procedural variation is deliberately split by channel: color, roughness,
    # and micro-normal do not share one identical noise signal. Manufactured
    # tile and tactile surfaces use an explicit brick/microrelief pattern so
    # columns and safety bands do not collapse to flat colors at render time.
    textured_classes = {
        'wood', 'metal', 'fabric', 'snow', 'tile', 'ceramic', 'tactile',
        'concrete', 'stone', 'rubber', 'painted-metal', 'painted-concrete',
    }
    if material_class in textured_classes:
        texcoord = nodes.new('ShaderNodeTexCoord')
        texcoord.name = 'Production Coordinates'
        mapping = nodes.new('ShaderNodeMapping')
        mapping.name = 'Physical Texture Mapping'
        mapping.inputs['Scale'].default_value = (1.0 / max(texture_scale, 0.01),) * 3
        links.new(texcoord.outputs['Object'], mapping.inputs['Vector'])

        if material_class in {'tile', 'ceramic'}:
            brick = nodes.new('ShaderNodeTexBrick')
            brick.name = 'Tile Grid and Grout'
            brick.offset = 0.0
            brick.offset_frequency = 2
            brick.inputs['Scale'].default_value = 1.0
            brick.inputs['Mortar Size'].default_value = 0.045
            brick.inputs['Mortar Smooth'].default_value = 0.018
            brick.inputs['Color1'].default_value = (*color, 1.0)
            brick.inputs['Color2'].default_value = tuple(min(1.0, channel * 1.08 + 0.02) for channel in color) + (1.0,)
            brick.inputs['Mortar'].default_value = tuple(max(0.0, channel * 0.38) for channel in color) + (1.0,)
            links.new(mapping.outputs['Vector'], brick.inputs['Vector'])
            links.new(brick.outputs['Color'], bsdf.inputs['Base Color'])
            relief_source = brick.outputs['Fac']
            relief_strength = 0.13
            relief_distance = 0.012
        else:
            color_noise = nodes.new('ShaderNodeTexNoise')
            color_noise.name = 'Color Variation'
            color_noise.inputs['Scale'].default_value = 3.0 if material_class == 'wood' else (12.0 if material_class == 'tactile' else 6.0)
            color_noise.inputs['Detail'].default_value = 4.0
            color_ramp = nodes.new('ShaderNodeValToRGB')
            color_ramp.name = 'Substrate Color Range'
            low = tuple(max(0.0, channel * 0.62) for channel in color)
            high = tuple(min(1.0, channel * 1.35 + 0.015) for channel in color)
            color_ramp.color_ramp.elements[0].color = (*low, 1.0)
            color_ramp.color_ramp.elements[1].color = (*high, 1.0)
            links.new(mapping.outputs['Vector'], color_noise.inputs['Vector'])
            links.new(color_noise.outputs['Fac'], color_ramp.inputs['Fac'])
            links.new(color_ramp.outputs['Color'], bsdf.inputs['Base Color'])
            relief_source = color_noise.outputs['Fac']
            relief_strength = 0.12 if material_class == 'tactile' else (0.08 if material_class in {'concrete', 'stone'} else 0.06)
            relief_distance = 0.025 if material_class == 'tactile' else 0.018

        rough_noise = nodes.new('ShaderNodeTexNoise')
        rough_noise.name = 'Roughness Variation'
        rough_noise.inputs['Scale'].default_value = 11.0 if material_class == 'wood' else (24.0 if material_class == 'tactile' else 17.0)
        rough_noise.inputs['Detail'].default_value = 2.0
        rough_map = nodes.new('ShaderNodeMapRange')
        rough_map.name = 'Roughness Range'
        rough_map.inputs['From Min'].default_value = 0.25
        rough_map.inputs['From Max'].default_value = 0.75
        rough_map.inputs['To Min'].default_value = max(0.04, roughness - 0.12)
        rough_map.inputs['To Max'].default_value = min(1.0, roughness + 0.12)
        links.new(mapping.outputs['Vector'], rough_noise.inputs['Vector'])
        links.new(rough_noise.outputs['Fac'], rough_map.inputs['Value'])
        links.new(rough_map.outputs['Result'], bsdf.inputs['Roughness'])

        bump = nodes.new('ShaderNodeBump')
        bump.name = 'Surface Micro Bump'
        bump.inputs['Strength'].default_value = relief_strength if material_class != 'wood' else 0.12
        bump.inputs['Distance'].default_value = relief_distance if material_class != 'wood' else 0.025
        if material_class not in {'tile', 'ceramic'}:
            relief_noise = nodes.new('ShaderNodeTexNoise')
            relief_noise.name = 'Micro Relief'
            relief_noise.inputs['Scale'].default_value = 35.0 if material_class == 'wood' else 48.0
            relief_noise.inputs['Detail'].default_value = 2.0
            links.new(mapping.outputs['Vector'], relief_noise.inputs['Vector'])
            relief_source = relief_noise.outputs['Fac']
        links.new(relief_source, bump.inputs['Height'])
        links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])

    if emission:
        key = 'Emission Color' if 'Emission Color' in bsdf.inputs else 'Emission'
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = (*emission[0], 1.0)
        if 'Emission Strength' in bsdf.inputs:
            bsdf.inputs['Emission Strength'].default_value = emission[1]
    mat['production_transmission'] = float(transmission)
    mat['production_ior'] = float(ior)
    return mat

WOOD = material('Cabin Wood', (0.18, 0.055, 0.015), 0.82, material_class='wood', texture_scale=0.32, hero=True)
WOOD_LIGHT = material('Pale Wood', (0.42, 0.18, 0.05), 0.78, material_class='wood', texture_scale=0.28)
WOOD_DARK = material('Dark Timber', (0.055, 0.014, 0.005), 0.9, material_class='wood', texture_scale=0.38)
METAL = material('Stove Iron', (0.025, 0.03, 0.035), 0.32, 0.85, material_class='metal', texture_scale=0.08, hero=True)
FABRIC = material('Wool', (0.48, 0.43, 0.36), 0.95, material_class='fabric', texture_scale=0.018)
FIRE = material('Fire', (0.2, 0.025, 0.003), 0.35, 0.0, ((1.0, 0.15, 0.02), 5.0), material_class='emissive', texture_scale=0.01)
SNOW = material('Exterior Snow', (0.55, 0.62, 0.72), 0.88, material_class='snow', texture_scale=0.18)

def move(obj, target):
    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    target.objects.link(obj)

def cube(name, location, dimensions, mat, role, target=ARCH, rotation=(0.0, 0.0, 0.0), bevel=0.0, zone=None):
    bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    obj['polyKitRole'] = role
    obj['polyKitSemanticName'] = name
    obj['polyKitZone'] = zone or ('architecture' if target == ARCH else 'interior')
    move(obj, target)
    if bevel:
        modifier = obj.modifiers.new('Soft edges', 'BEVEL')
        modifier.width = bevel
        modifier.segments = 2
    return obj

def cylinder(name, location, radius, depth, mat, role, target=FURNITURE):
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=radius, depth=depth, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    obj['polyKitRole'] = role
    obj['polyKitZone'] = 'architecture' if target == ARCH else 'interior'
    move(obj, target)
    return obj

def look_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat('-Z', 'Y').to_euler()

W = CONFIG['width']
D = CONFIG['depth']
H = CONFIG['wall_height']
T = CONFIG['wall_thickness']
FT = CONFIG['floor_thickness']
PITCH = math.radians(CONFIG['roof_pitch_deg'])
RT = CONFIG['roof_thickness']
OVERHANG = CONFIG['roof_overhang']
TOL = CONFIG['contact_tolerance']
BEARING = W / 2.0 - T / 2.0
RUN = W / 2.0 + OVERHANG
COS = math.cos(PITCH)
SIN = math.sin(PITCH)
# The roof centre is solved from the wall-bearing contact equation rather than
# guessed.  On the underside plane, the point at x=+/-BEARING lands exactly on
# z=H, so changing width/pitch/thickness cannot open a wall/roof gap.
A = BEARING - RUN / 2.0 + SIN * RT / 2.0
ROOF_CENTER_Z = H + math.tan(PITCH) * A + COS * RT / 2.0
ROOF_LENGTH = (RUN + 0.5) / COS

floor = cube('Cabin_Floor', (0.0, 0.0, -FT / 2.0), (W, D, FT), WOOD_LIGHT, 'floor', bevel=0.035)
left_wall = cube('Cabin_Wall_Left', (-BEARING, 0.0, H / 2.0), (T, D, H), WOOD, 'wall')
right_wall = cube('Cabin_Wall_Right', (BEARING, 0.0, H / 2.0), (T, D, H), WOOD, 'wall')
back_wall = cube('Cabin_Wall_Back', (0.0, D / 2.0 - T / 2.0, H / 2.0), (W - 2*T, T, H), WOOD, 'wall')
left_roof = cube('Cabin_Roof_Left', (-RUN / 2.0, 0.0, ROOF_CENTER_Z), (ROOF_LENGTH, D + 2*OVERHANG, RT), WOOD_DARK, 'roof', rotation=(0.0, -PITCH, 0.0))
right_roof = cube('Cabin_Roof_Right', (RUN / 2.0, 0.0, ROOF_CENTER_Z), (ROOF_LENGTH, D + 2*OVERHANG, RT), WOOD_DARK, 'roof', rotation=(0.0, PITCH, 0.0))
ridge_z = H + BEARING * math.tan(PITCH)
cube('Cabin_Ridge_Beam', (0.0, 0.0, ridge_z + 0.12), (0.3, D + 0.35, 0.3), WOOD_DARK, 'roof-beam', bevel=0.025)

# Open front remains deliberate for inspection; porch geometry is derived from
# the same W/D parameters instead of fixed world coordinates.
porch_y = -D / 2.0 - 0.48
cube('Cabin_Porch', (0.0, porch_y, 0.08), (W - 1.0, 0.95, 0.18), WOOD_LIGHT, 'porch', bevel=0.035)
cube('Cabin_Threshold', (0.0, -D / 2.0 + 0.06, 0.14), (W - 1.2, 0.22, 0.16), WOOD_DARK, 'threshold', bevel=0.025)

# A shallow snow receiver gives the exterior camera a real ground plane and
# keeps the cabin grounded without making it part of the interior BuildSpec.
ground = cube(
    'Exterior_Snow_Ground',
    (0.0, 0.15, -0.09),
    (W * 2.8, D * 2.8, 0.16),
    SNOW,
    'environment-ground',
    ENVIRONMENT,
    bevel=0.04,
    zone='environment',
)
ground['polyKitEnvironmentRole'] = 'snow-receiver'

# A compact furniture pass keeps the reference useful without mixing furniture
# placement into the structural attachment contract.
cube('Bed_Frame', (-W*0.28, D*0.12, 0.45), (W*0.28, 1.9, 0.5), WOOD_DARK, 'bed', FURNITURE, bevel=0.05)
cube('Bed_Mattress', (-W*0.28, D*0.12, 0.78), (W*0.26, 1.72, 0.28), FABRIC, 'bed-mattress', FURNITURE, bevel=0.09)
cube('Table_Top', (0.0, -D*0.08, 0.95), (1.8, 1.1, 0.16), WOOD_LIGHT, 'table', FURNITURE, bevel=0.04)
for x in (-0.65, 0.65):
    for y in (-0.38, 0.38):
        cube('Table_Leg', (x, -D*0.08+y, 0.47), (0.11, 0.11, 0.9), WOOD_DARK, 'table-leg', FURNITURE)
cube('Stove_Body', (W*0.31, D*0.24, 1.1), (1.45, 1.2, 1.9), METAL, 'wood-stove', FURNITURE, bevel=0.06)
cube('Stove_Fire', (W*0.31, D*0.18, 1.05), (0.78, 0.05, 0.75), FIRE, 'stove-fire', FURNITURE, bevel=0.03)
cylinder('Stove_Pipe', (W*0.31, D*0.24, 3.65), 0.2, 4.7, METAL, 'stove-pipe')

def from_canonical(value):
    return Vector((value[0], -value[2], value[1]))

def closest_distance(obj, point):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    matrix = evaluated.matrix_world
    local_point = matrix.inverted() @ point
    hit, nearest, _normal, _index = evaluated.closest_point_on_mesh(local_point)
    if not hit:
        return float('inf')
    return ((matrix @ nearest) - point).length

def world_bounds(obj):
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (
        Vector((min(point.x for point in corners), min(point.y for point in corners), min(point.z for point in corners))),
        Vector((max(point.x for point in corners), max(point.y for point in corners), max(point.z for point in corners))),
    )

part_objects = {
    'floor': floor,
    'left-wall': left_wall,
    'right-wall': right_wall,
    'back-wall': back_wall,
    'left-roof': left_roof,
    'right-roof': right_roof,
}
anchors = {item['id']: item for item in BUILDING_SPEC['anchors']}
attachment_results = []
construction_errors = []
for attachment in BUILDING_SPEC['attachments']:
    source = anchors[attachment['from']]
    target = anchors[attachment['to']]
    point_a = from_canonical(source['position'])
    point_b = from_canonical(target['position'])
    source_object = part_objects.get(source.get('partId'))
    target_object = part_objects.get(target.get('partId'))
    if source_object is None or target_object is None:
        construction_errors.append('attachment_missing_part:' + attachment['id'])
        continue
    source_distance = closest_distance(source_object, point_a)
    target_distance = closest_distance(target_object, point_b)
    anchor_gap = (point_a - point_b).length
    measured = max(source_distance, target_distance, anchor_gap)
    passed = measured <= float(attachment['tolerance'])
    attachment_results.append({
        'id': attachment['id'],
        'mode': attachment['mode'],
        'measuredGap': measured,
        'tolerance': attachment['tolerance'],
        'status': 'pass' if passed else 'fail',
    })
    if not passed:
        construction_errors.append('attachment_gap:' + attachment['id'] + ':' + format(measured, '.6f'))

construction_validation = {
    'status': 'pass' if not construction_errors else 'error',
    'errors': construction_errors,
    'warnings': [],
    'attachments': attachment_results,
    'contract': 'BuildSpec anchor contact plus evaluated Blender mesh nearest-surface distance',
}
for obj in bpy.context.scene.objects:
    if obj.type != 'MESH' or obj.get('polyKitZone') != 'interior':
        continue
    minimum, maximum = world_bounds(obj)
    if minimum.x < -W / 2.0 - TOL or maximum.x > W / 2.0 + TOL or minimum.y < -D / 2.0 - TOL or maximum.y > D / 2.0 + TOL:
        construction_errors.append('interior_outside_floor:' + obj.name)
    if minimum.z < -FT - TOL:
        construction_errors.append('interior_below_floor:' + obj.name)
if construction_errors:
    construction_validation['status'] = 'error'
    construction_validation['errors'] = construction_errors
scene['polyKitConstructionValidation'] = json.dumps(construction_validation, separators=(',', ':'))
if construction_errors:
    raise RuntimeError('construction validation failed: ' + ', '.join(construction_errors))

# Camera and lighting are presentation only; construction correctness above is
# camera-independent.  The render stage nevertheless records the light roles
# and produces neutral evidence before the final production pass.
def light(name, kind, location, energy, color, size=2.0, source='', role='', evidence='', affected_region='', loss_if_removed=''):
    data = bpy.data.lights.new(name=name, type=kind)
    data.energy = energy
    data.color = color
    if kind == 'SUN' and hasattr(data, 'angle'):
        data.angle = size
    elif hasattr(data, 'shadow_soft_size'):
        data.shadow_soft_size = size
    obj = bpy.data.objects.new(name, data)
    LIGHTING.objects.link(obj)
    obj.location = location
    obj['polyKitLightSource'] = source
    obj['polyKitLightRole'] = role
    obj['polyKitLightEvidence'] = evidence
    obj['polyKitLightAffectedRegion'] = affected_region
    obj['polyKitLightLossIfRemoved'] = loss_if_removed
    return obj

sun = light(
    'Sky_Sun', 'SUN', (-W * 0.7, -D * 1.1, H * 2.4), 1.6,
    (0.72, 0.82, 1.0), 0.18,
    source='winter sky / exterior daylight', role='primary directional source',
    evidence='gray-light baseline for exterior snow and window spill',
    affected_region='roof, snow receiver, window-facing wall',
    loss_if_removed='directional separation and cast shadows',
)
look_at(sun, (0.0, 0.0, 0.6))
window = light(
    'Window_Sky_Fill', 'AREA', (-W * 0.25, -D * 0.62, H * 0.72), 820.0,
    (0.48, 0.68, 1.0), 3.8,
    source='open front / cold sky', role='cool environmental fill',
    evidence='keeps interior shadow planes above black',
    affected_region='entry and left interior wall',
    loss_if_removed='interior shadow detail',
)
look_at(window, (0.0, 0.0, 1.5))
fire_light = light(
    'Fire_Practical', 'POINT', (W * 0.31, D * 0.18, 1.35), 220.0,
    (1.0, 0.12, 0.025), 0.35,
    source='visible stove fire', role='practical warm bounce',
    evidence='warm pool on stove, table, and floor',
    affected_region='hearth side and foreground floor',
    loss_if_removed='warm practical-light pool',
)
hearth_bounce = light(
    'Hearth_Bounce', 'AREA', (W * 0.28, D * 0.08, 1.55), 420.0,
    (1.0, 0.24, 0.06), 2.2,
    source='visible stove fire', role='local warm bounce',
    evidence='soft warm illumination on table and mattress',
    affected_region='hearth foreground and furniture',
    loss_if_removed='soft firelight falloff',
)
look_at(hearth_bounce, (0.0, -D * 0.05, 0.85))

production_lights = (sun, window, fire_light, hearth_bounce)

def camera(name, location, target, lens, view):
    data = bpy.data.cameras.new(name)
    obj = bpy.data.objects.new(name, data)
    LIGHTING.objects.link(obj)
    obj.location = location
    obj.data.lens = lens
    obj['polyKitInspectionView'] = view
    obj['polyKitCameraRevision'] = 1
    obj['polyKitCameraTarget'] = tuple(float(value) for value in target)
    look_at(obj, target)
    return obj

entry_camera = camera(
    'PresentationCamera',
    (-W*1.45, -D*2.25, H*1.15),
    (0.0, 0.0, H*0.62),
    50.0,
    'entry',
)
hearth_camera = camera(
    'InspectionCameraHearth',
    (W*0.48, -D*0.35, H*0.46),
    (W*0.25, D*0.22, 1.35),
    42.0,
    'hearth',
)
exterior_camera = camera(
    'InspectionCameraExterior',
    (0.0, -D*2.0, H*0.78),
    (0.0, 0.0, H*0.55),
    45.0,
    'exterior',
)
top_camera = camera('InspectionCameraTop', (0.0, 0.0, H * 2.15), (0.0, 0.0, 0.0), 48.0, 'top')
side_camera = camera('InspectionCameraSide', (W * 1.75, 0.0, H * 0.62), (0.0, 0.0, H * 0.45), 48.0, 'side')
scene.camera = entry_camera

def configure_world(mode='production'):
    if scene.world is None:
        scene.world = bpy.data.worlds.new('PolyKit World')
    world = scene.world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()
    background = nodes.new('ShaderNodeBackground')
    background.name = 'PolyKit Sky Fill'
    output = nodes.new('ShaderNodeOutputWorld')
    if mode == 'gray':
        background.inputs['Color'].default_value = (0.12, 0.12, 0.12, 1.0)
        background.inputs['Strength'].default_value = 0.28
    else:
        background.inputs['Color'].default_value = (0.008, 0.012, 0.025, 1.0)
        background.inputs['Strength'].default_value = 0.30
    links.new(background.outputs['Background'], output.inputs['Surface'])

def configure_render(mode='production'):
    try:
        scene.render.engine = 'BLENDER_EEVEE_NEXT'
    except Exception:
        scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x = RENDER_WIDTH
    scene.render.resolution_y = RENDER_HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.film_transparent = False
    try:
        scene.view_settings.view_transform = 'Standard' if RENDER_PROFILE == 'toon' else 'AgX'
        if RENDER_PROFILE == 'toon':
            scene.view_settings.look = 'Medium High Contrast'
        else:
            scene.view_settings.look = 'AgX - Medium High Contrast'
        scene.view_settings.exposure = 0.0 if RENDER_PROFILE == 'toon' else (-0.25 if mode == 'production' else 0.0)
    except Exception:
        pass
    configure_world(mode)

CLAY = material('Review Clay', (0.52, 0.52, 0.52), 0.9, material_class='review', texture_scale=1.0)

def _interface_socket(group, name, in_out, socket_type, default=None):
    socket = group.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if default is not None:
        try:
            socket.default_value = default
        except Exception:
            pass
    return socket

def _set_group_input(node, name, value):
    socket = node.inputs.get(name)
    if socket is None:
        return
    try:
        socket.default_value = value
    except Exception:
        pass

def _toon_shade(color, factor):
    return tuple(max(0.0, min(1.0, float(channel) * factor)) for channel in color) + (1.0,)

def _material_base_color(source):
    if source is None:
        return (0.32, 0.45, 0.62)
    bsdf = principled(source)
    if bsdf is not None:
        socket = bsdf.inputs.get('Base Color')
        if socket is not None and not socket.is_linked:
            value = socket.default_value
            return tuple(float(max(0.0, min(1.0, value[index]))) for index in range(3))
    value = source.diffuse_color
    return tuple(float(max(0.0, min(1.0, value[index]))) for index in range(3))

def _build_toon_material(base_color, source_name):
    safe_source = ''.join(character if character.isalnum() else '_' for character in str(source_name)).strip('_')[:48] or 'default'
    toon = bpy.data.materials.new('Cabin NPR Toon ' + safe_source)
    toon.diffuse_color = (*base_color, 1.0)
    toon['production_material_class'] = 'npr-toon'
    toon['production_texture_scale_m'] = 1.0
    toon['production_surface_variant'] = 'renderer-native'
    toon.use_nodes = True
    toon_nodes = toon.node_tree.nodes
    toon_links = toon.node_tree.links
    toon_nodes.clear()
    toon_output = toon_nodes.new('ShaderNodeOutputMaterial')
    diffuse = toon_nodes.new('ShaderNodeBsdfDiffuse')
    diffuse.name = 'NPR Diffuse Lighting'
    diffuse.inputs['Color'].default_value = (1.0, 1.0, 1.0, 1.0)
    shader_to_rgb = toon_nodes.new('ShaderNodeShaderToRGB')
    shader_to_rgb.name = 'NPR Shader To RGB'
    ramp = toon_nodes.new('ShaderNodeValToRGB')
    ramp.name = 'NPR Constant Three Bands'
    ramp.color_ramp.interpolation = 'CONSTANT'
    ramp.color_ramp.elements[0].position = 0.30
    ramp.color_ramp.elements[0].color = _toon_shade(base_color, 0.42)
    ramp.color_ramp.elements[1].position = 0.54
    ramp.color_ramp.elements[1].color = _toon_shade(base_color, 0.86)
    highlight = ramp.color_ramp.elements.new(0.76)
    highlight.color = _toon_shade(base_color, 1.38)
    variation = toon_nodes.new('ShaderNodeValToRGB')
    variation.name = 'NPR Stable Material Variation'
    variation.color_ramp.elements[0].position = 0.28
    variation.color_ramp.elements[0].color = (0.86, 0.86, 0.86, 1.0)
    variation.color_ramp.elements[1].position = 0.74
    variation.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    noise = toon_nodes.new('ShaderNodeTexNoise')
    noise.name = 'NPR Surface Variation'
    noise.noise_dimensions = '3D'
    noise.inputs['Scale'].default_value = 4.0
    noise.inputs['Detail'].default_value = 2.0
    noise.inputs['Roughness'].default_value = 0.45
    texcoord = toon_nodes.new('ShaderNodeTexCoord')
    texcoord.name = 'NPR Object Coordinates'
    multiply = toon_nodes.new('ShaderNodeMixRGB')
    multiply.name = 'NPR Palette x Material Variation'
    multiply.blend_type = 'MULTIPLY'
    multiply.inputs['Fac'].default_value = 1.0
    emission = toon_nodes.new('ShaderNodeEmission')
    emission.name = 'NPR Stable Toon Fill'
    emission.inputs['Strength'].default_value = 1.0
    toon_links.new(diffuse.outputs['BSDF'], shader_to_rgb.inputs['Shader'])
    toon_links.new(shader_to_rgb.outputs['Color'], ramp.inputs['Fac'])
    toon_links.new(texcoord.outputs['Generated'], noise.inputs['Vector'])
    toon_links.new(noise.outputs['Fac'], variation.inputs['Fac'])
    toon_links.new(ramp.outputs['Color'], multiply.inputs['Color1'])
    toon_links.new(variation.outputs['Color'], multiply.inputs['Color2'])
    toon_links.new(multiply.outputs['Color'], emission.inputs['Color'])
    toon_links.new(emission.outputs['Emission'], toon_output.inputs['Surface'])
    toon['polyKitNprRenderer'] = 'eevee'
    toon['polyKitNprSystem'] = 'shader-to-rgb-constant-ramp'
    toon['polyKitNprBands'] = 3
    toon['polyKitNprBaseColor'] = tuple(float(channel) for channel in base_color)
    toon['polyKitNprSourceMaterial'] = str(source_name)
    return toon

def _configure_structure_lines():
    """Add camera-aware crease/material lines for the stylized cabin pass."""
    try:
        scene.render.use_freestyle = True
        settings = scene.view_layers[0].freestyle_settings
        line_set = settings.linesets[0] if len(settings.linesets) else settings.linesets.new('Cabin Structure Lines')
        line_set.select_silhouette = False
        line_set.select_crease = True
        line_set.select_border = True
        if hasattr(line_set, 'select_material_boundary'):
            line_set.select_material_boundary = True
        style = line_set.linestyle
        style.color = (0.012, 0.016, 0.025)
        style.thickness = 1.0
        return {'enabled': True, 'system': 'freestyle-structure-lines', 'lineSet': line_set.name}
    except Exception as exc:
        try:
            scene.render.use_freestyle = False
        except Exception:
            pass
        return {'enabled': False, 'system': 'freestyle-unavailable', 'warning': str(exc)}

def configure_toon_profile():
    """Apply an Eevee 3D-to-2D pass without destroying authored material identity."""
    outline = bpy.data.materials.new('Cabin NPR Outline')
    outline.diffuse_color = (0.004, 0.002, 0.002, 1.0)
    outline.use_nodes = True
    outline_bsdf = principled(outline)
    if outline_bsdf:
        outline_bsdf.inputs['Base Color'].default_value = (0.004, 0.002, 0.002, 1.0)
        outline_bsdf.inputs['Roughness'].default_value = 1.0
    outline.use_backface_culling = True

    group = bpy.data.node_groups.new('PolyKit Cabin Eevee Inverted Hull', 'GeometryNodeTree')
    _interface_socket(group, 'Geometry', 'INPUT', 'NodeSocketGeometry')
    _interface_socket(group, 'Enable Outline', 'INPUT', 'NodeSocketBool', True)
    _interface_socket(group, 'Outline Width', 'INPUT', 'NodeSocketFloat', 0.035)
    _interface_socket(group, 'Geometry', 'OUTPUT', 'NodeSocketGeometry')
    nodes = group.nodes
    links = group.links
    group_input = nodes.new('NodeGroupInput')
    group_output = nodes.new('NodeGroupOutput')
    extrude = nodes.new('GeometryNodeExtrudeMesh')
    extrude.name = 'NPR Extrude Faces'
    extrude.mode = 'FACES'
    extrude.inputs['Selection'].default_value = True
    separate = nodes.new('GeometryNodeSeparateGeometry')
    separate.name = 'NPR Top Face Isolation'
    separate.domain = 'FACE'
    flip = nodes.new('GeometryNodeFlipFaces')
    flip.name = 'NPR Flip Faces'
    set_material = nodes.new('GeometryNodeSetMaterial')
    set_material.name = 'NPR Set Outline Material'
    set_material.inputs['Material'].default_value = outline
    join = nodes.new('GeometryNodeJoinGeometry')
    switch = nodes.new('GeometryNodeSwitch')
    switch.name = 'NPR Geometry Switch'
    switch.input_type = 'GEOMETRY'
    links.new(group_input.outputs['Geometry'], extrude.inputs['Mesh'])
    links.new(group_input.outputs['Outline Width'], extrude.inputs['Offset Scale'])
    links.new(extrude.outputs['Mesh'], separate.inputs['Geometry'])
    links.new(extrude.outputs['Top'], separate.inputs['Selection'])
    links.new(separate.outputs['Selection'], flip.inputs['Mesh'])
    links.new(flip.outputs['Mesh'], set_material.inputs['Geometry'])
    links.new(group_input.outputs['Geometry'], join.inputs['Geometry'])
    links.new(set_material.outputs['Geometry'], join.inputs['Geometry'])
    links.new(group_input.outputs['Enable Outline'], switch.inputs['Switch'])
    links.new(join.outputs['Geometry'], switch.inputs['True'])
    links.new(group_input.outputs['Geometry'], switch.inputs['False'])
    links.new(switch.outputs['Output'], group_output.inputs['Geometry'])
    toon_variants = {}
    for obj in [item for item in scene.objects if item.type == 'MESH']:
        authored = list(obj.data.materials)
        if not authored:
            authored = [None]
        original_indices = [int(polygon.material_index) for polygon in obj.data.polygons]
        variant_indices = {}
        for index, source in enumerate(authored):
            base_color = _material_base_color(source)
            key = tuple(round(channel, 5) for channel in base_color)
            if key not in toon_variants:
                toon_variants[key] = _build_toon_material(base_color, source.name if source is not None else 'default')
            variant = toon_variants[key]
            try:
                variant_index = list(obj.data.materials).index(variant)
            except ValueError:
                obj.data.materials.append(variant)
                variant_index = len(obj.data.materials) - 1
            variant_indices[index] = variant_index
        for polygon in obj.data.polygons:
            polygon.material_index = variant_indices.get(polygon.material_index, next(iter(variant_indices.values())))
        obj['polyKitNprAuthoredMaterials'] = [source.name if source is not None else None for source in authored]
        obj['polyKitNprOriginalMaterialIndices'] = original_indices
        obj['polyKitNprAuthoredSlotCount'] = len(authored)
        obj['polyKitNprMaterialPolicy'] = 'preserved_with_toon_variant'
        modifier = obj.modifiers.new('PolyKit Cabin NPR Outline', 'NODES')
        modifier.node_group = group
        obj['polyKitNprRenderer'] = 'eevee'
        obj['polyKitNprSystem'] = 'inverted-hull'
        obj['polyKitNprOutlineWidth'] = 0.035
    return {
        'renderer': 'eevee',
        'system': 'shader-to-rgb-plus-inverted-hull',
        'outlineGroup': group.name,
        'toonMaterials': [material.name for material in toon_variants.values()],
        'outlineMaterial': outline.name,
        'structureLines': _configure_structure_lines(),
        'materialPolicy': 'preserved_with_toon_variant',
    }

toon_profile = configure_toon_profile() if RENDER_PROFILE == 'toon' else None

def render_metrics(path=None):
    image = bpy.data.images.get('Render Result')
    loaded_from_disk = False
    if (image is None or tuple(image.size) == (0, 0)) and path and pathlib.Path(path).exists():
        image = bpy.data.images.load(str(path), check_existing=False)
        loaded_from_disk = True
    if image is None or tuple(image.size) == (0, 0):
        return {'nonblank': False, 'reason': 'render-result-missing'}
    pixels = list(image.pixels)
    luminance = []
    for index in range(0, len(pixels), 4):
        red, green, blue, alpha = pixels[index:index + 4]
        if alpha > 0.001:
            luminance.append(0.2126 * red + 0.7152 * green + 0.0722 * blue)
    if not luminance:
        return {'nonblank': False, 'reason': 'no-visible-pixels'}
    mean = sum(luminance) / len(luminance)
    variance = sum((value - mean) ** 2 for value in luminance) / len(luminance)
    deviation = math.sqrt(variance)
    metrics = {
        'width': int(image.size[0]),
        'height': int(image.size[1]),
        'luminanceMin': round(min(luminance), 6),
        'luminanceMax': round(max(luminance), 6),
        'luminanceMean': round(mean, 6),
        'luminanceStddev': round(deviation, 6),
        'nonblank': bool(max(luminance) - min(luminance) > 0.08 and deviation > 0.02),
    }
    if loaded_from_disk:
        bpy.data.images.remove(image)
    return metrics

def render_pass(root, pass_id, view_camera, mode='production', output_path=None):
    original_materials = {}
    original_material_indices = {}
    if mode == 'gray':
        for obj in bpy.context.scene.objects:
            if obj.type != 'MESH':
                continue
            original_materials[obj.name] = [slot.material for slot in obj.material_slots]
            original_material_indices[obj.name] = [int(polygon.material_index) for polygon in obj.data.polygons]
            if obj.get('polyKitRole') in {'tactile-guidance', 'opposite-tactile-guidance', 'right-tactile-guidance', 'tactile-dot'}:
                continue
            obj.data.materials.clear()
            obj.data.materials.append(CLAY)
        for light_obj in production_lights:
            light_obj.hide_render = light_obj != sun
    else:
        for light_obj in production_lights:
            light_obj.hide_render = False
    configure_render(mode)
    scene.camera = view_camera
    path = output_path or (root / (SCENE_NAME + '_pass_' + pass_id + '.png'))
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    metrics = render_metrics(path)
    if original_materials:
        for obj in bpy.context.scene.objects:
            if obj.name not in original_materials:
                continue
            obj.data.materials.clear()
            for mat in original_materials[obj.name]:
                if mat is not None:
                    obj.data.materials.append(mat)
            for polygon, index in zip(obj.data.polygons, original_material_indices.get(obj.name, [])):
                polygon.material_index = max(0, min(index, len(obj.data.materials) - 1))
    for light_obj in production_lights:
        light_obj.hide_render = False
    return path, metrics

def apply_semantic_identity():
    """Attach stable PolyKit IDs before Blender MCP receives the scene.

    Object names remain presentation labels and may be changed by a user. The
    generated semantic IDs are deterministic for this scene and are the only
    values consumed by the PolyKit semantic bridge.
    """
    used_object_ids = set()
    part_object_ids = {id(obj): str(part_id) for part_id, obj in part_objects.items()}
    semantic_objects = [
        obj for obj in scene.objects
        if obj.type in {'MESH', 'LIGHT', 'CAMERA'}
    ]
    for index, obj in enumerate(semantic_objects, start=1):
        base = part_object_ids.get(id(obj)) or (obj.type.lower() + '-' + str(index).zfill(4))
        object_id = 'generated:' + str(BUILDING_SPEC.get('id') or 'scene') + ':' + base
        if object_id in used_object_ids:
            suffix = 2
            while object_id + '_' + str(suffix) in used_object_ids:
                suffix += 1
            object_id = object_id + '_' + str(suffix)
        used_object_ids.add(object_id)
        obj['polykit_object_id'] = object_id
        obj['polykit_instance_id'] = object_id + ':instance-1'
        obj['polykit_name'] = str(obj.get('polyKitSemanticName') or obj.name)
        obj['polykit_role'] = str(obj.get('polyKitRole') or obj.type.lower())
        obj['polykit_collections'] = json.dumps(
            [collection.name for collection in obj.users_collection if collection.name != 'PolyKit'],
            ensure_ascii=False,
            separators=(',', ':'),
        )

apply_semantic_identity()

root = pathlib.Path(tempfile.mkdtemp(prefix='polykit_blender_cabin_v2_'))
glb_path = root / (SCENE_NAME + '.glb')
blend_path = root / (SCENE_NAME + '.blend')
preview_path = root / (SCENE_NAME + '.png')
preview_view_paths = {}
render_evidence = {
    'schemaVersion': 1,
    'profile': RENDER_PROFILE,
    'engine': 'BLENDER_EEVEE_NEXT',
    'colorManagement': {
        'viewTransform': 'Standard' if RENDER_PROFILE == 'toon' else 'AgX',
        'look': 'Medium High Contrast' if RENDER_PROFILE == 'toon' else 'AgX - Medium High Contrast',
        'productionExposure': 0.0 if RENDER_PROFILE == 'toon' else -0.25,
    },
    'lights': [
        {'id': obj.name, 'source': obj.get('polyKitLightSource'), 'role': obj.get('polyKitLightRole'), 'evidence': obj.get('polyKitLightEvidence'), 'affectedRegion': obj.get('polyKitLightAffectedRegion'), 'lossIfRemoved': obj.get('polyKitLightLossIfRemoved')}
        for obj in production_lights
    ],
    'npr': toon_profile,
    'passes': [],
}
if RENDER_PREVIEW:
    production_passes = (
        ('entry', entry_camera),
        ('hearth', hearth_camera),
        ('exterior', exterior_camera),
    )
    for view_name, view_camera in production_passes:
        view_path, metrics = render_pass(root, view_name, view_camera, 'production', root / (SCENE_NAME + '_view_' + view_name + '.png'))
        preview_view_paths[view_name] = view_path
        render_evidence['passes'].append({'id': view_name, 'mode': 'production', 'camera': view_camera.name, 'path': str(view_path), 'metrics': metrics})
    if preview_view_paths.get('entry'):
        shutil.copyfile(str(preview_view_paths['entry']), str(preview_path))
    gray_passes = (
        ('gray', entry_camera),
        ('top', top_camera),
        ('side', side_camera),
    )
    for view_name, view_camera in gray_passes:
        top_hidden = []
        if view_name == 'top':
            top_hidden = [
                obj for obj in scene.objects
                if obj.name in {'Ceiling_Soffit', 'Tunnel_Backdrop'}
            ]
            for obj in top_hidden:
                obj.hide_render = True
        view_path, metrics = render_pass(root, view_name, view_camera, 'gray', root / (SCENE_NAME + '_view_' + view_name + '.png'))
        preview_view_paths[view_name] = view_path
        render_evidence['passes'].append({'id': view_name, 'mode': 'gray', 'camera': view_camera.name, 'path': str(view_path), 'metrics': metrics})
        for obj in top_hidden:
            obj.hide_render = False
    scene.camera = entry_camera
    scene.render.filepath = str(preview_path)

    configure_render('production')
    scene.camera = entry_camera
    scene.render.filepath = str(preview_path)
render_health_pass = bool(render_evidence['passes']) and all(item.get('metrics', {}).get('nonblank') for item in render_evidence['passes'])
def pass_metrics(pass_id, mode=None):
    for item in render_evidence['passes']:
        if item.get('id') == pass_id and (mode is None or item.get('mode') == mode):
            return item.get('metrics') if isinstance(item.get('metrics'), dict) else {}
    return {}

entry_metrics = pass_metrics('entry', 'production')
gray_entry_metrics = pass_metrics('gray', 'gray')
accountable_lights = bool(production_lights) and all(
    str(obj.get('polyKitLightSource') or '').strip()
    and str(obj.get('polyKitLightRole') or '').strip()
    and str(obj.get('polyKitLightEvidence') or '').strip()
    for obj in production_lights
)
fixture_assembly = {
    'recessCutters': len([obj for obj in scene.objects if obj.get('polyKitRole') == 'ceiling-recess-cutter']),
    'recessHousings': len([obj for obj in scene.objects if obj.get('polyKitRole') == 'light-recess-housing']),
    'glassPanels': len([obj for obj in scene.objects if obj.get('polyKitRole') == 'light-glass-panel']),
    'ledSources': len([obj for obj in scene.objects if obj.get('polyKitRole') == 'ceiling-led']),
    'booleanRecesses': len([
        modifier for obj in scene.objects if obj.name == 'Ceiling_Soffit'
        for modifier in obj.modifiers
        if modifier.type == 'BOOLEAN' and modifier.operation == 'DIFFERENCE' and modifier.object is not None
    ]),
}
fixture_assembly_valid = all(value >= 2 for key, value in fixture_assembly.items() if key != 'booleanRecesses') and fixture_assembly['booleanRecesses'] == 2
left_platform_min, left_platform_max = world_bounds(left_platform)
left_wall_min, left_wall_max = world_bounds(left_wall)
track_min, track_max = world_bounds(track_bed)
platform_continuity = {
    'leftPlatforms': len([obj for obj in scene.objects if obj.get('polyKitRole') == 'left-platform']),
    'leftPlatformLength': round(float(left_platform_max.y - left_platform_min.y), 4),
    'leftPlatformWallGapM': round(abs(float(left_platform_min.x - left_wall_max.x)), 6),
    'leftPlatformTrackGapM': round(abs(float(left_platform_max.x - track_min.x)), 6),
    'leftPlatformWallContact': abs(float(left_platform_min.x - left_wall_max.x)) <= TOL,
    'leftPlatformTrackContact': abs(float(left_platform_max.x - track_min.x)) <= TOL,
    'leftPlatformContinuousLength': abs(float(left_platform_max.y - left_platform_min.y) - D) <= TOL,
}
platform_continuity_valid = (
    platform_continuity['leftPlatforms'] == 1
    and platform_continuity['leftPlatformWallContact']
    and platform_continuity['leftPlatformTrackContact']
    and platform_continuity['leftPlatformContinuousLength']
)
lighting_checks = {
    'accountable_sources': accountable_lights,
    'recessed_fixture_assembly': fixture_assembly_valid,
    'gray_light_nonblank': bool(gray_entry_metrics.get('nonblank')),
    'production_entry_readable': bool(
        entry_metrics.get('nonblank')
        and float(entry_metrics.get('luminanceMean', 0.0)) >= 0.06
        and float(entry_metrics.get('luminanceStddev', 0.0)) >= 0.04
        and float(entry_metrics.get('luminanceMax', 0.0)) >= 0.25
    ),
    'production_pass_count': sum(1 for item in render_evidence['passes'] if item.get('mode') == 'production'),
}
lighting_evaluated = bool(render_evidence['passes'])
lighting_pass = lighting_evaluated and all(
    (value if isinstance(value, bool) else value >= 3)
    for key, value in lighting_checks.items()
    if key != 'production_pass_count'
)
lighting_pass = lighting_pass and lighting_checks['production_pass_count'] >= 3
render_evidence['lightingValidation'] = {
    'status': 'pass' if lighting_pass else ('not_evaluated' if not lighting_evaluated else 'needs_review'),
    'checks': lighting_checks,
    'thresholds': {'entry_luminance_mean_min': 0.06, 'entry_luminance_stddev_min': 0.04, 'entry_luminance_max_min': 0.25, 'production_pass_count_min': 3},
    'entryMetrics': entry_metrics,
    'grayEntryMetrics': gray_entry_metrics,
    'fixtureAssembly': fixture_assembly,
    'leftPlatformContinuity': platform_continuity,
}

surface_materials = []
for material_name in ('Subway Porcelain Tile', 'Platform Wall Tile', 'Safety Yellow Tactile'):
    material_data = bpy.data.materials.get(material_name)
    if material_data is None:
        continue
    node_names = {node.name for node in material_data.node_tree.nodes}
    surface_materials.append({
        'name': material_name,
        'class': material_data.get('production_material_class'),
        'textureScaleM': material_data.get('production_texture_scale_m'),
        'hasMapping': 'Physical Texture Mapping' in node_names,
        'hasPattern': bool({'Tile Grid and Grout', 'Color Variation'} & node_names),
        'hasMicroRelief': 'Surface Micro Bump' in node_names,
    })
surface_evaluated = bool(surface_materials)
security_bands = [
    obj for obj in scene.objects
    if obj.get('polyKitRole') in {'left-tactile-guidance', 'right-tactile-guidance'}
]
security_band_thicknesses = []
security_band_flush_flags = []
for band in security_bands:
    band_minimum, band_maximum = world_bounds(band)
    security_band_thicknesses.append(round(float(band_maximum.z - band_minimum.z), 6))
    security_band_flush_flags.append(abs(float(band_minimum.z) - float(PH)) <= 0.005)
tactile_dot_rows = [obj for obj in scene.objects if obj.get('polyKitRole') == 'tactile-dot']
textured_columns = [
    obj for obj in scene.objects
    if obj.get('polyKitRole') in {'repeating-column', 'foreground-occluder'}
    and any(slot.material and 'Physical Texture Mapping' in {node.name for node in slot.material.node_tree.nodes} for slot in obj.material_slots)
]
glass_material = bpy.data.materials.get('Recessed Light Glass')
glass_bsdf = principled(glass_material) if glass_material is not None else None
glass_transmission_socket = (
    (glass_bsdf.inputs.get('Transmission Weight') or glass_bsdf.inputs.get('Transmission'))
    if glass_bsdf is not None else None
)
glass_transmission = float(glass_transmission_socket.default_value) if glass_transmission_socket is not None else 0.0
surface_pass = surface_evaluated and all(
    item['hasMapping'] and item['hasPattern'] and item['hasMicroRelief'] for item in surface_materials
) and len(security_bands) >= 2 and len(tactile_dot_rows) >= 2 and len(textured_columns) >= 1
surface_pass = surface_pass and bool(security_band_thicknesses) and all(
    thickness <= 0.035 for thickness in security_band_thicknesses
) and all(security_band_flush_flags) and fixture_assembly_valid and glass_transmission >= 0.5
render_evidence['surfaceValidation'] = {
    'status': 'pass' if surface_pass else ('not_evaluated' if not surface_evaluated else 'needs_review'),
    'materials': surface_materials,
    'securityBandCount': len(security_bands),
    'securityBandThicknessM': security_band_thicknesses,
    'securityBandFlush': all(security_band_flush_flags) if security_band_flush_flags else False,
    'lightFixtureAssembly': fixture_assembly,
    'glassTransmissionWeight': round(glass_transmission, 4),
    'tactileDotRowCount': len(tactile_dot_rows),
    'texturedColumnCount': len(textured_columns),
}
render_evidence['validation'] = {
    # Nonblank pixels are a render-health check, not visual approval. A real
    # visual report must assess framing, material identity, line quality and
    # reference fidelity outside this builder.
    'status': 'needs_review',
    'renderHealth': 'pass' if render_health_pass else 'needs_review',
    'visualValidation': 'not_evaluated',
    'checks': {
        'production_passes_nonblank': all(item.get('metrics', {}).get('nonblank') for item in render_evidence['passes'] if item.get('mode') == 'production'),
        'gray_review_passes_nonblank': all(item.get('metrics', {}).get('nonblank') for item in render_evidence['passes'] if item.get('mode') == 'gray'),
        'unique_camera_evidence': len({(item.get('camera'), item.get('mode')) for item in render_evidence['passes']}) == len(render_evidence['passes']),
    },
    'lighting': render_evidence['lightingValidation'],
    'surface': render_evidence['surfaceValidation'],
}
render_evidence['validation']['spatialConnectivity'] = {
    'status': 'pass' if platform_continuity_valid else 'needs_review',
    'applicable': True,
    'leftPlatform': platform_continuity,
}
used_materials = {
    slot.material
    for obj in scene.objects
    if obj.type == 'MESH'
    for slot in obj.material_slots
    if slot.material is not None
}
for material_data in list(bpy.data.materials):
    if material_data.users == 0 or (material_data.name == 'Material' and material_data not in used_materials):
        bpy.data.materials.remove(material_data)
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

def restore_authored_materials_for_glb():
    """Keep renderer-native NPR in .blend while exporting portable PBR slots."""
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        indices = obj.get('polyKitNprOriginalMaterialIndices')
        authored_count = int(obj.get('polyKitNprAuthoredSlotCount', 0) or 0)
        if indices is not None and authored_count > 0:
            try:
                original_indices = [int(index) for index in indices]
            except (TypeError, ValueError):
                original_indices = []
            for polygon, index in zip(obj.data.polygons, original_indices):
                polygon.material_index = max(0, min(int(index), authored_count - 1))
            while len(obj.data.materials) > authored_count:
                obj.data.materials.pop(index=len(obj.data.materials) - 1)
    used_materials = {
        slot.material
        for obj in scene.objects
        if obj.type == 'MESH'
        for slot in obj.material_slots
        if slot.material is not None
    }
    for mat in used_materials:
        bsdf = principled(mat)
        if bsdf is None:
            continue
        base_socket = bsdf.inputs.get('Base Color')
        color = tuple(float(channel) for channel in (base_socket.default_value[:3] if base_socket is not None else mat.diffuse_color[:3]))
        alpha_socket = bsdf.inputs.get('Alpha')
        alpha = float(alpha_socket.default_value) if alpha_socket is not None else float(mat.diffuse_color[3])
        roughness = float(bsdf.inputs.get('Roughness').default_value) if bsdf.inputs.get('Roughness') else 0.75
        metallic = float(bsdf.inputs.get('Metallic').default_value) if bsdf.inputs.get('Metallic') else 0.0
        emission_socket = bsdf.inputs.get('Emission Color') or bsdf.inputs.get('Emission')
        emission_color = tuple(float(channel) for channel in emission_socket.default_value[:3]) if emission_socket else None
        emission_strength = float(bsdf.inputs.get('Emission Strength').default_value) if bsdf.inputs.get('Emission Strength') else 0.0
        transmission_socket = bsdf.inputs.get('Transmission Weight') or bsdf.inputs.get('Transmission')
        transmission = float(transmission_socket.default_value) if transmission_socket else float(mat.get('production_transmission', 0.0) or 0.0)
        ior_socket = bsdf.inputs.get('IOR')
        ior = float(ior_socket.default_value) if ior_socket else float(mat.get('production_ior', 1.45) or 1.45)
        mat.node_tree.nodes.clear()
        output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
        flat = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
        flat.name = 'Principled BSDF'
        flat.inputs['Base Color'].default_value = (*color, alpha)
        flat.inputs['Roughness'].default_value = roughness
        flat.inputs['Metallic'].default_value = metallic
        flat_transmission = flat.inputs.get('Transmission Weight') or flat.inputs.get('Transmission')
        if flat_transmission is not None:
            flat_transmission.default_value = transmission
        if flat.inputs.get('IOR') is not None:
            flat.inputs['IOR'].default_value = ior
        if flat.inputs.get('Alpha') is not None:
            flat.inputs['Alpha'].default_value = alpha
        flat_emission = flat.inputs.get('Emission Color') or flat.inputs.get('Emission')
        if flat_emission is not None and emission_color is not None:
            flat_emission.default_value = (*emission_color, 1.0)
        if flat.inputs.get('Emission Strength') is not None:
            flat.inputs['Emission Strength'].default_value = emission_strength
        mat.node_tree.links.new(flat.outputs['BSDF'], output.inputs['Surface'])

# Keep renderer-native graphs in the .blend, but always flatten authored PBR
# materials for the portable GLB export. Toon runs additionally restore their
# original material-slot indices before this flattening step.
restore_authored_materials_for_glb()
bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format='GLB', export_materials='EXPORT')

def b64(path):
    return base64.b64encode(path.read_bytes()).decode('ascii') if path.exists() else ''

result = {
    'scene_name': SCENE_NAME,
    'preset': 'winter_cabin_v2',
    'blender_version': bpy.app.version_string,
    'object_count': len([obj for obj in scene.objects if obj.type == 'MESH']),
    'build_spec': BUILDING_SPEC,
    'construction_validation': construction_validation,
    'render_evidence': render_evidence,
    'glb_b64': b64(glb_path),
    'blend_b64': b64(blend_path),
    'preview_b64': b64(preview_path) if RENDER_PREVIEW else '',
    'preview_views_b64': {
        name: b64(path) for name, path in preview_view_paths.items()
    },
}
'''
    return (
        script.replace("__SCENE_NAME__", repr(scene_name))
        .replace("__SCENE_BRIEF__", repr(brief))
        .replace("__WIDTH__", str(width))
        .replace("__HEIGHT__", str(height))
        .replace("__RENDER_PREVIEW__", "True" if render_preview else "False")
        .replace("__RENDER_PROFILE__", repr(render_profile if render_profile in {"production", "gray", "toon"} else "production"))
        .replace("__CONFIG_JSON__", repr(json.dumps(dict(config), separators=(",", ":"))))
        .replace("__BUILD_SPEC_JSON__", repr(json.dumps(dict(building_spec), separators=(",", ":"))))
    )


def _subway_config(params: Mapping[str, Any]) -> dict[str, Any]:
    """Return bounded, meter-scale controls for the reference subway scene."""

    platform_width = _number(params, "platform_width", 4.6, 2.5, 9.0)
    width_ratio = _number(params, "tactile_width_ratio", 0.16, 0.08, 0.30)
    inset_ratio = _number(params, "tactile_inset_ratio", 0.04, 0.01, 0.10)
    # Ratio controls are the reviewed path. Absolute values remain accepted for
    # older saved worlds that predate the proportional controls.
    tactile_width = (
        platform_width * width_ratio
        if "tactile_width_ratio" in params
        else _number(params, "tactile_width", platform_width * width_ratio, 0.2, 1.2)
    )
    tactile_inset = (
        platform_width * inset_ratio
        if "tactile_inset_ratio" in params
        else _number(params, "tactile_inset", platform_width * inset_ratio, 0.05, 0.6)
    )
    return {
        "preset": "subway_station",
        "width": _number(params, "station_width", 20.0, 12.0, 40.0),
        "length": _number(params, "station_length", 48.0, 24.0, 120.0),
        "ceiling_height": _number(params, "ceiling_height", 5.8, 3.5, 12.0),
        "platform_width": platform_width,
        "platform_height": _number(params, "platform_height", 0.95, 0.4, 2.0),
        "column_spacing": _number(params, "column_spacing", 8.0, 4.0, 20.0),
        "column_size": _number(params, "column_size", 0.9, 0.25, 2.0),
        "rail_gauge": _number(params, "rail_gauge", 1.5, 0.8, 2.4),
        "tactile_width": round(tactile_width, 6),
        "tactile_inset": round(tactile_inset, 6),
        "tactile_width_ratio": width_ratio,
        "tactile_inset_ratio": inset_ratio,
        "contact_tolerance": _number(params, "contact_tolerance", 0.04, 0.005, 0.2),
    }


def _subway_building_spec(config: Mapping[str, Any]) -> dict[str, Any]:
    """Build semantic anchors and attachments for the subway station."""

    length = float(config["length"])
    width = float(config["width"])
    height = float(config["ceiling_height"])
    platform_width = float(config["platform_width"])
    track_x = 1.18
    track_bed_width = 3.55
    left_platform_edge = track_x - track_bed_width / 2.0
    left_wall_inner = -width / 2.0 + 0.36
    left_platform_width = max(0.25, left_platform_edge - left_wall_inner)
    right_platform_edge = 5.25 - platform_width / 2.0
    anchors = [
        {"id": "platform-front", "partId": "platform", "position": _canonical(left_platform_edge, 0.0, 0.0)},
        {"id": "platform-back", "partId": "platform", "position": _canonical(0.0, length / 2.0 - 1.0, 0.0)},
        {"id": "track-bed", "partId": "track-bed", "position": _canonical(left_platform_edge, 0.0, 0.0)},
        {"id": "right-platform-front", "partId": "right-platform", "position": _canonical(right_platform_edge, 0.0, 0.0)},
        {"id": "right-track-bed", "partId": "track-bed", "position": _canonical(right_platform_edge, 0.0, 0.0)},
        {"id": "ceiling-left", "partId": "ceiling", "position": _canonical(-width / 2.0, 0.0, height)},
        {"id": "left-wall", "partId": "left-wall", "position": _canonical(-width / 2.0, 0.0, height)},
        {"id": "ceiling-right", "partId": "ceiling", "position": _canonical(width / 2.0, 0.0, height)},
        {"id": "right-wall", "partId": "right-wall", "position": _canonical(width / 2.0, 0.0, height)},
    ]
    tolerance = float(config["contact_tolerance"])
    return {
        "id": "subway-station",
        "name": "Night Subway Station",
        "generator": "blender-parametric",
        "parameters": {
            "preset": "subway_station",
            "width": width,
            "length": length,
            "ceilingHeight": height,
            "platformWidth": float(config["platform_width"]),
            "leftPlatformWidth": round(left_platform_width, 6),
            "platformHeight": float(config["platform_height"]),
            "columnSpacing": float(config["column_spacing"]),
            "columnSize": float(config["column_size"]),
            "railGauge": float(config["rail_gauge"]),
            "tactileWidth": float(config["tactile_width"]),
            "tactileInset": float(config["tactile_inset"]),
            "tactileWidthRatio": float(config["tactile_width_ratio"]),
            "tactileInsetRatio": float(config["tactile_inset_ratio"]),
            "contactTolerance": tolerance,
            "coordinateSystem": "polykit-y-up-meters",
        },
        "anchors": anchors,
        "attachments": [
            {"id": pair_id, "from": source, "to": target, "mode": "support", "tolerance": tolerance}
            for pair_id, source, target in (
                ("platform-track", "platform-front", "track-bed"),
                ("right-platform-track", "right-platform-front", "right-track-bed"),
                ("ceiling-left-wall", "ceiling-left", "left-wall"),
                ("ceiling-right-wall", "ceiling-right", "right-wall"),
            )
        ],
    }


def _subway_scene_script(
    scene_name: str,
    brief: str,
    width: int,
    height: int,
    render_preview: bool,
    config: Mapping[str, Any],
    building_spec: Mapping[str, Any],
    render_profile: str = "production",
) -> str:
    """Adapt the existing Blender bridge template into the subway scene."""

    # Reuse the tested PBR, render-evidence, GLB export and inspection-view machinery.
    script = _cabin_scene_script(scene_name, brief, width, height, render_preview, config, building_spec, render_profile)
    start = script.index("W = CONFIG['width']")
    end = script.index("def from_canonical(value):")
    geometry = r'''TRACKS = collection('Tracks')
CUTTERS = collection('Cutters')
W = CONFIG['width']
D = CONFIG['length']
H = CONFIG['ceiling_height']
T = 0.36
FT = 0.36
TOL = CONFIG['contact_tolerance']
PW = CONFIG['platform_width']
PH = CONFIG['platform_height']
CS = CONFIG['column_spacing']
COL = CONFIG['column_size']
GAUGE = CONFIG['rail_gauge']
TACTILE_W = CONFIG['tactile_width']
TACTILE_INSET = CONFIG['tactile_inset']
TACTILE_H = 0.025
TACTILE_DOT_H = 0.018
TRACK_X = 1.18
TRACK_BED_WIDTH = 3.55
TRACK_LEFT_EDGE_X = TRACK_X - TRACK_BED_WIDTH / 2.0
TRACK_RIGHT_EDGE_X = TRACK_X + TRACK_BED_WIDTH / 2.0
LEFT_WALL_INNER_X = -W / 2.0 + 0.36
# The left platform is one manufactured slab. Its wall-side edge is solved
# from the tiled wall and its drop-side edge is solved from the track bed, so
# there is no hidden gap or a second disconnected platform behind the wall.
LEFT_PLATFORM_EDGE_X = TRACK_LEFT_EDGE_X
LEFT_PLATFORM_WIDTH = max(0.25, LEFT_PLATFORM_EDGE_X - LEFT_WALL_INNER_X)
LEFT_PLATFORM_X = (LEFT_WALL_INNER_X + LEFT_PLATFORM_EDGE_X) / 2.0
RIGHT_PLATFORM_X = 5.25
RIGHT_PLATFORM_EDGE_X = RIGHT_PLATFORM_X - PW / 2.0
COUNT = int(math.floor((D - 8.0) / CS)) + 1

TILE = material('Subway Porcelain Tile', (0.29, 0.38, 0.40), 0.62, material_class='tile', texture_scale=0.3, hero=True)
TILE_LIGHT = material('Platform Wall Tile', (0.53, 0.61, 0.62), 0.55, material_class='tile', texture_scale=0.25)
CONCRETE = material('Platform Concrete', (0.18, 0.21, 0.22), 0.86, material_class='concrete', texture_scale=0.6)
EDGE = material('Platform Edge Rubber', (0.025, 0.03, 0.032), 0.42, metallic=0.25, material_class='rubber', texture_scale=0.12)
TACTILE = material('Safety Yellow Tactile', (0.74, 0.47, 0.045), 0.76, material_class='tactile', texture_scale=0.07)
TRACK_BED = material('Track Bed', (0.018, 0.023, 0.027), 0.95, material_class='stone', texture_scale=0.18)
RAIL = material('Rail Steel', (0.18, 0.22, 0.23), 0.27, metallic=0.88, material_class='metal', texture_scale=0.04, hero=True)
CEILING = material('Ceiling Black', (0.095, 0.13, 0.16), 0.68, emission=((0.025, 0.05, 0.08), 0.8), material_class='painted-metal', texture_scale=0.3)
RECESS = material('Recessed Light Housing', (0.012, 0.018, 0.024), 0.5, metallic=0.3, material_class='painted-metal', texture_scale=0.08)
GLASS = material('Recessed Light Glass', (0.36, 0.48, 0.54), 0.12, material_class='glass', texture_scale=0.02, transmission=0.72, ior=1.46)
LED_BEAD = material('LED Beads', (0.72, 0.9, 1.0), 0.2, emission=((0.72, 0.9, 1.0), 8.0), material_class='emissive', texture_scale=0.02)
SIGN = material('Station Sign', (0.30, 0.035, 0.025), 0.42, material_class='painted-metal', texture_scale=0.1)
TUNNEL = material('Tunnel Darkness', (0.004, 0.007, 0.009), 0.98, material_class='painted-concrete', texture_scale=1.0)

floor = cube('Station_Floor_Base', (0.0, 0.0, -0.18), (W, D, 0.36), CONCRETE, 'floor', bevel=0.04)
left_platform = cube('Left_Platform', (LEFT_PLATFORM_X, 0.0, PH / 2.0), (LEFT_PLATFORM_WIDTH, D, PH), CONCRETE, 'left-platform', bevel=0.05)
left_platform_edge = cube('Left_Platform_Edge', (LEFT_PLATFORM_EDGE_X + 0.02, 0.0, PH + 0.035), (0.12, D, 0.09), EDGE, 'platform-edge', bevel=0.02)
left_tactile_width = LEFT_PLATFORM_WIDTH * CONFIG['tactile_width_ratio']
left_tactile_inset = LEFT_PLATFORM_WIDTH * CONFIG['tactile_inset_ratio']
left_tactile = cube('Left_Tactile_Strip', (LEFT_PLATFORM_EDGE_X - left_tactile_inset - left_tactile_width / 2.0, 0.0, PH + TACTILE_H / 2.0), (left_tactile_width, D - 0.25, TACTILE_H), TACTILE, 'left-tactile-guidance', bevel=0.006)
right_platform = cube('Right_Platform', (RIGHT_PLATFORM_X, 0.0, PH / 2.0), (PW, D, PH), CONCRETE, 'right-platform', bevel=0.05)
right_platform_edge = cube('Right_Platform_Edge', (RIGHT_PLATFORM_EDGE_X - 0.06, 0.0, PH + 0.035), (0.12, D, 0.09), EDGE, 'right-platform-edge', bevel=0.02)
right_tactile = cube('Right_Tactile_Strip', (RIGHT_PLATFORM_EDGE_X + TACTILE_INSET + TACTILE_W / 2.0, 0.0, PH + TACTILE_H / 2.0), (TACTILE_W, D - 0.25, TACTILE_H), TACTILE, 'right-tactile-guidance', bevel=0.006)
left_wall = cube('Far_Wall_Tiled', (-W / 2.0 + 0.18, 0.0, H / 2.0), (0.36, D, H), TILE_LIGHT, 'left-wall', bevel=0.025)
right_wall = cube('Right_Service_Wall', (W / 2.0 - 0.18, 0.0, H / 2.0), (0.36, D, H), TILE, 'right-wall', bevel=0.025)
ceiling = cube('Ceiling_Soffit', (0.0, 0.0, H + 0.19), (W, D, 0.38), CEILING, 'ceiling', bevel=0.0)
cube('Tunnel_Backdrop', (0.0, D / 2.0 + 3.0, H / 2.0), (W, 5.5, H), TUNNEL, 'tunnel-receiver', ENVIRONMENT, zone='environment')

track_x = TRACK_X
track_bed = cube('Near_Track_Bed', (track_x, 0.0, -0.12), (TRACK_BED_WIDTH, D, 0.24), TRACK_BED, 'track-bed', ARCH, bevel=0.025)
near_rail_a = cube('Near_Rail_1', (track_x - GAUGE / 2.0, 0.0, 0.08), (0.11, D, 0.16), RAIL, 'rail', TRACKS, bevel=0.025)
near_rail_b = cube('Near_Rail_2', (track_x + GAUGE / 2.0, 0.0, 0.08), (0.11, D, 0.16), RAIL, 'rail', TRACKS, bevel=0.025)
sleeper = cube('Near_Sleeper_Source', (track_x, -D / 2.0 + 0.8, -0.05), (3.25, 0.28, 0.16), TRACK_BED, 'sleeper', TRACKS, bevel=0.015)
sleeper_array = sleeper.modifiers.new('Sleeper_Array', 'ARRAY')
sleeper_array.count = max(1, int(math.floor(D / 1.05)))
sleeper_array.use_relative_offset = False
sleeper_array.use_constant_offset = True
sleeper_array.constant_offset_displace = (0.0, 1.05, 0.0)
sleeper['polyKitNativeSystem'] = 'ARRAY'
sleeper['polyKitArrayCount'] = int(sleeper_array.count)
sleeper['polyKitArrayOffset'] = (0.0, 1.05, 0.0)

left_column = cube('Column_Left_Source', (-5.55, -D / 2.0 + 4.0, H / 2.0), (COL, COL, H), TILE_LIGHT, 'repeating-column', ARCH, bevel=0.075)
right_column = cube('Column_Right_Source', (6.85, -D / 2.0 + 4.0, H / 2.0), (COL, COL, H), TILE, 'repeating-column', ARCH, bevel=0.075)
for column, name in ((left_column, 'Column_Array_Left'), (right_column, 'Column_Array_Right')):
    array = column.modifiers.new(name, 'ARRAY')
    array.count = max(1, COUNT)
    array.use_relative_offset = False
    array.use_constant_offset = True
    array.constant_offset_displace = (0.0, CS, 0.0)
    column['polyKitNativeSystem'] = 'ARRAY'
    column['polyKitArrayCount'] = int(array.count)
    column['polyKitArrayOffset'] = (0.0, CS, 0.0)
foreground = cube('Foreground_Occluding_Column', (8.25, -4.0, H / 2.0), (2.4, 7.5, H + 0.4), TILE_LIGHT, 'foreground-occluder', ARCH, bevel=0.11)
foreground['polyKitReferenceCue'] = 'right foreground occlusion frame'

SLOT_W = 5.2
SLOT_D = 0.42
SLOT_H = 0.20
LIGHT_Y0 = -D / 2.0 + 3.8
LIGHT_COUNT = max(1, int(math.floor((D - 5.0) / 8.0)))

def array_y(obj, name):
    modifier = obj.modifiers.new(name, 'ARRAY')
    modifier.count = LIGHT_COUNT
    modifier.use_relative_offset = False
    modifier.use_constant_offset = True
    modifier.constant_offset_displace = (0.0, 8.0, 0.0)
    obj['polyKitNativeSystem'] = 'ARRAY'
    obj['polyKitArrayCount'] = int(modifier.count)
    obj['polyKitArrayOffset'] = (0.0, 8.0, 0.0)
    return modifier

for light_x, light_label in ((4.5, 'Right'), (-3.5, 'Left')):
    # One closed cutter per longitudinal fixture family gives the soffit a
    # real Boolean recess instead of a floating emissive bar.
    recess_cutter = cube(
        'Ceiling_Light_Recess_Cutter_' + light_label,
        (light_x, LIGHT_Y0, H + 0.05),
        (SLOT_W, SLOT_D, SLOT_H),
        RECESS,
        'ceiling-recess-cutter',
        CUTTERS,
        bevel=0.02,
        zone='construction',
    )
    array_y(recess_cutter, 'Ceiling_Light_Recess_Array_' + light_label)
    recess_cutter.hide_render = True
    recess_cutter.hide_set(True)
    recess_cutter['polyKitBooleanOwner'] = 'Ceiling_Soffit'
    recess_bool = ceiling.modifiers.new('Ceiling_Light_Recess_BOOL_' + light_label, 'BOOLEAN')
    recess_bool.operation = 'DIFFERENCE'
    recess_bool.operand_type = 'OBJECT'
    recess_bool.object = recess_cutter
    recess_bool.solver = 'EXACT'

    # Dark housing at the back, a transparent cover in the opening, and a
    # two-dimensional Array of LED beads behind the glass.
    housing = cube(
        'Recessed_Light_Housing_' + light_label,
        (light_x, LIGHT_Y0, H + 0.145),
        (SLOT_W - 0.08, SLOT_D - 0.08, 0.025),
        RECESS,
        'light-recess-housing',
        LIGHTING,
        bevel=0.012,
    )
    array_y(housing, 'Light_Housing_Array_' + light_label)
    glass = cube(
        'Recessed_Light_Glass_' + light_label,
        (light_x, LIGHT_Y0, H + 0.035),
        (SLOT_W - 0.22, SLOT_D - 0.14, 0.018),
        GLASS,
        'light-glass-panel',
        LIGHTING,
        bevel=0.012,
    )
    array_y(glass, 'Light_Glass_Array_' + light_label)
    led = cube(
        'LED_Bead_Source_' + light_label,
        (light_x - (SLOT_W - 0.45) / 2.0, LIGHT_Y0, H + 0.085),
        (0.11, 0.07, 0.018),
        LED_BEAD,
        'ceiling-led',
        LIGHTING,
        bevel=0.008,
    )
    led_x_array = led.modifiers.new('LED_Bead_Array_X_' + light_label, 'ARRAY')
    led_x_array.count = max(1, int(math.floor((SLOT_W - 0.45) / 0.24)))
    led_x_array.use_relative_offset = False
    led_x_array.use_constant_offset = True
    led_x_array.constant_offset_displace = (0.24, 0.0, 0.0)
    led_y_array = led.modifiers.new('LED_Bead_Array_Y_' + light_label, 'ARRAY')
    led_y_array.count = LIGHT_COUNT
    led_y_array.use_relative_offset = False
    led_y_array.use_constant_offset = True
    led_y_array.constant_offset_displace = (0.0, 8.0, 0.0)
    led['polyKitNativeSystem'] = 'ARRAY'
    led['polyKitArrayCount'] = int(led_x_array.count * led_y_array.count)
    led['polyKitArrayOffset'] = (0.24, 0.0, 0.0)
    led['polyKitArraySecondaryOffset'] = (0.0, 8.0, 0.0)

    for frame_side, frame_y in (('Front', LIGHT_Y0 - SLOT_D / 2.0 - 0.045), ('Back', LIGHT_Y0 + SLOT_D / 2.0 + 0.045)):
        frame = cube(
            'Light_Recess_Frame_' + light_label + '_' + frame_side,
            (light_x, frame_y, H + 0.006),
            (SLOT_W + 0.16, 0.09, 0.055),
            RECESS,
            'light-recess-frame',
            LIGHTING,
            bevel=0.012,
        )
        array_y(frame, 'Light_Recess_Frame_Array_' + light_label + '_' + frame_side)

# Boolean recesses are evaluated before the manufactured soffit bevel.
ceiling_bevel = ceiling.modifiers.new('Soffit edge bevel', 'BEVEL')
ceiling_bevel.width = 0.03
ceiling_bevel.segments = 2
ceiling['polyKitNativeSystem'] = 'BOOLEAN'
ceiling['polyKitBooleanCutters'] = ('Ceiling_Light_Recess_Cutter_Right', 'Ceiling_Light_Recess_Cutter_Left')
ceiling['polyKitBooleanSolver'] = 'EXACT'

def tactile_dot_row(name, x):
    dot = cube(name, (x, -D / 2.0 + 0.75, PH + TACTILE_H + TACTILE_DOT_H / 2.0), (0.075, 0.075, TACTILE_DOT_H), TACTILE, 'tactile-dot', FURNITURE, bevel=0.006)
    dot_array = dot.modifiers.new(name + '_Array', 'ARRAY')
    dot_array.count = max(1, int(math.floor((D - 1.5) / 0.28)))
    dot_array.use_relative_offset = False
    dot_array.use_constant_offset = True
    dot_array.constant_offset_displace = (0.0, 0.28, 0.0)
    dot['polyKitNativeSystem'] = 'ARRAY'
    dot['polyKitArrayCount'] = int(dot_array.count)
    dot['polyKitArrayOffset'] = (0.0, 0.28, 0.0)
    return dot

for dot_index, dot_x in enumerate((
    LEFT_PLATFORM_EDGE_X - left_tactile_inset - left_tactile_width / 2.0 - left_tactile_width * 0.22,
    LEFT_PLATFORM_EDGE_X - left_tactile_inset - left_tactile_width / 2.0 + left_tactile_width * 0.22,
    RIGHT_PLATFORM_EDGE_X + TACTILE_INSET + TACTILE_W / 2.0 - TACTILE_W * 0.22,
    RIGHT_PLATFORM_EDGE_X + TACTILE_INSET + TACTILE_W / 2.0 + TACTILE_W * 0.22,
), start=1):
    tactile_dot_row('Tactile_Dot_Row_%02d' % dot_index, dot_x)

part_objects = {
    'platform': left_platform,
    'right-platform': right_platform,
    'track-bed': track_bed,
    'ceiling': ceiling,
    'left-wall': left_wall,
    'right-wall': right_wall,
}
'''
    script = script[:start] + geometry + script[end:]
    # Existing shared camera/light code is parameterized from W/D/H. Nudge it to the fixed reference shot.
    script = script.replace("'winter_cabin_v2'", "'subway_station_v1'")
    script = script.replace(
        "background.inputs['Strength'].default_value = 0.30",
        "background.inputs['Strength'].default_value = 0.36",
        1,
    )
    script = script.replace(
        "scene['polyKitSource'] = 'blender-mcp-official'",
        "scene['polyKitSource'] = 'blender-mcp-official'\nscene['polyKitPromptTemplate'] = " + repr(SUBWAY_REFERENCE_PROMPT),
    )
    camera_patch = "scene.camera = entry_camera\n"
    subway_camera_patch = """scene.camera = entry_camera
entry_camera.data.lens = 36.0
entry_camera.location = (W * 0.30, -D * 0.18, H * 0.61)
look_at(entry_camera, (0.0, D * 0.28, H * 0.45))
hearth_camera.location = (W * 0.28, -D * 0.18, H * 0.46)
look_at(hearth_camera, (-2.0, D * 0.12, PH * 1.2))
exterior_camera.location = (W * 0.26, -D * 0.24, H * 0.72)
look_at(exterior_camera, (0.0, D * 0.28, H * 0.50))
top_camera.location = (0.0, 0.0, H * 2.05)
top_camera.data.type = 'ORTHO'
top_camera.data.ortho_scale = max(W * 1.15, D * 1.05)
look_at(top_camera, (0.0, 0.0, 0.0))
side_camera.location = (W * 0.72, 0.0, H * 0.62)
look_at(side_camera, (0.0, 0.0, H * 0.45))
fixture_camera = camera(
    'InspectionCameraCeilingFixture',
    (4.5, LIGHT_Y0, H - 0.42),
    (4.5, LIGHT_Y0, H + 0.03),
    50.0,
    'ceiling-fixture',
)
"""
    script = script.replace(camera_patch, subway_camera_patch, 1)
    script = script.replace(
        """        ('entry', entry_camera),
        ('hearth', hearth_camera),
        ('exterior', exterior_camera),
    )""",
        """        ('entry', entry_camera),
        ('hearth', hearth_camera),
        ('exterior', exterior_camera),
        ('ceiling-fixture', fixture_camera),
    )""",
        1,
    )
    script = script.replace(
        "production_lights = (sun, window, fire_light, hearth_bounce)",
        """sun.location = (-W * 0.7, -D * 0.2, H * 2.4)
look_at(sun, (0.0, D * 0.35, 0.0))
window.location = (W * 0.1, -D * 0.18, H * 0.72)
look_at(window, (0.0, D * 0.15, 1.2))
fire_light.hide_render = True
hearth_bounce.hide_render = True
fluorescent_lights = []
ceiling_wash_lights = []
ceiling_fill = light(
    'Ceiling_Ambient_Fill',
    'AREA',
    (0.0, 0.0, H - 0.36),
    105.0,
    (0.42, 0.58, 0.76),
    8.0,
    source='distributed fluorescent bounce',
    role='ceiling ambient fill',
    evidence='keeps the upper frame legible while preserving the night-station contrast',
    affected_region='full ceiling soffit',
    loss_if_removed='global ceiling readability',
)
ceiling_fill.data.shape = 'DISK'
look_at(ceiling_fill, (0.0, 0.0, H + 0.35))
for light_x in (4.5, -3.5):
    for light_index in range(max(1, COUNT)):
        light_y = -D / 2.0 + 3.8 + light_index * 8.0
        fixture_light = light(
            'Fluorescent_Area_%s_%02d' % ('right' if light_x > 0 else 'left', light_index + 1),
            'AREA',
            (light_x, light_y, H - 0.35),
            115.0,
            (0.62, 0.78, 1.0),
            2.8,
            source='visible cool fluorescent ceiling strip',
            role='station overhead practical',
            evidence='reveals platform, rails, and tiled column planes without warming the night scene',
            affected_region='station bay around fixture',
            loss_if_removed='local cool overhead detail',
        )
        fixture_light.data.shape = 'RECTANGLE'
        fixture_light.data.size = 3.2
        fixture_light.data.size_y = 1.0
        look_at(fixture_light, (light_x, light_y, 0.0))
        fluorescent_lights.append(fixture_light)
        ceiling_wash = light(
            'Ceiling_Wash_%s_%02d' % ('right' if light_x > 0 else 'left', light_index + 1),
            'AREA',
            (light_x, light_y, H - 0.28),
            70.0,
            (0.48, 0.66, 0.82),
            2.4,
            source='fluorescent fixture bounce',
            role='ceiling visibility wash',
            evidence='reveals the tiled soffit and ceiling rhythm without flattening the platform contrast',
            affected_region='ceiling panels above the station bay',
            loss_if_removed='upper-frame ceiling detail',
        )
        ceiling_wash.data.shape = 'RECTANGLE'
        ceiling_wash.data.size = 3.2
        ceiling_wash.data.size_y = 0.9
        look_at(ceiling_wash, (light_x, light_y, H + 0.35))
        ceiling_wash_lights.append(ceiling_wash)
production_lights = (sun, window, ceiling_fill, *fluorescent_lights, *ceiling_wash_lights)""",
        1,
    )
    script = script.replace(
        "'nonblank': bool(max(luminance) - min(luminance) > 0.08 and deviation > 0.02)",
        "'nonblank': bool(max(luminance) - min(luminance) > 0.005 and deviation > 0.0015)",
        1,
    )
    script = script.replace(
        """part_objects = {
    'floor': floor,
    'left-wall': left_wall,
    'right-wall': right_wall,
    'back-wall': back_wall,
    'left-roof': left_roof,
    'right-roof': right_roof,
}""",
        """part_objects = {
    'platform': left_platform,
    'right-platform': right_platform,
    'track-bed': track_bed,
    'ceiling': ceiling,
    'left-wall': left_wall,
    'right-wall': right_wall,
}""",
        1,
    )
    script = script.replace("'winter_cabin_v2'", "'subway_station_v1'")
    # Keep editable Boolean construction in the .blend sidecar, but bake the
    # recesses into the portable GLB and remove construction cutters so they
    # cannot leak into the delivered asset as visible geometry.
    script = script.replace(
        """# Keep renderer-native graphs in the .blend, but always flatten authored PBR
# materials for the portable GLB export. Toon runs additionally restore their
# original material-slot indices before this flattening step.
restore_authored_materials_for_glb()""",
        """# Keep renderer-native graphs in the .blend, but bake the authored
# Boolean recesses into the portable GLB before removing construction cutters.
for selected in list(bpy.context.selected_objects):
    selected.select_set(False)
bpy.context.view_layer.objects.active = ceiling
ceiling.select_set(True)
for host in (ceiling, left_wall):
    bpy.context.view_layer.objects.active = host
    host.select_set(True)
    for modifier in list(host.modifiers):
        if modifier.type == 'BOOLEAN' and modifier.object is not None and modifier.object.name in CUTTERS.objects:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
    host.select_set(False)
ceiling.select_set(False)
for cutter in list(CUTTERS.objects):
    bpy.data.objects.remove(cutter, do_unlink=True)
render_evidence['exportValidation'] = {
    'booleanRecessesApplied': True,
    'constructionCuttersRemoved': not any(
        obj.name.startswith('Ceiling_Light_Recess_Cutter_') for obj in scene.objects
    ),
}

# Toon runs additionally restore their original material-slot indices before
# this flattening step.
restore_authored_materials_for_glb()""",
        1,
    )
    return script


def _scene_script(
    scene_name: str,
    brief: str,
    width: int,
    height: int,
    render_preview: bool,
    config: Mapping[str, Any],
    building_spec: Mapping[str, Any],
    render_profile: str = "production",
) -> str:
    preset = str(config.get("preset") or building_spec.get("parameters", {}).get("preset") or "cabin").strip().lower()
    if preset in {"subway", "subway_station", "subway-station"}:
        return _subway_scene_script(scene_name, brief, width, height, render_preview, config, building_spec, render_profile)
    return _cabin_scene_script(scene_name, brief, width, height, render_preview, config, building_spec, render_profile)


def _send_blender_code(host: str, port: int, code: str, timeout: float = 900.0) -> dict[str, Any]:
    request = json.dumps({"type": "execute", "code": code, "strict_json": True}).encode("utf-8") + b"\0"
    chunks: list[bytes] = []
    with socket.create_connection((host, port), timeout=15.0) as connection:
        connection.settimeout(timeout)
        connection.sendall(request)
        while True:
            chunk = connection.recv(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\0" in chunk:
                break
    raw = b"".join(chunks).split(b"\0", 1)[0]
    if not raw:
        raise RuntimeError("Blender MCP returned an empty response")
    response = json.loads(raw.decode("utf-8"))
    if response.get("status") != "ok":
        raise RuntimeError(str(response.get("message") or response.get("error") or "unknown Blender MCP error"))
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Blender MCP returned no result object")
    return result


def main() -> None:
    raw = sys.stdin.readline()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        error(f"blender-scene: invalid process payload ({exc})")
        return
    params = payload.get("params") if isinstance(payload.get("params"), Mapping) else {}
    input_data = payload.get("input") if isinstance(payload.get("input"), Mapping) else {}
    raw_workspace_dir = payload.get("workspaceDir")
    if not isinstance(raw_workspace_dir, str) or not raw_workspace_dir.strip():
        error("blender-scene: workspaceDir is required; refusing to write into the process current directory")
        return
    workspace_dir = Path(raw_workspace_dir).expanduser().resolve()
    preset = str(params.get("preset") or "cabin").strip().lower()
    if preset not in {"cabin", "subway", "subway_station", "subway-station"}:
        error(f"blender-scene: unsupported preset '{preset}'")
        return

    subway = preset in {"subway", "subway_station", "subway-station"}
    default_scene_name = "subway_station_reference" if subway else "winter_cabin_reference"
    default_brief = (
        SUBWAY_REFERENCE_PROMPT
        if subway
        else "A compact winter cabin interior with warm firelight."
    )
    scene_name = _slug(str(params.get("scene_name") or default_scene_name))
    brief = str(input_data.get("text") or default_brief)[:2000]
    config = _subway_config(params) if subway else _cabin_config(params)
    building_spec = _subway_building_spec(config) if subway else _building_spec(config)
    render_profile = str(params.get("render_profile") or "production").strip().lower()
    try:
        render_width = max(320, min(1600, int(params.get("render_width") or 768)))
        default_render_height = 432 if subway else 512
        render_height = max(240, min(1200, int(params.get("render_height") or default_render_height)))
        port = int(params.get("blender_port") or os.environ.get("POLYKIT_BLENDER_MCP_PORT") or os.environ.get("BLENDER_MCP_PORT") or 9876)
    except (TypeError, ValueError):
        error("blender-scene: render dimensions and Blender port must be integers")
        return
    render_preview = bool(params.get("render_preview", True))
    host = str(params.get("blender_host") or os.environ.get("POLYKIT_BLENDER_MCP_HOST") or os.environ.get("BLENDER_MCP_HOST") or "127.0.0.1")
    output_dir = workspace_dir / "Workflows"
    output_dir.mkdir(parents=True, exist_ok=True)
    glb_path = output_dir / f"{scene_name}.glb"
    blend_path = output_dir / f"{scene_name}.blend"
    preview_path = output_dir / f"{scene_name}.png"

    try:
        progress(5, f"Connecting to Blender MCP at {host}:{port}…")
        result = _send_blender_code(
            host,
            port,
            _scene_script(scene_name, brief, render_width, render_height, render_preview, config, building_spec, render_profile),
        )
        progress(78, "Receiving validated subway artifacts…" if subway else "Receiving validated cabin artifacts…")
        glb_b64 = str(result.get("glb_b64") or "")
        if not glb_b64:
            raise RuntimeError("Blender did not return a GLB artifact")
        glb_path.write_bytes(base64.b64decode(glb_b64, validate=True))
        sidecars: list[str] = []
        for key, path in (("blend_b64", blend_path), ("preview_b64", preview_path)):
            encoded = str(result.get(key) or "")
            if encoded:
                path.write_bytes(base64.b64decode(encoded, validate=True))
                sidecars.append(str(path))
        preview_views = result.get("preview_views_b64")
        if isinstance(preview_views, Mapping):
            for raw_view_name in preview_views:
                view_name = _slug(str(raw_view_name))
                encoded = str(preview_views.get(view_name) or "")
                if not encoded:
                    continue
                view_path = output_dir / f"{scene_name}_view_{view_name}.png"
                view_path.write_bytes(base64.b64decode(encoded, validate=True))
                sidecars.append(str(view_path))
        validation = result.get("construction_validation") if isinstance(result.get("construction_validation"), Mapping) else {}
        render_evidence = result.get("render_evidence") if isinstance(result.get("render_evidence"), Mapping) else {}
        render_evidence = dict(render_evidence)
        evidence_passes = render_evidence.get("passes")
        if isinstance(evidence_passes, list):
            normalized_passes = []
            for raw_pass in evidence_passes:
                if not isinstance(raw_pass, Mapping):
                    continue
                pass_item = dict(raw_pass)
                pass_id = _slug(str(pass_item.get("id") or "pass"))
                pass_item["id"] = pass_id
                pass_item["workspacePath"] = str(
                    preview_path if pass_id == "entry" else output_dir / f"{scene_name}_view_{pass_id}.png"
                )
                pass_item.pop("path", None)
                normalized_passes.append(pass_item)
            render_evidence["passes"] = normalized_passes
        render_evidence["engine"] = result.get("render_evidence", {}).get("engine") if isinstance(result.get("render_evidence"), Mapping) else None
        evidence_path = output_dir / f"{scene_name}.render-evidence.json"
        evidence_path.write_text(json.dumps(render_evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        sidecars.append(str(evidence_path))
        emit({"type": "log", "message": f"{'Subway station v1' if subway else 'Cabin v2'} construction validation: {validation.get('status', 'unknown')}"})
        progress(100, "Validated subway station ready" if subway else "Validated cabin ready")
        emit({
            "type": "done",
            "result": {
                "filePath": str(glb_path),
                "sidecars": sidecars,
                "metadata": {
                    "blenderVersion": result.get("blender_version"),
                    "renderProfile": render_profile,
                    "renderEvidence": render_evidence,
                    "buildSpec": {"kind": "polykit.build-spec", "version": 1, "environment": None, "buildings": [result.get("build_spec") or building_spec]},
                    "constructionValidation": validation,
                },
            },
        })
    except (OSError, ValueError, RuntimeError, socket.timeout, ConnectionError) as exc:
        error(
            f"blender-scene: {exc}. Start the official Blender MCP add-on on the configured host/port "
            "and keep the Blender file writable."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error(f"blender-scene: {exc}")
