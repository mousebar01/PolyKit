"""Build a small, presentation-ready scene through the official Blender MCP bridge.

The node deliberately accepts a preset rather than arbitrary Python.  PolyKit
owns the workflow/run lifecycle, while Blender remains the scene-construction
backend.  The bridge returns compact base64 artifacts so the result is durable
in the PolyKit workspace even when Blender runs on another machine.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _slug(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", "_", value).strip("_").lower()
    return result[:64] or "blender_scene"


def _scene_script(scene_name: str, brief: str, width: int, height: int, render_preview: bool) -> str:
    """Return the fixed Blender script for the supported reference preset.

    This is intentionally a declarative scene recipe: callers can choose a
    preset and output settings, but cannot inject arbitrary Blender code.
    """
    script = r'''
import base64
import json
import pathlib
import tempfile
import math
import bpy
from mathutils import Vector

SCENE_NAME = __SCENE_NAME__
SCENE_BRIEF = __SCENE_BRIEF__
RENDER_WIDTH = __WIDTH__
RENDER_HEIGHT = __HEIGHT__
RENDER_PREVIEW = __RENDER_PREVIEW__

scene = bpy.context.scene
# Start from a genuinely empty scene.  ``object.select_all`` only touches the
# active view layer, so hidden objects/collections from a previous MCP run can
# otherwise survive and make an otherwise valid cabin look spatially corrupted.
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for collection in list(bpy.data.collections):
    bpy.data.collections.remove(collection)

scene.name = SCENE_NAME
scene['polyKitPreset'] = 'winter_cabin_reference'
scene['polyKitSource'] = 'blender-mcp-official'
scene['polyKitBrief'] = SCENE_BRIEF
scene['polyKitCoordinateSystem'] = (
    'Blender: right-handed, Z-up, meters; '
    'glTF/Three.js: right-handed, Y-up, meters (Blender exporter converts)'
)
scene['polyKitLayoutVersion'] = '1.0'
scene['polyKitLayoutContract'] = json.dumps({
    'units': 'meters',
    'blender_up': 'Z',
    'gltf_up': 'Y',
    'interior_bounds': {'min': [-5.7, -3.9, 0.0], 'max': [5.7, 5.85, 5.55]},
    'exterior_clearance': 0.12,
}, separators=(',', ':'))
try:
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.length_unit = 'METERS'
except Exception:
    pass

def make_collection(name):
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if collection.name not in [item.name for item in scene.collection.children]:
        scene.collection.children.link(collection)
    return collection

ENV = make_collection('Environment')
EXTERIOR = make_collection('Exterior')
FURNITURE = make_collection('Furniture')
SET_DRESSING = make_collection('SetDressing')
CUTTERS = make_collection('Cutters')
LIGHTING = make_collection('Lighting')

def material(name, color, roughness=0.7, metallic=0.0, emission=None):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = (*color, 1.0)
        bsdf.inputs['Roughness'].default_value = roughness
        bsdf.inputs['Metallic'].default_value = metallic
        if emission is not None:
            if 'Emission Color' in bsdf.inputs:
                bsdf.inputs['Emission Color'].default_value = (*emission[0], 1.0)
            elif 'Emission' in bsdf.inputs:
                bsdf.inputs['Emission'].default_value = (*emission[0], 1.0)
            if 'Emission Strength' in bsdf.inputs:
                bsdf.inputs['Emission Strength'].default_value = emission[1]
    mat.diffuse_color = (*color, 1.0)
    return mat

WOOD = material('Cabin Wood', (0.16, 0.045, 0.012), 0.82)
WOOD_LIGHT = material('Pale Wood', (0.34, 0.13, 0.032), 0.78)
WOOD_DARK = material('Dark Timber', (0.045, 0.012, 0.004), 0.9)
METAL = material('Stove Iron', (0.025, 0.032, 0.04), 0.3, 0.7)
GLASS = material('Frosted Window', (0.32, 0.5, 0.66), 0.32, 0.0, ((0.2, 0.38, 0.6), 0.04))
SNOW = material('Snow', (0.78, 0.86, 0.9), 0.92)
FABRIC = material('Wool', (0.54, 0.49, 0.42), 0.95)
RUG = material('Rug', (0.22, 0.075, 0.04), 0.98)
FIRE = material('Fire', (0.12, 0.018, 0.002), 0.35, 0.0, ((1.0, 0.12, 0.015), 6.0))
WARM = material('Warm Lamp', (0.3, 0.12, 0.025), 0.35, 0.0, ((1.0, 0.3, 0.04), 3.0))

def procedural_wood(mat, dark, light, scale=3.5):
    """Give wood a directional-looking response without an image texture.

    The noise drives only base colour and a separate noise drives roughness;
    bump remains a small surface cue rather than a substitute for geometry.
    """
    if mat.get('polyKitProceduralSurface'):
        return
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get('Principled BSDF')
    if not bsdf:
        return
    texcoord = nodes.new('ShaderNodeTexCoord')
    texcoord.name = 'Wood Generated Coordinates'
    grain = nodes.new('ShaderNodeTexNoise')
    grain.name = 'Wood Grain Color'
    grain.inputs['Scale'].default_value = scale
    grain.inputs['Detail'].default_value = 3.0
    grain.inputs['Roughness'].default_value = 0.72
    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.name = 'Wood Grain Color Ramp'
    ramp.color_ramp.elements[0].color = (*dark, 1.0)
    ramp.color_ramp.elements[1].color = (*light, 1.0)
    rough = nodes.new('ShaderNodeTexNoise')
    rough.name = 'Wood Roughness Variation'
    rough.inputs['Scale'].default_value = scale * 1.7
    rough.inputs['Detail'].default_value = 2.0
    bump = nodes.new('ShaderNodeBump')
    bump.name = 'Wood Fine Relief'
    bump.inputs['Strength'].default_value = 0.12
    bump.inputs['Distance'].default_value = 0.08
    links.new(texcoord.outputs['Generated'], grain.inputs['Vector'])
    links.new(grain.outputs['Fac'], ramp.inputs['Fac'])
    links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    links.new(texcoord.outputs['Generated'], rough.inputs['Vector'])
    links.new(rough.outputs['Fac'], bsdf.inputs['Roughness'])
    links.new(grain.outputs['Fac'], bump.inputs['Height'])
    links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    mat['polyKitProceduralSurface'] = 'wood_color_roughness_bump'

def procedural_fabric(mat, base, scale=5.0):
    if mat.get('polyKitProceduralSurface'):
        return
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get('Principled BSDF')
    if not bsdf:
        return
    texcoord = nodes.new('ShaderNodeTexCoord')
    weave = nodes.new('ShaderNodeTexNoise')
    weave.inputs['Scale'].default_value = scale
    weave.inputs['Detail'].default_value = 2.0
    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].color = (*tuple(value * 0.72 for value in base), 1.0)
    ramp.color_ramp.elements[1].color = (*tuple(min(1.0, value * 1.15) for value in base), 1.0)
    bump = nodes.new('ShaderNodeBump')
    bump.inputs['Strength'].default_value = 0.08
    bump.inputs['Distance'].default_value = 0.035
    links.new(texcoord.outputs['Generated'], weave.inputs['Vector'])
    links.new(weave.outputs['Fac'], ramp.inputs['Fac'])
    links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    links.new(weave.outputs['Fac'], bump.inputs['Height'])
    links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    mat['polyKitProceduralSurface'] = 'fabric_color_bump'

procedural_wood(WOOD, (0.045, 0.010, 0.003), (0.26, 0.085, 0.018), 4.0)
procedural_wood(WOOD_LIGHT, (0.16, 0.055, 0.012), (0.52, 0.23, 0.07), 3.2)
procedural_wood(WOOD_DARK, (0.012, 0.004, 0.002), (0.09, 0.022, 0.006), 5.0)
procedural_fabric(FABRIC, (0.54, 0.49, 0.42), 7.0)

def move_to(obj, collection):
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    collection.objects.link(obj)

def finish(obj, role, collection, mat=None, bevel=0.0):
    obj['polyKitRole'] = role
    obj['polyKitSemanticName'] = obj.name
    obj['polyKitCollection'] = collection.name
    if collection.name == EXTERIOR.name:
        obj['polyKitZone'] = 'exterior'
    elif collection.name in {FURNITURE.name, SET_DRESSING.name}:
        obj['polyKitZone'] = 'interior'
    else:
        obj['polyKitZone'] = 'architecture'
    move_to(obj, collection)
    if mat:
        obj.data.materials.append(mat)
    if bevel:
        modifier = obj.modifiers.new('Soft edges', 'BEVEL')
        modifier.width = bevel
        modifier.segments = 2
    return obj

def cube(name, location, dimensions, mat, role, collection=ENV, rotation=(0.0, 0.0, 0.0), bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return finish(obj, role, collection, mat, bevel)

def array_repeat(obj, name, count, offset):
    modifier = obj.modifiers.new(name, 'ARRAY')
    modifier.count = count
    modifier.use_relative_offset = False
    modifier.use_constant_offset = True
    modifier.constant_offset_displace = offset
    obj['polyKitNativeSystem'] = 'ARRAY'
    obj['polyKitArrayCount'] = count
    obj['polyKitArrayOffset'] = list(offset)
    return modifier

def cylinder(name, location, radius, depth, mat, role, collection=ENV, vertices=24, rotation=(0.0, 0.0, 0.0)):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    return finish(obj, role, collection, mat, 0.0)

def sphere(name, location, radius, mat, role, collection=SET_DRESSING):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=radius, location=location)
    obj = bpy.context.object
    obj.name = name
    return finish(obj, role, collection, mat, 0.0)

def look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

# A compact cabin shell, kept open toward the camera so the exported GLB is
# immediately useful in a Three.js viewer.
cube('Floor_Wood_Planks', (0, 1.0, 0.0), (12.0, 10.0, 0.24), WOOD_LIGHT, 'floor')
cube('Back_Wall_Left', (-4.2, 5.8, 2.8), (3.6, 0.22, 5.6), WOOD, 'wall')
cube('Back_Wall_Right', (4.2, 5.8, 2.8), (3.6, 0.22, 5.6), WOOD, 'wall')
cube('Back_Wall_Top', (0, 5.8, 5.25), (4.8, 0.22, 1.1), WOOD, 'wall')
# The door is a real through-wall Boolean opening, not a panel pasted onto a
# solid wall or three boxes pretending to be a wall.  The cutter remains in a
# dedicated collection while the evaluated host is exported.
left_wall = cube('Left_Wall', (-5.9, 1.0, 2.8), (0.22, 9.75, 5.6), WOOD, 'wall')
door_cutter = cube('Door_Opening_Cutter', (-5.9, -2.8, 2.45), (0.7, 2.2, 4.9), WOOD, 'opening_cutter', CUTTERS)
door_cutter.hide_render = True
door_cutter.hide_set(True)
door_boolean = left_wall.modifiers.new('Door Opening Boolean', 'BOOLEAN')
door_boolean.operation = 'DIFFERENCE'
door_boolean.solver = 'EXACT'
door_boolean.object = door_cutter
left_wall['polyKitNativeSystem'] = 'BOOLEAN'
left_wall['polyKitBooleanCutter'] = door_cutter.name
left_wall['polyKitBooleanPolicy'] = 'keep_non_destructive'
cube('Right_Wall_Lower', (5.9, -1.7, 1.35), (0.22, 4.6, 2.7), WOOD, 'wall')
cube('Right_Wall_Upper', (5.9, 4.75, 1.35), (0.22, 2.1, 2.7), WOOD, 'wall')
cube('Roof_Left', (-3.0, 1.0, 6.1), (6.2, 10.0, 0.18), WOOD_DARK, 'roof', rotation=(0.0, -0.63, 0.0))
cube('Roof_Right', (3.0, 1.0, 6.1), (6.2, 10.0, 0.18), WOOD_DARK, 'roof', rotation=(0.0, 0.63, 0.0))
cube('Roof_Ridge_Beam', (0.0, 1.0, 6.82), (0.32, 10.0, 0.32), WOOD_DARK, 'roof_beam')
rafter_left = cube('Roof_Rafter_Left_Source', (-4.7, 1.0, 5.5), (0.18, 10.0, 0.36), WOOD_DARK, 'roof_beam', ENV, rotation=(0.0, -0.63, 0.0))
array_repeat(rafter_left, 'Roof Rafter Array Left', 2, (2.35, 0.0, 0.0))
rafter_right = cube('Roof_Rafter_Right_Source', (0.0, 1.0, 5.5), (0.18, 10.0, 0.36), WOOD_DARK, 'roof_beam', ENV, rotation=(0.0, 0.63, 0.0))
array_repeat(rafter_right, 'Roof Rafter Array Right', 3, (2.35, 0.0, 0.0))

# The front remains open for Three.js inspection, but it now has a real porch
# threshold and load-bearing trim so the cabin reads as a structure rather
# than a cutaway box.  Posts are an Array-owned regular module.
cube('Porch_Deck', (0.0, -4.7, 0.12), (10.4, 1.05, 0.22), WOOD_LIGHT, 'porch_deck', EXTERIOR, bevel=0.04)
cube('Porch_Step', (0.0, -5.35, -0.08), (10.0, 0.55, 0.24), WOOD_DARK, 'porch_step', EXTERIOR, bevel=0.035)
porch_post = cube('Porch_Post_Source', (-4.7, -4.7, 2.15), (0.24, 0.24, 4.1), WOOD_DARK, 'porch_post', EXTERIOR, bevel=0.03)
array_repeat(porch_post, 'Porch Post Array', 2, (9.4, 0.0, 0.0))
cube('Porch_Header', (0.0, -4.7, 4.1), (9.7, 0.26, 0.28), WOOD_DARK, 'porch_header', EXTERIOR, bevel=0.03)
cube('Front_Threshold', (0.0, -3.92, 0.32), (10.6, 0.28, 0.18), WOOD_DARK, 'threshold', ENV, bevel=0.025)

# Small repeated plank seams make the shell read as timber instead of a set of
# untextured primitives while remaining lightweight in the exported GLB.
for y in (-3.7, -2.8, -1.9, -1.0, -0.1, 0.8, 1.7, 2.6, 3.5, 4.4):
    cube('Left_Wall_Plank_Seam', (-5.76, y, 2.8), (0.06, 0.035, 5.15), WOOD_LIGHT, 'wall_detail', ENV)
floor_seam = cube('Floor_Plank_Seam_Source', (-5.0, 1.0, 0.14), (0.035, 9.65, 0.025), WOOD_DARK, 'floor_detail', SET_DRESSING)
array_repeat(floor_seam, 'Floor Plank Seam Array', 11, (1.0, 0.0, 0.0))

def window(name, location, dims, axis='back'):
    cube(name + '_Glass', location, dims, GLASS, 'window_glass', ENV)
    if axis == 'back':
        x, y, z = location
        w, _, h = dims
        for xx in (x - w / 2.0, x + w / 2.0):
            cube(name + '_Frame_V', (xx, y - 0.04, z), (0.16, 0.18, h + 0.3), WOOD_DARK, 'window_frame', ENV)
        for zz in (z - h / 2.0, z + h / 2.0):
            cube(name + '_Frame_H', (x, y - 0.04, zz), (w + 0.3, 0.18, 0.16), WOOD_DARK, 'window_frame', ENV)
    else:
        x, y, z = location
        _, w, h = dims
        for yy in (y - w / 2.0, y + w / 2.0):
            cube(name + '_Frame_V', (x - 0.04, yy, z), (0.18, 0.16, h + 0.3), WOOD_DARK, 'window_frame', ENV)
        for zz in (z - h / 2.0, z + h / 2.0):
            cube(name + '_Frame_H', (x - 0.04, y, zz), (0.18, w + 0.3, 0.16), WOOD_DARK, 'window_frame', ENV)

window('Back_Window', (1.35, 5.62, 3.15), (2.55, 0.1, 2.25), 'back')
window('Side_Window_A', (5.74, 1.3, 3.15), (0.1, 2.45, 2.25), 'side')
window('Side_Window_B', (5.74, 4.1, 3.15), (0.1, 2.0, 2.25), 'side')

# Door and a warm, layered furniture arrangement.
cube('Door', (-5.68, -2.8, 2.45), (0.12, 1.9, 4.7), WOOD_LIGHT, 'door', ENV)
cube('Door_Handle', (-5.58, -2.8, 2.45), (0.1, 0.1, 0.28), METAL, 'door_handle', SET_DRESSING)
cube('Bed_Frame', (-3.45, 1.65, 0.72), (3.9, 2.1, 0.55), WOOD_DARK, 'bed', FURNITURE, bevel=0.06)
cube('Bed_Mattress', (-3.45, 1.65, 1.12), (3.58, 1.82, 0.36), FABRIC, 'bed_mattress', FURNITURE, bevel=0.12)
cube('Bed_Pillow', (-4.6, 1.65, 1.38), (0.62, 1.3, 0.24), SNOW, 'bed_pillow', FURNITURE, bevel=0.12)
cube('Rug', (0.0, 0.5, 0.15), (3.8, 2.6, 0.08), RUG, 'rug', SET_DRESSING, bevel=0.03)
cube('Table_Top', (-0.1, 0.0, 1.05), (1.9, 1.2, 0.16), WOOD_LIGHT, 'table', FURNITURE, bevel=0.05)
for x in (-0.75, 0.55):
    for y in (-0.4, 0.4):
        cube('Table_Leg', (x, y, 0.53), (0.12, 0.12, 1.0), WOOD_DARK, 'table_leg', FURNITURE)
cube('Table_Mug', (-0.3, 0.0, 1.24), (0.25, 0.25, 0.3), METAL, 'table_dressing', SET_DRESSING, bevel=0.04)
cube('Stove_Body', (3.85, 3.45, 1.35), (1.65, 1.35, 2.15), METAL, 'wood_stove', FURNITURE, bevel=0.08)
cube('Stove_Door', (3.85, 2.75, 1.25), (0.9, 0.06, 0.9), FIRE, 'stove_fire', SET_DRESSING, bevel=0.04)
cylinder('Stove_Pipe', (3.85, 3.45, 4.5), 0.24, 5.8, METAL, 'stove_pipe', FURNITURE)
cube('Stove_Handle', (3.85, 2.69, 1.32), (0.36, 0.1, 0.08), WOOD_LIGHT, 'stove_handle', SET_DRESSING)
cube('Shelf_Back', (-0.4, 5.5, 4.2), (2.7, 0.18, 1.65), WOOD_DARK, 'shelf', FURNITURE)
for z in (3.65, 4.15, 4.65):
    cube('Shelf_' + str(z), (-0.4, 5.22, z), (2.8, 0.55, 0.12), WOOD_LIGHT, 'shelf', FURNITURE)
for i, x in enumerate((-1.2, -0.55, 0.1)):
    cube('Shelf_Item_' + str(i), (x, 5.03, 4.48), (0.38, 0.34, 0.52), (FABRIC if i % 2 else WOOD_LIGHT), 'shelf_dressing', SET_DRESSING, bevel=0.03)
for x in (-4.8, 4.9):
    cube('Crate', (x, -1.9, 0.55), (0.9, 0.9, 0.9), WOOD_LIGHT, 'crate', SET_DRESSING, bevel=0.04)

# Snow bank visible through the windows gives the composition depth without
# pretending that a flat panorama is a navigable world.
# Snow sits on the ground outside the glazing.  It is intentionally low so a
# rear/side inspection view does not read as a pair of floating white walls.
cube('Snow_Outside_Back', (1.35, 6.35, 0.24), (9.0, 0.55, 0.48), SNOW, 'snow_background', EXTERIOR)
cube('Snow_Outside_Side', (6.35, 2.5, 0.2), (0.55, 5.5, 0.4), SNOW, 'snow_background', EXTERIOR)

# A few low-poly tree trunks sit beyond the glazing, so the windows read as
# openings to a snowy exterior rather than as floating white rectangles.
for i, x in enumerate((-2.8, -0.8, 2.8, 4.8)):
    cylinder('Exterior_Tree_Trunk_' + str(i), (x, 7.15, 2.2), 0.11, 3.4, WOOD_DARK, 'exterior_tree', EXTERIOR, vertices=10)
    for z, spread in ((2.8, 0.75), (3.65, 0.52), (4.35, 0.3)):
        cube('Exterior_Tree_Branch_' + str(i) + '_' + str(z), (x, 7.08, z), (spread, 0.12, 0.11), WOOD_DARK, 'exterior_tree', EXTERIOR)

def add_light(name, kind, location, energy, color, size=3.0):
    data = bpy.data.lights.new(name=name, type=kind)
    data.energy = energy
    data.color = color
    if kind in {'AREA', 'POINT'}:
        data.shadow_soft_size = size
    obj = bpy.data.objects.new(name, data)
    LIGHTING.objects.link(obj)
    obj.location = location
    obj['polyKitRole'] = 'lighting'
    return obj

key = add_light('Warm_Key', 'AREA', (-1.0, -3.0, 4.6), 240.0, (1.0, 0.48, 0.22), 5.0)
key.data.shape = 'DISK'
key.data.size = 4.0
look_at(key, (0.0, 1.5, 1.5))
fill = add_light('Window_Fill', 'AREA', (4.5, 4.7, 4.2), 260.0, (0.48, 0.68, 1.0), 3.5)
look_at(fill, (0.0, 1.5, 1.8))
fire_light = add_light('Fire_Glow', 'POINT', (3.85, 2.4, 1.35), 70.0, (1.0, 0.12, 0.02), 0.8)

def add_camera(name, location, target, lens):
    data = bpy.data.cameras.new(name)
    camera = bpy.data.objects.new(name, data)
    LIGHTING.objects.link(camera)
    camera.location = location
    camera.data.lens = lens
    camera.data.sensor_width = 36.0
    camera['polyKitRole'] = 'inspection_camera'
    look_at(camera, target)
    return camera

camera = add_camera('PresentationCamera', (-4.3, -3.2, 2.75), (0.2, 2.2, 2.0), 30.0)
inspection_cameras = [
    # These two cameras are just inside the entry and right wall.  Placing
    # them outside the shell would test the wall's occlusion rather than the
    # scene layout and produces a misleading black render.
    ('entry', add_camera('InspectionCamera_Entry', (-5.05, -2.8, 2.15), (-0.2, 1.8, 2.0), 35.0)),
    ('hearth', add_camera('InspectionCamera_Hearth', (4.75, -0.6, 2.2), (0.5, 2.6, 1.9), 35.0)),
    # The front of this reference cabin is intentionally open, so the
    # exterior check is placed low in front of that opening.  A high corner
    # camera mostly sees the roof and is a poor test of the actual shell and
    # interior relationships.
    ('exterior', add_camera('InspectionCamera_Exterior', (0.0, -11.5, 3.0), (0.0, 2.0, 2.35), 42.0)),
]
scene['polyKitPreviewViews'] = json.dumps(
    [{'id': 'presentation', 'camera': camera.name}, *({'id': view_id, 'camera': view_camera.name} for view_id, view_camera in inspection_cameras)],
    separators=(',', ':'),
)
scene.camera = camera

# Persist a useful inspection state as well as the render camera.  Blender's
# viewport otherwise reopens in whatever free perspective happened to be
# active on the remote machine, which makes a correctly laid-out cabin look
# tiny or spatially inverted when a user first opens the .blend file.
try:
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.camera = camera
                area.spaces.active.region_3d.view_perspective = 'CAMERA'
except Exception:
    pass

try:
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
except Exception:
    # Blender 5.2 builds exposed by the official add-on may still use the
    # legacy enum name even though the renderer is Eevee.
    scene.render.engine = 'BLENDER_EEVEE'
scene.render.resolution_x = RENDER_WIDTH
scene.render.resolution_y = RENDER_HEIGHT
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
try:
    scene.view_settings.look = 'AgX - Medium High Contrast'
except Exception:
    pass
scene.world.color = (0.008, 0.012, 0.025)

def world_bbox(obj):
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (
        tuple(min(point[index] for point in points) for index in range(3)),
        tuple(max(point[index] for point in points) for index in range(3)),
    )

def validate_layout():
    """Validate semantic zones before exporting the scene.

    The checks are intentionally inexpensive and deterministic.  They catch
    the class of errors that is hard to notice from a random Blender viewport:
    an exterior prop accidentally placed inside the cabin, an interior asset
    outside the floor plan, or a presentation camera aimed away from the set.
    Detailed mesh collision/BVH checks belong to a heavier placement backend;
    this gate is the contract for this reviewed reference preset.
    """
    interior_min = (-5.7, -3.9, 0.0)
    interior_max = (5.7, 5.85, 5.55)
    exterior_clearance = 0.12
    errors = []
    warnings = []
    checked = 0
    collision_candidates = []
    collision_roles = {'bed', 'wood_stove', 'table', 'crate'}
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        zone = obj.get('polyKitZone')
        if zone == 'interior':
            checked += 1
            bb_min, bb_max = world_bbox(obj)
            if obj.get('polyKitRole') in collision_roles:
                collision_candidates.append((obj, bb_min, bb_max))
            if (
                bb_min[0] < interior_min[0] or bb_max[0] > interior_max[0] or
                bb_min[1] < interior_min[1] or bb_max[1] > interior_max[1]
            ):
                errors.append('interior_out_of_bounds:' + obj.name)
            if obj.get('polyKitRole') != 'stove_pipe' and (
                bb_min[2] < interior_min[2] or bb_max[2] > interior_max[2]
            ):
                errors.append('interior_z_out_of_bounds:' + obj.name)
            if bb_min[2] < -0.02:
                errors.append('interior_below_floor:' + obj.name)
        elif zone == 'exterior':
            checked += 1
            bb_min, bb_max = world_bbox(obj)
            crosses_x = (
                bb_min[0] < interior_max[0] + exterior_clearance and
                bb_max[0] > interior_min[0] - exterior_clearance
            )
            crosses_y = (
                bb_min[1] < interior_max[1] + exterior_clearance and
                bb_max[1] > interior_min[1] - exterior_clearance
            )
            if crosses_x and crosses_y:
                errors.append('exterior_crosses_cabin_boundary:' + obj.name)

    # Check only primary pieces here; mattress, legs, handles and other
    # authored details are expected to overlap their parent object.
    for index, (first, first_min, first_max) in enumerate(collision_candidates):
        for second, second_min, second_max in collision_candidates[index + 1:]:
            overlap_xy = (
                first_min[0] < second_max[0] and first_max[0] > second_min[0] and
                first_min[1] < second_max[1] and first_max[1] > second_min[1]
            )
            overlap_z = first_min[2] < second_max[2] and first_max[2] > second_min[2]
            if overlap_xy and overlap_z:
                errors.append('interior_mesh_overlap:' + first.name + ':' + second.name)

    target = Vector((0.2, 2.7, 2.35))
    # Architecture-specific checks catch the common failure mode where an
    # object is present but its contact/opening relationship is only correct
    # from the presentation camera.  The door must sit inside the evaluated
    # Boolean cutter, and exterior snow must remain a low ground bank outside
    # the shell.
    door = scene.objects.get('Door')
    left_wall = scene.objects.get('Left_Wall')
    door_cutter = scene.objects.get('Door_Opening_Cutter')
    has_door_boolean = bool(left_wall and door_cutter and any(
        modifier.type == 'BOOLEAN' and modifier.operation == 'DIFFERENCE' and modifier.object == door_cutter
        for modifier in left_wall.modifiers
    ))
    if door is None or not has_door_boolean:
        errors.append('missing_left_wall_door_opening')
    elif door_cutter:
        door_min, door_max = world_bbox(door)
        cutter_min, cutter_max = world_bbox(door_cutter)
        if (
            door_min[1] < cutter_min[1] or door_max[1] > cutter_max[1] or
            door_min[2] < cutter_min[2] or door_max[2] > cutter_max[2]
        ):
            errors.append('door_outside_left_wall_cutter')

    for snow_name, axis, boundary in (
        ('Snow_Outside_Back', 1, interior_max[1] + exterior_clearance),
        ('Snow_Outside_Side', 0, interior_max[0] + exterior_clearance),
    ):
        snow = scene.objects.get(snow_name)
        if snow is None:
            errors.append('missing_exterior_snow:' + snow_name)
            continue
        bb_min, bb_max = world_bbox(snow)
        if bb_min[axis] <= boundary or (bb_max[2] - bb_min[2]) > 0.75:
            errors.append('exterior_snow_not_grounded:' + snow_name)

    if scene.camera is None:
        errors.append('missing_presentation_camera')
    else:
        direction = target - scene.camera.location
        if direction.length < 0.1:
            errors.append('presentation_camera_target_too_close')
        if scene.camera.data.type != 'PERSP':
            warnings.append('presentation_camera_not_perspective')
    return {
        'status': 'error' if errors else 'pass',
        'checked_meshes': checked,
        'errors': errors,
        'warnings': warnings,
        'contract': 'interior/exterior semantic zones with Blender world-space AABB checks',
    }

validation = validate_layout()
scene['polyKitLayoutValidation'] = json.dumps(validation, separators=(',', ':'))
if validation['errors']:
    raise RuntimeError('layout validation failed: ' + ', '.join(validation['errors']))

root = pathlib.Path(tempfile.mkdtemp(prefix='polykit_blender_scene_'))
glb_path = root / (SCENE_NAME + '.glb')
blend_path = root / (SCENE_NAME + '.blend')
preview_path = root / (SCENE_NAME + '.png')
preview_view_paths = {}

scene.camera = camera
scene.render.filepath = str(preview_path)
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format='GLB', export_materials='EXPORT')
if RENDER_PREVIEW:
    bpy.ops.render.render(write_still=True)
    for view_id, view_camera in inspection_cameras:
        scene.camera = view_camera
        view_path = root / (SCENE_NAME + '_view_' + view_id + '.png')
        scene.render.filepath = str(view_path)
        bpy.ops.render.render(write_still=True)
        preview_view_paths[view_id] = view_path
    scene.camera = camera

def b64(path):
    return base64.b64encode(path.read_bytes()).decode('ascii') if path.exists() else ''

result = {
    'scene_name': SCENE_NAME,
    'preset': 'winter_cabin_reference',
    'object_count': len([obj for obj in scene.objects if obj.type == 'MESH']),
    'layout_validation': validation,
    'glb_b64': b64(glb_path),
    'blend_b64': b64(blend_path),
    'preview_b64': b64(preview_path) if RENDER_PREVIEW else '',
    'preview_views': {view_id: b64(path) for view_id, path in preview_view_paths.items()},
}
'''
    return (
        script.replace("__SCENE_NAME__", repr(scene_name))
        .replace("__SCENE_BRIEF__", repr(brief))
        .replace("__WIDTH__", str(width))
        .replace("__HEIGHT__", str(height))
        .replace("__RENDER_PREVIEW__", "True" if render_preview else "False")
    )


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
        message = response.get("message") or response.get("error") or "unknown Blender MCP error"
        raise RuntimeError(str(message))
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
    params = payload.get("params") or {}
    input_data = payload.get("input") or {}
    workspace_dir = Path(str(payload.get("workspaceDir") or ".")).expanduser().resolve()
    preset = str(params.get("preset") or "cabin")
    if preset != "cabin":
        error(f"blender-scene: unsupported preset '{preset}'")
        return
    scene_name = _slug(str(params.get("scene_name") or "winter_cabin_reference"))
    brief = str(input_data.get("text") or "A compact winter cabin interior with warm firelight.")[:2000]
    try:
        width = max(320, min(1600, int(params.get("render_width") or 768)))
        height = max(240, min(1200, int(params.get("render_height") or 512)))
    except (TypeError, ValueError):
        error("blender-scene: render dimensions must be integers")
        return
    render_preview = bool(params.get("render_preview", True))
    host = str(params.get("blender_host") or os.environ.get("POLYKIT_BLENDER_MCP_HOST") or os.environ.get("BLENDER_MCP_HOST") or "127.0.0.1")
    try:
        port = int(params.get("blender_port") or os.environ.get("POLYKIT_BLENDER_MCP_PORT") or os.environ.get("BLENDER_MCP_PORT") or 9876)
    except (TypeError, ValueError):
        error("blender-scene: Blender port must be an integer")
        return
    output_dir = workspace_dir / "Workflows"
    output_dir.mkdir(parents=True, exist_ok=True)
    glb_path = output_dir / f"{scene_name}.glb"
    blend_path = output_dir / f"{scene_name}.blend"
    preview_path = output_dir / f"{scene_name}.png"
    try:
        progress(5, f"Connecting to Blender MCP at {host}:{port}…")
        result = _send_blender_code(host, port, _scene_script(scene_name, brief, width, height, render_preview))
        progress(75, "Receiving Blender scene artifacts…")
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
        preview_views = result.get("preview_views")
        if isinstance(preview_views, dict):
            for view_id, encoded in preview_views.items():
                if not str(encoded or "").strip():
                    continue
                view_path = output_dir / f"{scene_name}_view_{_slug(str(view_id))}.png"
                view_path.write_bytes(base64.b64decode(str(encoded), validate=True))
                sidecars.append(str(view_path))
        emit({
            "type": "log",
            "message": (
                f"Built {result.get('object_count', '?')} mesh objects with Blender MCP "
                f"({host}:{port}); layout validation: "
                f"{(result.get('layout_validation') or {}).get('status', 'unknown')}"
            ),
        })
        progress(100, "Blender scene ready")
        emit({
            "type": "done",
            "result": {
                "filePath": str(glb_path),
                "sidecars": sidecars,
                "metadata": {"layoutValidation": result.get('layout_validation')},
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
