"""Structured Blender production process nodes.

The pack deliberately exposes bounded operations instead of accepting arbitrary
Python.  It is executed by the normal PolyKit process runner and communicates
with the official Blender bridge only from the server-side node pack.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import socket
import sys
import uuid
import zlib
from pathlib import Path
from typing import Any, Mapping


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def progress(percent: int, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(percent))), "label": label})


def error(message: str) -> None:
    emit({"type": "error", "message": message})


def _slug(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", "_", value).strip("_").lower()
    return result[:64] or "blender_production"


def _float(params: Mapping[str, Any], key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(params.get(key, default))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


def _int(params: Mapping[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(params.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bool(params: Mapping[str, Any], key: str, default: bool) -> bool:
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _scene_script(operation: str, params: Mapping[str, Any], input_path: str | None, input_b64: str | None, scene_name: str, render_preview: bool) -> str:
    """Return a self-contained script for the official Blender bridge."""
    script = r'''
import base64
import json
import math
import pathlib
import re
import tempfile
import zlib
import bpy
from mathutils import Vector

OPERATION = __OPERATION__
PARAMS = json.loads(__PARAMS_JSON__)
INPUT_PATH = __INPUT_PATH__
INPUT_B64 = __INPUT_B64__
SCENE_NAME = __SCENE_NAME__
RENDER_PREVIEW = __RENDER_PREVIEW__

scene = bpy.context.scene
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for collection in list(bpy.data.collections):
    bpy.data.collections.remove(collection)
scene.name = SCENE_NAME
scene['polyKitProductionOperation'] = OPERATION
scene['polyKitProductionVersion'] = '1'
try:
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.length_unit = 'METERS'
except Exception:
    pass

def collection(name):
    value = bpy.data.collections.new(name)
    scene.collection.children.link(value)
    return value

MODEL = collection('Model')
DETAILS = collection('ProductionDetails')
LIGHTS = collection('Lighting')

def move(obj, target):
    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    target.objects.link(obj)

def principled(mat):
    nodes = mat.node_tree.nodes
    return nodes.get('Principled BSDF') or next((node for node in nodes if node.bl_idname == 'ShaderNodeBsdfPrincipled'), None)

def material(name, color, roughness=0.55, metallic=0.0, transmission=0.0):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.use_nodes = True
    bsdf = principled(mat)
    if bsdf:
        if 'Base Color' in bsdf.inputs:
            bsdf.inputs['Base Color'].default_value = (*color, 1.0)
        if 'Roughness' in bsdf.inputs:
            bsdf.inputs['Roughness'].default_value = roughness
        if 'Metallic' in bsdf.inputs:
            bsdf.inputs['Metallic'].default_value = metallic
        if 'Transmission Weight' in bsdf.inputs:
            bsdf.inputs['Transmission Weight'].default_value = transmission
        elif 'Transmission' in bsdf.inputs:
            bsdf.inputs['Transmission'].default_value = transmission
    return mat

WOOD = material('Production Wood', (0.28, 0.075, 0.018), 0.72)
METAL = material('Production Metal', (0.06, 0.08, 0.11), 0.28, 0.85)
CONCRETE = material('Production Concrete', (0.34, 0.36, 0.38), 0.88)
FRAME = material('Production Frame', (0.13, 0.03, 0.012), 0.42)
GLASS = material('Production Opening Glass', (0.22, 0.42, 0.62), 0.08, 0.0, 0.88)
BLACK = material('NPR Outline', (0.008, 0.008, 0.012), 0.95)
TOON = material('NPR Toon', (0.24, 0.42, 0.8), 0.9)
BLACK.use_backface_culling = True

def cube(name, location, dimensions, mat=WOOD, target=MODEL, rotation=(0.0, 0.0, 0.0), bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if mat:
        obj.data.materials.append(mat)
    move(obj, target)
    if bevel:
        mod = obj.modifiers.new('Edge Softening', 'BEVEL')
        mod.width = bevel
        mod.segments = 2
    return obj

def cylinder(name, location, radius, depth, mat=METAL, target=DETAILS):
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=radius, depth=depth, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    move(obj, target)
    return obj

def import_mesh(path):
    if not path:
        return []
    path = str(path)
    if INPUT_B64 and not pathlib.Path(path).is_file():
        transferred = pathlib.Path(tempfile.gettempdir()) / ('polykit_input_' + SCENE_NAME + pathlib.Path(path).suffix.lower())
        transferred.write_bytes(zlib.decompress(base64.b64decode(INPUT_B64)))
        path = str(transferred)
    suffix = pathlib.Path(path).suffix.lower()
    if suffix in {'.glb', '.gltf'}:
        bpy.ops.import_scene.gltf(filepath=path)
    elif suffix == '.obj':
        bpy.ops.wm.obj_import(filepath=path)
    elif suffix == '.fbx':
        bpy.ops.import_scene.fbx(filepath=path)
    elif suffix == '.ply':
        bpy.ops.wm.ply_import(filepath=path)
    elif suffix == '.stl':
        bpy.ops.wm.stl_import(filepath=path)
    else:
        raise RuntimeError('unsupported mesh format: ' + suffix)
    imported = [obj for obj in bpy.context.selected_objects if obj.type == 'MESH']
    for obj in imported:
        move(obj, MODEL)
        obj['polyKitImported'] = True
    return imported

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

def build_eevee_outline_group(width, noise_scale, wobble, outline_material):
    """Build one reusable deformation-following inverted-hull node group."""
    group = bpy.data.node_groups.get('PolyKit Eevee Inverted Hull')
    if group is not None:
        return group
    group = bpy.data.node_groups.new('PolyKit Eevee Inverted Hull', 'GeometryNodeTree')
    _interface_socket(group, 'Geometry', 'INPUT', 'NodeSocketGeometry')
    _interface_socket(group, 'Enable Outline', 'INPUT', 'NodeSocketBool', True)
    _interface_socket(group, 'Outline Width', 'INPUT', 'NodeSocketFloat', width)
    _interface_socket(group, 'Noise Scale', 'INPUT', 'NodeSocketFloat', noise_scale)
    _interface_socket(group, 'Wobble', 'INPUT', 'NodeSocketFloat', wobble)
    _interface_socket(group, 'Outline Material', 'INPUT', 'NodeSocketMaterial', outline_material)
    _interface_socket(group, 'Geometry', 'OUTPUT', 'NodeSocketGeometry')
    nodes = group.nodes
    links = group.links
    group_input = nodes.new('NodeGroupInput')
    group_output = nodes.new('NodeGroupOutput')
    extrude = nodes.new('GeometryNodeExtrudeMesh')
    extrude.name = 'Outline Extrude Faces'
    extrude.mode = 'FACES'
    extrude.inputs['Selection'].default_value = True
    separate = nodes.new('GeometryNodeSeparateGeometry')
    separate.name = 'Outline Top Face Isolation'
    separate.domain = 'FACE'
    set_position = nodes.new('GeometryNodeSetPosition')
    set_position.name = 'Outline Wobble Set Position'
    normal = nodes.new('GeometryNodeInputNormal')
    position = nodes.new('GeometryNodeInputPosition')
    noise = nodes.new('ShaderNodeTexNoise')
    noise.name = 'Outline Position Noise'
    map_range = nodes.new('ShaderNodeMapRange')
    map_range.name = 'Outline Centered Noise'
    map_range.inputs['From Min'].default_value = 0.0
    map_range.inputs['From Max'].default_value = 1.0
    map_range.inputs['To Min'].default_value = -1.0
    map_range.inputs['To Max'].default_value = 1.0
    combine = nodes.new('ShaderNodeCombineXYZ')
    centered = nodes.new('ShaderNodeMath')
    centered.operation = 'MULTIPLY'
    vector_mul = nodes.new('ShaderNodeVectorMath')
    vector_mul.operation = 'MULTIPLY'
    flip = nodes.new('GeometryNodeFlipFaces')
    flip.name = 'Outline Flip Faces'
    set_material = nodes.new('GeometryNodeSetMaterial')
    set_material.name = 'Outline Set Material'
    join = nodes.new('GeometryNodeJoinGeometry')
    join.name = 'Outline Join Source'
    switch = nodes.new('GeometryNodeSwitch')
    switch.name = 'Outline Geometry Switch'
    switch.input_type = 'GEOMETRY'

    links.new(group_input.outputs['Geometry'], extrude.inputs['Mesh'])
    links.new(group_input.outputs['Outline Width'], extrude.inputs['Offset Scale'])
    links.new(extrude.outputs['Mesh'], separate.inputs['Geometry'])
    links.new(extrude.outputs['Top'], separate.inputs['Selection'])
    links.new(separate.outputs['Selection'], set_position.inputs['Geometry'])
    links.new(position.outputs['Position'], noise.inputs['Vector'])
    links.new(group_input.outputs['Noise Scale'], noise.inputs['Scale'])
    links.new(noise.outputs['Fac'], map_range.inputs['Value'])
    links.new(map_range.outputs['Result'], centered.inputs[0])
    links.new(group_input.outputs['Wobble'], centered.inputs[1])
    links.new(centered.outputs[0], combine.inputs['X'])
    links.new(centered.outputs[0], combine.inputs['Y'])
    links.new(centered.outputs[0], combine.inputs['Z'])
    links.new(normal.outputs['Normal'], vector_mul.inputs[0])
    links.new(combine.outputs['Vector'], vector_mul.inputs[1])
    links.new(vector_mul.outputs['Vector'], set_position.inputs['Offset'])
    links.new(set_position.outputs['Geometry'], flip.inputs['Mesh'])
    links.new(flip.outputs['Mesh'], set_material.inputs['Geometry'])
    links.new(group_input.outputs['Outline Material'], set_material.inputs['Material'])
    links.new(group_input.outputs['Geometry'], join.inputs['Geometry'])
    links.new(set_material.outputs['Geometry'], join.inputs['Geometry'])
    links.new(group_input.outputs['Enable Outline'], switch.inputs['Switch'])
    links.new(join.outputs['Geometry'], switch.inputs['True'])
    links.new(group_input.outputs['Geometry'], switch.inputs['False'])
    links.new(switch.outputs['Output'], group_output.inputs['Geometry'])

    return group

def build_eevee_outline(obj, width, noise_scale, wobble, outline_material, group=None):
    """Attach the shared inverted-hull Geometry Nodes group to an object."""
    for modifier in list(obj.modifiers):
        if modifier.name.startswith('PolyKit Eevee Outline'):
            obj.modifiers.remove(modifier)
    group = group or build_eevee_outline_group(width, noise_scale, wobble, outline_material)
    modifier = obj.modifiers.new('PolyKit Eevee Outline', 'NODES')
    modifier.node_group = group
    for item in group.interface.items_tree:
        if getattr(item, 'item_type', None) != 'SOCKET' or item.in_out != 'INPUT':
            continue
        values = {
            'Enable Outline': True,
            'Outline Width': width,
            'Noise Scale': noise_scale,
            'Wobble': wobble,
            'Outline Material': outline_material,
        }
        if item.name in values:
            try:
                modifier[item.identifier] = values[item.name]
            except Exception:
                pass
    obj['polyKitNprSystem'] = 'eevee-inverted-hull'
    obj['polyKitNprOutlineWidth'] = float(width)
    obj['polyKitNprNoiseScale'] = float(noise_scale)
    obj['polyKitNprWobble'] = float(wobble)
    return group

def _toon_shade(color, factor):
    return tuple(max(0.0, min(1.0, float(channel) * factor)) for channel in color) + (1.0,)

def build_eevee_toon_material(base_color=(0.28, 0.48, 0.92), source_name='default'):
    safe_source = re.sub(r'[^0-9A-Za-z_]+', '_', str(source_name)).strip('_')[:48] or 'default'
    mat = bpy.data.materials.new('PolyKit NPR Eevee Toon ' + safe_source)
    mat.diffuse_color = (*base_color, 1.0)
    mat['production_material_class'] = 'npr-toon'
    mat['production_texture_scale_m'] = 1.0
    mat['production_surface_variant'] = 'renderer-native'
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    diffuse = nodes.new('ShaderNodeBsdfDiffuse')
    diffuse.name = 'Toon Diffuse'
    diffuse.inputs['Color'].default_value = (1.0, 1.0, 1.0, 1.0)
    shader_to_rgb = nodes.new('ShaderNodeShaderToRGB')
    shader_to_rgb.name = 'Discrete Shader To RGB'
    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.name = 'Constant Toon Bands'
    ramp.color_ramp.interpolation = 'CONSTANT'
    ramp.color_ramp.elements[0].position = 0.30
    ramp.color_ramp.elements[0].color = _toon_shade(base_color, 0.42)
    ramp.color_ramp.elements[1].position = 0.54
    ramp.color_ramp.elements[1].color = _toon_shade(base_color, 0.86)
    highlight = ramp.color_ramp.elements.new(0.76)
    highlight.color = _toon_shade(base_color, 1.38)
    variation = nodes.new('ShaderNodeValToRGB')
    variation.name = 'Stable Material Variation'
    variation.color_ramp.elements[0].position = 0.28
    variation.color_ramp.elements[0].color = (0.86, 0.86, 0.86, 1.0)
    variation.color_ramp.elements[1].position = 0.74
    variation.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    noise = nodes.new('ShaderNodeTexNoise')
    noise.name = 'Toon Surface Variation'
    noise.noise_dimensions = '3D'
    noise.inputs['Scale'].default_value = 4.0
    noise.inputs['Detail'].default_value = 2.0
    noise.inputs['Roughness'].default_value = 0.45
    texcoord = nodes.new('ShaderNodeTexCoord')
    texcoord.name = 'Toon Object Coordinates'
    multiply = nodes.new('ShaderNodeMixRGB')
    multiply.name = 'Toon Palette x Material Variation'
    multiply.blend_type = 'MULTIPLY'
    multiply.inputs['Fac'].default_value = 1.0
    emission = nodes.new('ShaderNodeEmission')
    emission.name = 'Stable Toon Fill'
    emission.inputs['Strength'].default_value = 1.0
    links.new(diffuse.outputs['BSDF'], shader_to_rgb.inputs['Shader'])
    links.new(shader_to_rgb.outputs['Color'], ramp.inputs['Fac'])
    links.new(texcoord.outputs['Generated'], noise.inputs['Vector'])
    links.new(noise.outputs['Fac'], variation.inputs['Fac'])
    links.new(ramp.outputs['Color'], multiply.inputs['Color1'])
    links.new(variation.outputs['Color'], multiply.inputs['Color2'])
    links.new(multiply.outputs['Color'], emission.inputs['Color'])
    links.new(emission.outputs['Emission'], output.inputs['Surface'])
    mat['polyKitNprRenderer'] = 'eevee'
    mat['polyKitNprBands'] = 3
    mat['polyKitNprBaseColor'] = tuple(float(channel) for channel in base_color)
    mat['polyKitNprSourceMaterial'] = str(source_name)
    return mat

def build_cycles_ray_sample_group():
    group = bpy.data.node_groups.get('PolyKit Cycles Ray Sample')
    if group is not None:
        return group
    group = bpy.data.node_groups.new('PolyKit Cycles Ray Sample', 'ShaderNodeTree')
    _interface_socket(group, 'Offset', 'INPUT', 'NodeSocketVector', (1.0, 0.0, 0.0))
    _interface_socket(group, 'Width', 'INPUT', 'NodeSocketFloat', 0.018)
    _interface_socket(group, 'Ray Length', 'INPUT', 'NodeSocketFloat', 100.0)
    _interface_socket(group, 'Is Hit', 'OUTPUT', 'NodeSocketFloat')
    nodes = group.nodes
    links = group.links
    group_input = nodes.new('NodeGroupInput')
    group_output = nodes.new('NodeGroupOutput')
    transform = nodes.new('ShaderNodeVectorTransform')
    transform.name = 'Camera Offset To World'
    transform.vector_type = 'VECTOR'
    transform.convert_from = 'CAMERA'
    transform.convert_to = 'WORLD'
    scale = nodes.new('ShaderNodeVectorMath')
    scale.name = 'Scale Offset By Width'
    scale.operation = 'SCALE'
    geometry = nodes.new('ShaderNodeNewGeometry')
    add = nodes.new('ShaderNodeVectorMath')
    add.name = 'Shift Ray Origin'
    add.operation = 'ADD'
    reverse = nodes.new('ShaderNodeVectorMath')
    reverse.name = 'Reverse Incoming'
    reverse.operation = 'SCALE'
    reverse.inputs['Scale'].default_value = -1.0
    raycast = nodes.new('ShaderNodeRaycast')
    raycast.name = 'Bounded Local Raycast'
    raycast.only_local = True
    links.new(group_input.outputs['Offset'], transform.inputs['Vector'])
    links.new(transform.outputs['Vector'], scale.inputs[0])
    links.new(group_input.outputs['Width'], scale.inputs['Scale'])
    links.new(geometry.outputs['Position'], add.inputs[0])
    links.new(scale.outputs['Vector'], add.inputs[1])
    links.new(add.outputs['Vector'], raycast.inputs['Position'])
    links.new(geometry.outputs['Incoming'], reverse.inputs[0])
    links.new(reverse.outputs['Vector'], raycast.inputs['Direction'])
    links.new(group_input.outputs['Ray Length'], raycast.inputs['Length'])
    links.new(raycast.outputs['Is Hit'], group_output.inputs['Is Hit'])
    return group

def _material_base_color(source):
    if source is not None:
        bsdf = principled(source)
        if bsdf is not None:
            socket = bsdf.inputs.get('Base Color')
            if socket is not None and not socket.is_linked:
                value = socket.default_value
                return tuple(float(max(0.0, min(1.0, value[index]))) for index in range(3))
        value = source.diffuse_color
        return tuple(float(max(0.0, min(1.0, value[index]))) for index in range(3))
    return (0.28, 0.48, 0.92)

def _source_base_color(obj):
    for slot in obj.material_slots:
        if slot.material is not None:
            return _material_base_color(slot.material)
    return (0.28, 0.48, 0.92)

def _material_slots_with_toon_variant(obj, make_variant):
    """Keep authored slots and render through appended NPR variants."""
    authored = list(obj.data.materials)
    if not authored:
        authored = [None]
    original_indices = [int(polygon.material_index) for polygon in obj.data.polygons]
    variants = {}
    variant_indices = {}
    for index, source in enumerate(authored):
        base = _source_base_color(obj) if source is None else _material_base_color(source)
        key = tuple(round(channel, 5) for channel in base)
        if key not in variants:
            variants[key] = make_variant(base, source.name if source is not None else 'default')
        variant = variants[key]
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
    return variants

def presentation_objects(objects):
    mesh_objects = [obj for obj in objects if obj.type == 'MESH']
    excluded = []
    spans = []
    for obj in mesh_objects:
        minimum, maximum = bounds([obj])
        dimensions = maximum - minimum
        spans.append((obj, dimensions))
    nonflat_span = max(
        (max(float(dimensions.x), float(dimensions.y)) for _obj, dimensions in spans if float(dimensions.z) > 0.25),
        default=0.0,
    )
    for obj, dimensions in spans:
        if obj.type != 'MESH':
            continue
        role = str(obj.get('polyKitEnvironmentRole', '')).lower()
        name = obj.name.lower()
        planar_receiver = (
            nonflat_span > 0.0
            and float(dimensions.z) <= 0.25
            and max(float(dimensions.x), float(dimensions.y)) >= nonflat_span * 1.6
        )
        if role in {'snow-receiver', 'ground', 'backdrop', 'environment'} or planar_receiver or any(token in name for token in ('environment-ground', 'snow-ground', 'backdrop', 'receiver')):
            excluded.append(obj)
    candidates = [obj for obj in mesh_objects if obj not in excluded]
    return candidates or mesh_objects

def build_cycles_npr_material(width, ray_length, base_color=(0.28, 0.48, 0.92), source_name='default'):
    sample = build_cycles_ray_sample_group()
    safe_source = re.sub(r'[^0-9A-Za-z_]+', '_', str(source_name)).strip('_')[:48] or 'default'
    toon_color = tuple(min(1.0, max(0.0, float(channel) * 1.65 + 0.02)) for channel in base_color)
    look_name = 'PolyKit Cycles Four-Ray NPR ' + safe_source
    look = bpy.data.node_groups.get(look_name)
    if look is None:
        look = bpy.data.node_groups.new(look_name, 'ShaderNodeTree')
        _interface_socket(look, 'Base Color', 'INPUT', 'NodeSocketColor', (*toon_color, 1.0))
        _interface_socket(look, 'Outline Color', 'INPUT', 'NodeSocketColor', (0.008, 0.008, 0.012, 1.0))
        _interface_socket(look, 'Outline Width', 'INPUT', 'NodeSocketFloat', width)
        _interface_socket(look, 'Ray Length', 'INPUT', 'NodeSocketFloat', ray_length)
        _interface_socket(look, 'Shader', 'OUTPUT', 'NodeSocketShader')
        _interface_socket(look, 'Outline Mask', 'OUTPUT', 'NodeSocketFloat')
        nodes = look.nodes
        links = look.links
        group_input = nodes.new('NodeGroupInput')
        group_output = nodes.new('NodeGroupOutput')
        try:
            toon = nodes.new('ShaderNodeBsdfToon')
            toon.name = 'Cycles Toon BSDF Fill'
            toon.component = 'DIFFUSE'
            toon.inputs['Size'].default_value = 0.52
            toon.inputs['Smooth'].default_value = 0.03
            look['polyKitNprCyclesFill'] = 'toon-bsdf'
        except Exception:
            toon = nodes.new('ShaderNodeBsdfDiffuse')
            toon.name = 'Cycles Flat Diffuse Fallback'
            look['polyKitNprCyclesFill'] = 'diffuse-fallback'
        outline = nodes.new('ShaderNodeEmission')
        outline.name = 'Cycles Outline Emission'
        outline.inputs['Strength'].default_value = 1.0
        mix = nodes.new('ShaderNodeMixShader')
        offsets = ((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0))
        masks = []
        for index, offset in enumerate(offsets):
            sample_node = nodes.new('ShaderNodeGroup')
            sample_node.name = 'Ray Sample ' + ('PosX', 'NegX', 'PosY', 'NegY')[index]
            sample_node.node_tree = sample
            _set_group_input(sample_node, 'Offset', offset)
            links.new(group_input.outputs['Outline Width'], sample_node.inputs['Width'])
            links.new(group_input.outputs['Ray Length'], sample_node.inputs['Ray Length'])
            masks.append(sample_node.outputs['Is Hit'])
        multiply_a = nodes.new('ShaderNodeMath')
        multiply_a.name = 'Multiply Ray Hits A'
        multiply_a.operation = 'MULTIPLY'
        multiply_b = nodes.new('ShaderNodeMath')
        multiply_b.name = 'Multiply Ray Hits B'
        multiply_b.operation = 'MULTIPLY'
        multiply_c = nodes.new('ShaderNodeMath')
        multiply_c.name = 'Multiply Ray Hits C'
        multiply_c.operation = 'MULTIPLY'
        links.new(masks[0], multiply_a.inputs[0])
        links.new(masks[1], multiply_a.inputs[1])
        links.new(masks[2], multiply_b.inputs[0])
        links.new(masks[3], multiply_b.inputs[1])
        links.new(multiply_a.outputs[0], multiply_c.inputs[0])
        links.new(multiply_b.outputs[0], multiply_c.inputs[1])
        links.new(group_input.outputs['Base Color'], toon.inputs['Color'])
        links.new(group_input.outputs['Outline Color'], outline.inputs['Color'])
        links.new(multiply_c.outputs[0], mix.inputs[0])
        links.new(outline.outputs['Emission'], mix.inputs[1])
        links.new(toon.outputs['BSDF'], mix.inputs[2])
        links.new(mix.outputs['Shader'], group_output.inputs['Shader'])
        links.new(multiply_c.outputs[0], group_output.inputs['Outline Mask'])
    mat = bpy.data.materials.new('PolyKit NPR Cycles Raycast ' + safe_source)
    mat.use_nodes = True
    mat['production_material_class'] = 'npr-toon'
    mat['production_texture_scale_m'] = 1.0
    mat['production_surface_variant'] = 'renderer-native'
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    group_node = nodes.new('ShaderNodeGroup')
    group_node.name = 'Four Direction Raycast Toon'
    group_node.node_tree = look
    _set_group_input(group_node, 'Outline Width', width)
    _set_group_input(group_node, 'Ray Length', ray_length)
    links.new(group_node.outputs['Shader'], output.inputs['Surface'])
    mat['polyKitNprRenderer'] = 'cycles'
    mat['polyKitNprBaseColor'] = tuple(float(channel) for channel in base_color)
    mat['polyKitNprToonColor'] = tuple(float(channel) for channel in toon_color)
    mat['polyKitNprSourceMaterial'] = str(source_name)
    mat['polyKitNprSystem'] = 'four-direction-shader-raycast'
    mat['polyKitNprRayLength'] = float(ray_length)
    return mat, sample, look

def look_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat('-Z', 'Y').to_euler()

def add_light(name, kind, location, energy, color, size=2.0):
    data = bpy.data.lights.new(name=name, type=kind)
    data.energy = energy
    data.color = color
    if hasattr(data, 'shadow_soft_size'):
        data.shadow_soft_size = size
    obj = bpy.data.objects.new(name, data)
    LIGHTS.objects.link(obj)
    obj.location = location
    return obj

def bounds(objects):
    points = []
    for obj in objects:
        if obj.type != 'MESH':
            continue
        points.extend([obj.matrix_world @ Vector(corner) for corner in obj.bound_box])
    if not points:
        return Vector((-1.0, -1.0, 0.0)), Vector((1.0, 1.0, 1.0))
    return Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))), Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))

def add_presentation(objects):
    framed_objects = presentation_objects(objects)
    minimum, maximum = bounds(framed_objects)
    center = (minimum + maximum) * 0.5
    extent = max((maximum - minimum).length, 1.0)
    # Area-light energy is distance-sensitive. Scale the authored preset for
    # larger composed scenes so a cabin-sized asset remains readable without
    # forcing callers to guess an exposure value; small assets keep the
    # original preset intensity.
    energy_scale = max(1.0, (extent / 12.0) ** 2)
    preset = str(PARAMS.get('preset', 'three-point')).lower() if OPERATION == 'lighting' else 'three-point'
    if preset == 'daylight':
        key_spec = (center + Vector((-extent * 1.2, -extent * 1.5, extent * 2.4)), 1500.0 * energy_scale, (1.0, 0.92, 0.78), extent * 0.9)
        fill_spec = (center + Vector((extent * 1.4, -extent * 0.3, extent * 0.9)), 850.0 * energy_scale, (0.66, 0.82, 1.0), extent * 1.2)
        rim_spec = (center + Vector((0.0, extent * 1.8, extent * 1.4)), 260.0 * energy_scale, (0.72, 0.84, 1.0), extent)
    elif preset == 'dramatic':
        key_spec = (center + Vector((-extent * 1.7, -extent * 0.8, extent * 1.1)), 1250.0 * energy_scale, (1.0, 0.32, 0.12), extent * 0.3)
        fill_spec = (center + Vector((extent * 1.6, -extent * 0.1, extent * 0.4)), 110.0 * energy_scale, (0.18, 0.28, 1.0), extent * 0.35)
        rim_spec = (center + Vector((extent * 0.2, extent * 1.6, extent * 1.6)), 1050.0 * energy_scale, (0.3, 0.48, 1.0), extent * 0.25)
    else:
        key_spec = (center + Vector((-extent, -extent, extent * 1.2)), 850.0 * energy_scale, (1.0, 0.72, 0.52), extent * 0.55)
        fill_spec = (center + Vector((extent, -extent * 0.25, extent * 0.65)), 520.0 * energy_scale, (0.45, 0.62, 1.0), extent * 0.7)
        rim_spec = (center + Vector((0.0, extent, extent * 1.1)), 700.0 * energy_scale, (0.55, 0.7, 1.0), extent * 0.45)
    key = add_light('Key_Light', 'AREA', *key_spec)
    fill = add_light('Fill_Light', 'AREA', *fill_spec)
    rim = add_light('Rim_Light', 'AREA', *rim_spec)
    for light in (key, fill, rim):
        look_at(light, center)
    camera_data = bpy.data.cameras.new('InspectionCamera')
    camera = bpy.data.objects.new('InspectionCamera', camera_data)
    LIGHTS.objects.link(camera)
    camera.location = center + Vector((extent * 1.65, -extent * 1.9, extent * 1.2))
    camera_data.lens = 48.0
    look_at(camera, center)
    camera['polyKitInspectionView'] = 'production'
    scene.camera = camera
    # NPR/Cycles selects its renderer before presentation setup; do not let the
    # generic camera helper silently switch that run back to Eevee.
    if not (OPERATION == 'npr' and str(PARAMS.get('renderer', 'eevee')).lower() == 'cycles'):
        try:
            scene.render.engine = 'BLENDER_EEVEE_NEXT'
        except Exception:
            scene.render.engine = 'BLENDER_EEVEE'
    scene.render.resolution_x = 768
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.world.color = (0.012, 0.016, 0.03)
    scene['polyKitPresentationObjectCount'] = len(framed_objects)
    scene['polyKitPresentationExcluded'] = [obj.name for obj in objects if obj not in framed_objects]
    return camera

def configure_structure_lines(mode):
    mode = str(mode or 'silhouette').lower()
    if mode not in {'silhouette', 'structure', 'hybrid'}:
        raise RuntimeError("line_mode must be 'silhouette', 'structure', or 'hybrid'")
    if mode == 'silhouette':
        try:
            scene.render.use_freestyle = False
        except Exception:
            pass
        return {'mode': mode, 'enabled': False, 'system': 'inverted-hull-or-raycast'}
    try:
        scene.render.use_freestyle = True
        view_layer = scene.view_layers[0]
        settings = view_layer.freestyle_settings
        line_set = settings.linesets[0] if len(settings.linesets) else settings.linesets.new('PolyKit Structure Lines')
        line_set.select_silhouette = mode == 'hybrid'
        line_set.select_crease = True
        line_set.select_border = True
        if hasattr(line_set, 'select_material_boundary'):
            line_set.select_material_boundary = True
        style = line_set.linestyle
        style.color = (0.012, 0.016, 0.025)
        style.thickness = 1.15
        return {
            'mode': mode,
            'enabled': True,
            'system': 'freestyle-structure-lines',
            'lineSet': line_set.name,
            'crease': True,
            'materialBoundary': bool(getattr(line_set, 'select_material_boundary', False)),
        }
    except Exception as exc:
        try:
            scene.render.use_freestyle = False
        except Exception:
            pass
        return {'mode': mode, 'enabled': False, 'system': 'freestyle-unavailable', 'warning': str(exc)}

def render_metrics(path):
    image = bpy.data.images.get('Render Result')
    loaded_from_disk = False
    if (image is None or tuple(image.size) == (0, 0)) and pathlib.Path(path).exists():
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
    deviation = math.sqrt(sum((value - mean) ** 2 for value in luminance) / len(luminance))
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

objects = []
metadata = {'operation': OPERATION, 'version': 1, 'blenderVersion': bpy.app.version_string, 'warnings': []}

if OPERATION == 'opening':
    width = float(PARAMS.get('wall_width', 6.0))
    height = float(PARAMS.get('wall_height', 3.2))
    thickness = float(PARAMS.get('wall_thickness', 0.24))
    opening_width = min(float(PARAMS.get('opening_width', 0.9)), width * 0.8)
    opening_height = min(float(PARAMS.get('opening_height', 2.1)), height * 0.8)
    sill = max(0.0, min(float(PARAMS.get('opening_sill', 0.0)), height - opening_height))
    frame_depth = float(PARAMS.get('frame_depth', 0.12))
    wall = cube('Host_Wall', (0.0, 0.0, height / 2.0), (width, thickness, height), CONCRETE, bevel=0.02)
    cutter = cube('Boolean_Cutter', (0.0, 0.0, sill + opening_height / 2.0), (opening_width, thickness * 3.0, opening_height), None, target=DETAILS)
    modifier = wall.modifiers.new('Architectural Opening', 'BOOLEAN')
    modifier.operation = 'DIFFERENCE'
    modifier.solver = 'EXACT'
    modifier.object = cutter
    bpy.context.view_layer.objects.active = wall
    wall.select_set(True)
    try:
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    except Exception as exc:
        metadata['warnings'].append('boolean_apply_failed:' + str(exc))
    bpy.data.objects.remove(cutter, do_unlink=True)
    frame_width = max(frame_depth, min(0.18, opening_width * 0.12))
    cube('Frame_Left', (-opening_width / 2.0 - frame_width / 2.0, -thickness * 0.58, sill + opening_height / 2.0), (frame_width, frame_depth, opening_height + frame_width), FRAME, DETAILS, bevel=0.02)
    cube('Frame_Right', (opening_width / 2.0 + frame_width / 2.0, -thickness * 0.58, sill + opening_height / 2.0), (frame_width, frame_depth, opening_height + frame_width), FRAME, DETAILS, bevel=0.02)
    cube('Frame_Top', (0.0, -thickness * 0.58, sill + opening_height + frame_width / 2.0), (opening_width + frame_width * 2.0, frame_depth, frame_width), FRAME, DETAILS, bevel=0.02)
    opening_type = str(PARAMS.get('opening_type', 'door')).lower()
    if opening_type == 'window':
        cube('Opening_Glass', (0.0, -thickness * 0.48, sill + opening_height / 2.0), (opening_width * 0.94, 0.025, opening_height * 0.94), GLASS, DETAILS)
    else:
        cube('Opening_Door', (0.0, -thickness * 0.48, sill + opening_height / 2.0), (opening_width * 0.92, 0.045, opening_height * 0.96), WOOD, DETAILS, bevel=0.018)
    objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    metadata.update({'openingType': opening_type, 'openingWidth': opening_width, 'openingHeight': opening_height, 'sillHeight': sill, 'boolean': 'exact', 'infill': 'glass' if opening_type == 'window' else 'door'})
elif OPERATION == 'array-stairs':
    steps = max(2, int(PARAMS.get('steps', 12)))
    run = max(0.05, float(PARAMS.get('run', 0.28)))
    rise = max(0.03, float(PARAMS.get('rise', 0.18)))
    width = max(0.2, float(PARAMS.get('width', 1.2)))
    tread_thickness = 0.12
    # Keep the first tread's underside on the Blender ground plane.  The
    # composed-scene fitter also enforces contact, but standalone stair
    # artifacts should not ship with half their base below y=0.
    tread = cube('Stair_Tread_Array', (0.0, 0.0, tread_thickness / 2.0), (width, run, tread_thickness), WOOD, bevel=0.025)
    array = tread.modifiers.new('Parametric Stair Array', 'ARRAY')
    array.count = steps
    array.use_relative_offset = False
    array.use_constant_offset = True
    array.constant_offset_displace = (0.0, run, rise)
    bpy.context.view_layer.objects.active = tread
    tread.select_set(True)
    bpy.ops.object.modifier_apply(modifier=array.name)
    if bool(PARAMS.get('railings', True)):
        rail_height = 0.92
        for side in (-1.0, 1.0):
            x = side * (width / 2.0 - 0.04)
            cube('Stair_Rail', (x, (steps - 1) * run / 2.0, (steps - 1) * rise / 2.0 + rail_height), (0.06, steps * run + 0.12, 0.06), METAL, DETAILS, rotation=(math.atan2((steps - 1) * rise, max(steps * run, 0.01)), 0.0, 0.0))
            for index in (0, steps - 1):
                # Each post stands on the corresponding tread.  The end
                # post must use the full accumulated rise; halving it leaves
                # the post floating inside the flight for normal stair rises.
                cylinder('Rail_Post', (x, index * run, index * rise + rail_height / 2.0), 0.035, rail_height, METAL)
    objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    metadata.update({'steps': steps, 'run': run, 'rise': rise, 'arrayApplied': True})
elif OPERATION == 'curve-profile':
    raw = PARAMS.get('points', '[[0,0,0],[1,0,0],[2,0.5,0.4],[3,0.5,0.4]]')
    try:
        points = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        points = []
    if not isinstance(points, list) or len(points) < 2:
        raise RuntimeError('curve-profile points must contain at least two points')
    curve_data = bpy.data.curves.new('ProfileCurve', 'CURVE')
    curve_data.dimensions = '3D'
    curve_data.resolution_u = max(1, min(24, int(PARAMS.get('resolution', 3))))
    curve_data.bevel_depth = max(0.005, min(1.0, float(PARAMS.get('radius', 0.06))))
    curve_data.use_fill_caps = True
    profile = str(PARAMS.get('profile', 'round')).lower()
    curve_data.bevel_resolution = 0 if profile == 'square' else max(0, min(8, int(PARAMS.get('resolution', 3))))
    spline = curve_data.splines.new('BEZIER')
    spline.bezier_points.add(len(points) - 1)
    for point, value in zip(spline.bezier_points, points):
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise RuntimeError('curve-profile points must be [x,y,z] triples')
        point.co = tuple(float(item) for item in value)
        point.handle_left_type = 'AUTO'
        point.handle_right_type = 'AUTO'
    curve = bpy.data.objects.new('Profile_Curve', curve_data)
    MODEL.objects.link(curve)
    curve.data.materials.append(METAL)
    curve['polyKitProfile'] = profile
    curve['polyKitPointCount'] = len(points)
    bpy.context.view_layer.objects.active = curve
    curve.select_set(True)
    bpy.ops.object.convert(target='MESH')
    objects = [curve]
    metadata.update({'pointCount': len(points), 'profile': profile, 'bevelRadius': curve_data.bevel_depth})
elif OPERATION == 'geometry-nodes':
    count = max(1, min(500, int(PARAMS.get('count', 12))))
    spacing = max(0.05, min(20.0, float(PARAMS.get('spacing', 0.8))))
    size = max(0.02, min(10.0, float(PARAMS.get('size', 0.35))))
    source = cube('Procedural_Instance_Source', (0.0, 0.0, size / 2.0), (size, size, size), METAL, MODEL, bevel=size * 0.08)
    modifier = source.modifiers.new('Procedural Instance Strip', 'NODES')
    try:
        group = bpy.data.node_groups.new('PolyKit Instance Strip', 'GeometryNodeTree')
        group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        nodes = group.nodes
        links = group.links
        group_input = nodes.new('NodeGroupInput')
        group_output = nodes.new('NodeGroupOutput')
        line = nodes.new('GeometryNodeMeshLine')
        line.mode = 'OFFSET'
        line.inputs['Count'].default_value = count
        line.inputs['Offset'].default_value = (spacing, 0.0, 0.0)
        instance_mesh = nodes.new('GeometryNodeMeshCube')
        instance_mesh.inputs['Size'].default_value = (size, size, size)
        instances = nodes.new('GeometryNodeInstanceOnPoints')
        realize = nodes.new('GeometryNodeRealizeInstances')
        links.new(line.outputs['Mesh'], instances.inputs['Points'])
        links.new(instance_mesh.outputs['Mesh'], instances.inputs['Instance'])
        links.new(instances.outputs['Instances'], realize.inputs['Geometry'])
        links.new(realize.outputs['Geometry'], group_output.inputs['Geometry'])
        modifier.node_group = group
        source['polyKitGeometryNodes'] = {'nodeGroup': group.name, 'count': count, 'spacing': spacing, 'size': size}
        metadata.update({'nodeGroup': group.name, 'count': count, 'spacing': spacing, 'size': size, 'instances': True})
    except Exception as exc:
        metadata['warnings'].append('geometry_nodes_setup_failed:' + str(exc))
        # Keep a useful deterministic result on Blender versions with a
        # changed Geometry Nodes socket API.
        for index in range(1, count):
            cube('Procedural_Instance_%03d' % index, (index * spacing, 0.0, size / 2.0), (size, size, size), METAL, MODEL, bevel=size * 0.08)
        metadata.update({'count': count, 'spacing': spacing, 'size': size, 'instances': False})
    objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
elif OPERATION == 'assembly':
    count = max(2, min(24, int(PARAMS.get('part_count', 3))))
    width = max(0.1, float(PARAMS.get('part_width', 1.0)))
    depth = max(0.1, float(PARAMS.get('part_depth', 0.6)))
    height = max(0.05, float(PARAMS.get('part_height', 0.3)))
    gap = max(0.0, float(PARAMS.get('gap', 0.01)))
    for index in range(count):
        part = cube('Assembly_Part_%02d' % (index + 1), ((width + gap) * index, 0.0, height / 2.0), (width, depth, height), WOOD, MODEL, bevel=0.025)
        part['polyKitPartId'] = 'part-%02d' % (index + 1)
        part['polyKitConnectorIn'] = 'connector-%02d-in' % (index + 1)
        part['polyKitConnectorOut'] = 'connector-%02d-out' % (index + 1)
        part['polyKitAssemblyGap'] = gap
        objects.append(part)
    for index in range(count - 1):
        connector = cube('Connector_%02d' % (index + 1), ((width + gap) * index + width + gap / 2.0, 0.0, height / 2.0), (max(gap, 0.005), depth * 0.35, height * 0.35), METAL, DETAILS)
        connector['polyKitConnectorBetween'] = ['part-%02d' % (index + 1), 'part-%02d' % (index + 2)]
    metadata.update({'partCount': count, 'gap': gap, 'connectors': count - 1, 'independentParts': True})
elif OPERATION in {'surface', 'lighting', 'deform', 'simulation-setup', 'npr'}:
    objects = import_mesh(INPUT_PATH)
    if not objects:
        objects = [cube('Generated_Base', (0.0, 0.0, 0.5), (1.5, 1.5, 1.0), WOOD)]
    if OPERATION == 'surface':
        kind = str(PARAMS.get('surface', 'wood')).lower()
        palette = {'wood': WOOD, 'metal': METAL, 'concrete': CONCRETE, 'glass': material('Production Glass', (0.22, 0.42, 0.62), 0.08, 0.0, 0.88), 'water': material('Production Water', (0.03, 0.16, 0.35), 0.05, 0.0, 0.72), 'fabric': material('Production Fabric', (0.32, 0.09, 0.12), 0.92)}
        mat = palette.get(kind, WOOD)
        roughness = max(0.02, min(1.0, float(PARAMS.get('roughness', 0.55))))
        bsdf = principled(mat)
        if bsdf and 'Roughness' in bsdf.inputs:
            bsdf.inputs['Roughness'].default_value = roughness
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        for node in list(nodes):
            if node.name.startswith('PolyKit Surface '):
                nodes.remove(node)
        texcoord = nodes.new('ShaderNodeTexCoord')
        texcoord.name = 'PolyKit Surface Coordinates'
        noise = nodes.new('ShaderNodeTexNoise')
        noise.name = 'PolyKit Surface Noise'
        noise.inputs['Scale'].default_value = max(0.1, min(50.0, float(PARAMS.get('scale', 3.0))))
        noise.inputs['Detail'].default_value = 3.0
        noise.inputs['Roughness'].default_value = 0.7
        bump = nodes.new('ShaderNodeBump')
        bump.name = 'PolyKit Surface Bump'
        bump.inputs['Strength'].default_value = 0.18 if kind in {'wood', 'concrete', 'fabric'} else 0.08
        bump.inputs['Distance'].default_value = 0.08
        links.new(texcoord.outputs['Generated'], noise.inputs['Vector'])
        links.new(noise.outputs['Fac'], bump.inputs['Height'])
        if bsdf:
            links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
        if kind in {'wood', 'concrete', 'fabric'} and bsdf:
            ramp = nodes.new('ShaderNodeValToRGB')
            ramp.name = 'PolyKit Surface Color'
            if kind == 'wood':
                ramp.color_ramp.elements[0].color = (0.025, 0.004, 0.001, 1.0)
                ramp.color_ramp.elements[1].color = (0.48, 0.16, 0.025, 1.0)
            elif kind == 'concrete':
                ramp.color_ramp.elements[0].color = (0.12, 0.13, 0.14, 1.0)
                ramp.color_ramp.elements[1].color = (0.52, 0.54, 0.57, 1.0)
            else:
                ramp.color_ramp.elements[0].color = (0.08, 0.015, 0.025, 1.0)
                ramp.color_ramp.elements[1].color = (0.42, 0.12, 0.16, 1.0)
            links.new(noise.outputs['Fac'], ramp.inputs['Fac'])
            links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
        for obj in objects:
            obj.data.materials.clear()
            obj.data.materials.append(mat)
            obj['polyKitSurface'] = kind
            obj['polyKitTextureScale'] = max(0.1, min(50.0, float(PARAMS.get('scale', 3.0))))
        metadata.update({'surface': kind, 'roughness': roughness, 'textureScale': float(PARAMS.get('scale', 3.0))})
    elif OPERATION == 'lighting':
        metadata.update({'lightingPreset': str(PARAMS.get('preset', 'three-point')), 'exposure': float(PARAMS.get('exposure', 0.0))})
        try:
            scene.view_settings.look = 'AgX - Medium High Contrast'
        except Exception:
            pass
        scene.view_settings.exposure = max(-8.0, min(8.0, float(PARAMS.get('exposure', 0.0))))
    elif OPERATION == 'deform':
        target = objects[0]
        modifier = target.modifiers.new('Controlled Simple Deform', 'SIMPLE_DEFORM')
        modifier.deform_method = str(PARAMS.get('mode', 'BEND')).upper()
        modifier.deform_axis = str(PARAMS.get('axis', 'Z')).upper()
        modifier.angle = math.radians(max(-180.0, min(180.0, float(PARAMS.get('angle_deg', 25.0)))))
        bpy.context.view_layer.objects.active = target
        target.select_set(True)
        try:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        except Exception as exc:
            metadata['warnings'].append('deform_apply_failed:' + str(exc))
        target['polyKitDeformation'] = {'mode': modifier.deform_method, 'axis': modifier.deform_axis, 'angleDeg': float(PARAMS.get('angle_deg', 25.0))}
        metadata.update({'mode': modifier.deform_method, 'axis': modifier.deform_axis, 'angleDeg': float(PARAMS.get('angle_deg', 25.0))})
    elif OPERATION == 'simulation-setup':
        simulation = str(PARAMS.get('simulation', 'cloth')).lower()
        quality = max(1, min(20, int(PARAMS.get('quality', 5))))
        target = objects[0]
        if simulation == 'cloth':
            modifier = target.modifiers.new('Cloth Simulation Setup', 'CLOTH')
            modifier.point_cache.frame_start = 1
            modifier.point_cache.frame_end = 120
            try:
                modifier.settings.quality = quality
            except Exception:
                pass
            target['polyKitSimulation'] = {'type': 'cloth', 'quality': quality, 'baked': bool(PARAMS.get('bake', False))}
        else:
            bpy.context.view_layer.objects.active = target
            target.select_set(True)
            try:
                bpy.ops.rigidbody.object_add()
                target.rigid_body.kinematic = False
                target['polyKitSimulation'] = {'type': 'rigid', 'quality': quality, 'baked': bool(PARAMS.get('bake', False))}
            except Exception as exc:
                metadata['warnings'].append('rigid_body_setup_failed:' + str(exc))
        bake_requested = bool(PARAMS.get('bake', False))
        bake_status = 'not_requested'
        if bake_requested:
            try:
                scene.frame_start = 1
                scene.frame_end = 120
                bpy.ops.ptcache.free_bake_all()
                bpy.ops.ptcache.bake_all(bake=True)
                bake_status = 'baked'
            except Exception as exc:
                bake_status = 'failed'
                metadata['warnings'].append('simulation_bake_failed:' + str(exc))
        metadata.update({'simulation': simulation, 'quality': quality, 'bakeRequested': bake_requested, 'bakeStatus': bake_status})
    elif OPERATION == 'npr':
        renderer = str(PARAMS.get('renderer', 'eevee')).lower()
        outline_width = max(0.001, min(0.2, float(PARAMS.get('outline_width', 0.018))))
        noise_scale = max(0.1, min(50.0, float(PARAMS.get('outline_noise_scale', 3.0))))
        wobble = max(0.0, min(0.2, float(PARAMS.get('outline_wobble', 0.0))))
        line_mode = str(PARAMS.get('line_mode', 'silhouette')).strip().lower()
        replace_material = PARAMS.get('replace_material', False)
        if isinstance(replace_material, str):
            replace_material = replace_material.strip().lower() not in {'', '0', 'false', 'no', 'off'}
        replace_material = bool(replace_material)
        if renderer == 'eevee':
            outline_group = build_eevee_outline_group(outline_width, noise_scale, wobble, BLACK)
            line_info = configure_structure_lines(line_mode)
            default_toon = build_eevee_toon_material()
            for obj in objects:
                if replace_material:
                    obj.data.materials.clear()
                    obj.data.materials.append(default_toon)
                    for polygon in obj.data.polygons:
                        polygon.material_index = 0
                    obj['polyKitNprMaterialPolicy'] = 'replaced_by_explicit_request'
                else:
                    _material_slots_with_toon_variant(
                        obj,
                        lambda base, source: build_eevee_toon_material(base, source),
                    )
                build_eevee_outline(obj, outline_width, noise_scale, wobble, BLACK, outline_group)
                obj['polyKitNprRenderer'] = 'eevee'
            try:
                scene.render.engine = 'BLENDER_EEVEE_NEXT'
            except Exception:
                scene.render.engine = 'BLENDER_EEVEE'
            metadata.update({
                'renderer': 'eevee',
                'outlineWidth': outline_width,
                'outlineNoiseScale': noise_scale,
                'outlineWobble': wobble,
                'outline': 'geometry-nodes-inverted-hull',
                'toonFill': 'per-material-diffuse-shader-to-rgb-constant-ramp-emission',
                'outlineNodeGroups': [outline_group.name],
                'lineMode': line_info,
                'materialPolicy': 'replaced_by_explicit_request' if replace_material else 'preserved_with_toon_variant',
            })
        elif renderer == 'cycles':
            if not hasattr(bpy.types, 'ShaderNodeRaycast'):
                raise RuntimeError('Cycles NPR requires Blender 5.2 ShaderNodeRaycast; use renderer=eevee as fallback')
            minimum, maximum = bounds(presentation_objects(objects))
            extent = max(float((maximum - minimum).length), 1.0)
            ray_length = max(1.0, min(500.0, float(PARAMS.get('ray_length', extent * 8.0))))
            line_info = configure_structure_lines(line_mode)
            cycles_material, sample_group, look_group = build_cycles_npr_material(outline_width, ray_length)
            for obj in objects:
                if replace_material:
                    obj.data.materials.clear()
                    obj.data.materials.append(cycles_material)
                    for polygon in obj.data.polygons:
                        polygon.material_index = 0
                    obj['polyKitNprMaterialPolicy'] = 'replaced_by_explicit_request'
                else:
                    _material_slots_with_toon_variant(
                        obj,
                        lambda base, source: build_cycles_npr_material(outline_width, ray_length, base, source)[0],
                    )
                obj['polyKitNprRenderer'] = 'cycles'
            scene.render.engine = 'CYCLES'
            try:
                scene.cycles.samples = max(16, min(128, int(PARAMS.get('samples', 32))))
                scene.cycles.use_denoising = True
            except Exception:
                pass
            metadata.update({
                'renderer': 'cycles',
                'outlineWidth': outline_width,
                'rayLength': ray_length,
                'outline': 'four-direction-shader-raycast',
                'raySampleGroup': sample_group.name,
                'rayLookGroup': look_group.name,
                'cyclesFill': look_group.get('polyKitNprCyclesFill', 'unknown'),
                'rayDirections': ['+X', '-X', '+Y', '-Y'],
                'raycastOnlyLocal': True,
                'samples': int(PARAMS.get('samples', 32)),
                'lineMode': line_info,
                'materialPolicy': 'replaced_by_explicit_request' if replace_material else 'preserved_with_toon_variant',
            })
        else:
            raise RuntimeError('NPR renderer must be eevee or cycles')
else:
    raise RuntimeError('unsupported production operation: ' + OPERATION)

if OPERATION != 'geometry-report':
    objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH' or obj.type == 'CURVE']
    camera = add_presentation(objects)
    metadata['objectCount'] = len(objects)
    metadata['meshObjectCount'] = len([obj for obj in objects if obj.type == 'MESH'])
    root = pathlib.Path(tempfile.mkdtemp(prefix='polykit_blender_production_'))
    glb_path = root / (SCENE_NAME + '.glb')
    blend_path = root / (SCENE_NAME + '.blend')
    preview_path = root / (SCENE_NAME + '.png')
    scene.render.filepath = str(preview_path)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    # Render while the renderer-native graph is still active. The GLB export
    # below intentionally flattens NPR materials for portability and must not
    # change the evidence image into a PBR fallback.
    if RENDER_PREVIEW:
        bpy.ops.render.render(write_still=True)
        metadata['renderEvidence'] = {
            'schemaVersion': 1,
            'engine': scene.render.engine,
            'camera': camera.name,
            'preview': {'path': str(preview_path), 'metrics': render_metrics(preview_path)},
        }
    if OPERATION == 'npr' and not replace_material:
        for obj in objects:
            indices = obj.get('polyKitNprOriginalMaterialIndices')
            authored_count = int(obj.get('polyKitNprAuthoredSlotCount', 0) or 0)
            if indices is None or authored_count <= 0:
                continue
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
            for obj in objects
            if obj.type == 'MESH'
            for slot in obj.material_slots
            if slot.material is not None
        }
        for mat in used_materials:
            bsdf = principled(mat)
            if bsdf is None:
                continue
            base = bsdf.inputs.get('Base Color')
            color = tuple(float(channel) for channel in (base.default_value[:3] if base is not None else mat.diffuse_color[:3]))
            roughness = float(bsdf.inputs.get('Roughness').default_value) if bsdf.inputs.get('Roughness') else 0.75
            metallic = float(bsdf.inputs.get('Metallic').default_value) if bsdf.inputs.get('Metallic') else 0.0
            mat.node_tree.nodes.clear()
            output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
            flat = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
            flat.name = 'Principled BSDF'
            flat.inputs['Base Color'].default_value = (*color, 1.0)
            flat.inputs['Roughness'].default_value = roughness
            flat.inputs['Metallic'].default_value = metallic
            mat.node_tree.links.new(flat.outputs['BSDF'], output.inputs['Surface'])
    if OPERATION == 'surface':
        # Blender's glTF exporter cannot serialize procedural ColorRamp/Noise
        # links as a portable PBR base color and otherwise falls back to white.
        # Keep the authored node graph in the .blend sidecar, but export the
        # calibrated Principled default so downstream GLB consumers retain the
        # declared substrate color.
        for mat in bpy.data.materials:
            bsdf = principled(mat)
            base = bsdf.inputs.get('Base Color') if bsdf else None
            if not base:
                continue
            for link in list(mat.node_tree.links):
                if link.to_node == bsdf and link.to_socket == base:
                    mat.node_tree.links.remove(link)
    bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format='GLB', export_materials='EXPORT', export_apply=True)
    def b64(path):
        return base64.b64encode(path.read_bytes()).decode('ascii') if path.exists() else ''
    RESULT = {'glb_b64': b64(glb_path), 'blend_b64': b64(blend_path), 'preview_b64': b64(preview_path) if RENDER_PREVIEW else '', 'metadata': metadata}
else:
    RESULT = {'metadata': metadata}
result = RESULT
'''
    return (
        script.replace('__OPERATION__', repr(operation))
        .replace('__PARAMS_JSON__', repr(json.dumps(dict(params), separators=(',', ':'))))
        .replace('__INPUT_PATH__', repr(input_path))
        .replace('__INPUT_B64__', repr(input_b64))
        .replace('__SCENE_NAME__', repr(scene_name))
        .replace('__RENDER_PREVIEW__', 'True' if render_preview else 'False')
    )


def _report_script(input_path: str, input_b64: str | None, params: Mapping[str, Any]) -> str:
    script = r'''
import base64
import json
import pathlib
import tempfile
import zlib
import bpy
import bmesh

INPUT_PATH = __INPUT_PATH__
INPUT_B64 = __INPUT_B64__
PARAMS = json.loads(__PARAMS_JSON__)
if not INPUT_PATH:
    raise RuntimeError('geometry-report requires a mesh input')
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for collection in list(bpy.data.collections):
    bpy.data.collections.remove(collection)
if INPUT_B64 and not pathlib.Path(INPUT_PATH).is_file():
    transferred = pathlib.Path(tempfile.gettempdir()) / ('polykit_report_input' + pathlib.Path(INPUT_PATH).suffix.lower())
    transferred.write_bytes(zlib.decompress(base64.b64decode(INPUT_B64)))
    INPUT_PATH = str(transferred)
suffix = pathlib.Path(INPUT_PATH).suffix.lower()
if suffix in {'.glb', '.gltf'}:
    bpy.ops.import_scene.gltf(filepath=INPUT_PATH)
elif suffix == '.obj':
    bpy.ops.wm.obj_import(filepath=INPUT_PATH)
elif suffix == '.ply':
    bpy.ops.wm.ply_import(filepath=INPUT_PATH)
elif suffix == '.stl':
    bpy.ops.wm.stl_import(filepath=INPUT_PATH)
else:
    raise RuntimeError('unsupported mesh format: ' + suffix)
objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
report = {'version': 1, 'blenderVersion': bpy.app.version_string, 'input': pathlib.Path(INPUT_PATH).name, 'objects': len(objects), 'findings': [], 'status': 'pass'}
for obj in objects:
    mesh = obj.data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        # glTF commonly splits vertices for hard/normal seams. Merge only
        # coincident report vertices so those representation seams are not
        # misreported as open topology; authored positional gaps remain.
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        bm.normal_update()
        finding = {'object': obj.name, 'vertices': len(bm.verts), 'edges': len(bm.edges), 'faces': len(bm.faces), 'nonManifoldEdges': 0, 'boundaryEdges': 0, 'looseVertices': 0, 'zeroAreaFaces': 0}
        if PARAMS.get('check_non_manifold', True):
            finding['nonManifoldEdges'] = sum(1 for edge in bm.edges if len(edge.link_faces) > 2)
            finding['boundaryEdges'] = sum(1 for edge in bm.edges if len(edge.link_faces) == 1)
        if PARAMS.get('check_loose', True):
            finding['looseVertices'] = sum(1 for vert in bm.verts if not vert.link_faces)
        if PARAMS.get('check_normals', True):
            finding['zeroAreaFaces'] = sum(1 for face in bm.faces if face.calc_area() <= 1e-9)
        if finding['nonManifoldEdges'] or finding['boundaryEdges'] or finding['looseVertices'] or finding['zeroAreaFaces']:
            report['status'] = 'needs_review'
        report['findings'].append(finding)
    finally:
        bm.free()
report['checks'] = {'nonManifold': bool(PARAMS.get('check_non_manifold', True)), 'loose': bool(PARAMS.get('check_loose', True)), 'normals': bool(PARAMS.get('check_normals', True))}
root = pathlib.Path(tempfile.mkdtemp(prefix='polykit_geometry_report_'))
glb_path = root / 'geometry-report.glb'
report_path = root / 'geometry-report.json'
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
bpy.ops.wm.save_as_mainfile(filepath=str(root / 'geometry-report.blend'))
bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format='GLB', export_materials='EXPORT', export_apply=True)
RESULT = {
    'glb_b64': base64.b64encode(glb_path.read_bytes()).decode('ascii'),
    'report_b64': base64.b64encode(report_path.read_bytes()).decode('ascii'),
    'report_json': json.dumps(report, ensure_ascii=False, separators=(',', ':')),
}
result = RESULT
'''
    return script.replace('__INPUT_PATH__', repr(input_path)).replace('__INPUT_B64__', repr(input_b64)).replace('__PARAMS_JSON__', repr(json.dumps(dict(params), separators=(',', ':'))))


def _send_blender_code(host: str, port: int, code: str, timeout: float = 900.0) -> dict[str, Any]:
    request = json.dumps({'type': 'execute', 'code': code, 'strict_json': True}).encode('utf-8') + b'\0'
    chunks: list[bytes] = []
    with socket.create_connection((host, port), timeout=15.0) as connection:
        connection.settimeout(timeout)
        connection.sendall(request)
        while True:
            chunk = connection.recv(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            if b'\0' in chunk:
                break
    raw = b''.join(chunks).split(b'\0', 1)[0]
    if not raw:
        raise RuntimeError('Blender bridge returned an empty response')
    response = json.loads(raw.decode('utf-8'))
    if response.get('status') != 'ok':
        raise RuntimeError(str(response.get('message') or response.get('error') or 'unknown Blender bridge error'))
    result = response.get('result')
    if not isinstance(result, dict):
        raise RuntimeError('Blender bridge returned no result object')
    return result


def main() -> None:
    try:
        payload = json.loads(sys.stdin.readline())
    except json.JSONDecodeError as exc:
        error(f'blender-production: invalid process payload ({exc})')
        return
    params = payload.get('params') if isinstance(payload.get('params'), Mapping) else {}
    input_data = payload.get('input') if isinstance(payload.get('input'), Mapping) else {}
    workspace_dir = Path(str(payload.get('workspaceDir') or '.')).expanduser().resolve()
    node_id = str(params.get('_node_id') or params.get('operation') or 'surface')
    aliases = {'opening': 'opening', 'array-stairs': 'array-stairs', 'curve-profile': 'curve-profile', 'geometry-nodes': 'geometry-nodes', 'assembly': 'assembly', 'surface': 'surface', 'lighting': 'lighting', 'deform': 'deform', 'simulation-setup': 'simulation-setup', 'npr': 'npr', 'geometry-report': 'geometry-report'}
    operation = aliases.get(node_id)
    if operation is None:
        error(f'blender-production: unsupported node operation {node_id!r}')
        return
    input_path = str(input_data.get('filePath') or '') or None
    if operation in {'surface', 'lighting', 'deform', 'simulation-setup', 'npr', 'geometry-report'} and input_path and not Path(input_path).is_file():
        error(f'blender-production: input mesh not found: {input_path}')
        return
    if operation == 'geometry-report' and not input_path:
        error('blender-production: geometry-report requires a connected mesh input')
        return
    input_b64 = None
    if input_path:
        input_file = Path(input_path)
        if input_file.stat().st_size > 64 * 1024 * 1024:
            error('blender-production: remote mesh transfer is limited to 64 MiB; use a shared workspace for larger assets')
            return
        # Remote Blender bridges commonly impose a request-size limit. Compress
        # embedded mesh bytes before placing them in the JSON code payload while
        # retaining the existing bounded 64 MiB source guard.
        input_b64 = base64.b64encode(zlib.compress(input_file.read_bytes(), level=9)).decode('ascii')
    render_preview = _bool(params, 'render_preview', True)
    scene_name = _slug(str(params.get('scene_name') or f'production_{operation}_{uuid.uuid4().hex[:8]}'))
    try:
        port = int(params.get('blender_port') or os.environ.get('POLYKIT_BLENDER_MCP_PORT') or os.environ.get('BLENDER_MCP_PORT') or 9876)
    except (TypeError, ValueError):
        error('blender-production: Blender port must be an integer')
        return
    host = str(params.get('blender_host') or os.environ.get('POLYKIT_BLENDER_MCP_HOST') or os.environ.get('BLENDER_MCP_HOST') or '127.0.0.1')
    output_dir = workspace_dir / 'Workflows'
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        progress(5, f'Connecting to Blender bridge at {host}:{port}…')
        code = _report_script(input_path, input_b64, params) if operation == 'geometry-report' else _scene_script(operation, params, input_path, input_b64, scene_name, render_preview)
        result = _send_blender_code(host, port, code)
        if operation == 'geometry-report':
            glb_b64 = str(result.get('glb_b64') or '')
            report_b64 = str(result.get('report_b64') or '')
            report_json = str(result.get('report_json') or '')
            if not glb_b64 or not report_b64 or not report_json:
                raise RuntimeError('Blender did not return a complete geometry report')
            glb_path = output_dir / f'{scene_name}.glb'
            report_path = output_dir / f'{scene_name}.json'
            glb_path.write_bytes(base64.b64decode(glb_b64, validate=True))
            report_path.write_bytes(base64.b64decode(report_b64, validate=True))
            metadata = {'operation': operation, 'report': json.loads(report_json)}
            progress(100, 'Geometry report ready')
            emit({'type': 'done', 'result': {'filePath': str(glb_path), 'sidecars': [str(report_path)], 'metadata': metadata}})
            return
        glb_b64 = str(result.get('glb_b64') or '')
        if not glb_b64:
            raise RuntimeError('Blender did not return a GLB artifact')
        glb_path = output_dir / f'{scene_name}.glb'
        glb_path.write_bytes(base64.b64decode(glb_b64, validate=True))
        sidecars: list[str] = []
        for key, suffix in (('blend_b64', '.blend'), ('preview_b64', '.png')):
            encoded = str(result.get(key) or '')
            if encoded:
                path = output_dir / f'{scene_name}{suffix}'
                path.write_bytes(base64.b64decode(encoded, validate=True))
                sidecars.append(str(path))
        metadata = result.get('metadata') if isinstance(result.get('metadata'), Mapping) else {}
        progress(100, f'{operation} artifact ready')
        emit({'type': 'done', 'result': {'filePath': str(glb_path), 'sidecars': sidecars, 'metadata': dict(metadata)}})
    except (OSError, ValueError, RuntimeError, socket.timeout, ConnectionError) as exc:
        error(f'blender-production: {exc}. Start the official Blender bridge and keep the input/workspace writable.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        error(f'blender-production: {exc}')
