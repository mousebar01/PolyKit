"""Parameterized cabin builder with geometry-backed attachment validation.

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


def _scene_script(
    scene_name: str,
    brief: str,
    width: int,
    height: int,
    render_preview: bool,
    config: Mapping[str, float],
    building_spec: Mapping[str, Any],
) -> str:
    script = r'''
import base64
import json
import math
import pathlib
import tempfile
import bpy
from mathutils import Vector

SCENE_NAME = __SCENE_NAME__
SCENE_BRIEF = __SCENE_BRIEF__
RENDER_WIDTH = __WIDTH__
RENDER_HEIGHT = __HEIGHT__
RENDER_PREVIEW = __RENDER_PREVIEW__
CONFIG = json.loads(__CONFIG_JSON__)
BUILDING_SPEC = json.loads(__BUILD_SPEC_JSON__)

scene = bpy.context.scene
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for collection in list(bpy.data.collections):
    bpy.data.collections.remove(collection)
scene.name = SCENE_NAME
scene['polyKitPreset'] = 'winter_cabin_v2'
scene['polyKitSource'] = 'blender-mcp-official'
scene['polyKitBrief'] = SCENE_BRIEF
scene['polyKitBuildSpec'] = json.dumps(BUILDING_SPEC, separators=(',', ':'))
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

def material(name, color, roughness=0.75, metallic=0.0, emission=None):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = (*color, 1.0)
        bsdf.inputs['Roughness'].default_value = roughness
        bsdf.inputs['Metallic'].default_value = metallic
        if emission:
            key = 'Emission Color' if 'Emission Color' in bsdf.inputs else 'Emission'
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = (*emission[0], 1.0)
            if 'Emission Strength' in bsdf.inputs:
                bsdf.inputs['Emission Strength'].default_value = emission[1]
    return mat

WOOD = material('Cabin Wood', (0.18, 0.055, 0.015), 0.82)
WOOD_LIGHT = material('Pale Wood', (0.42, 0.18, 0.05), 0.78)
WOOD_DARK = material('Dark Timber', (0.055, 0.014, 0.005), 0.9)
METAL = material('Stove Iron', (0.025, 0.03, 0.035), 0.32, 0.65)
FABRIC = material('Wool', (0.48, 0.43, 0.36), 0.95)
FIRE = material('Fire', (0.2, 0.025, 0.003), 0.35, 0.0, ((1.0, 0.15, 0.02), 5.0))

def move(obj, target):
    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    target.objects.link(obj)

def cube(name, location, dimensions, mat, role, target=ARCH, rotation=(0.0, 0.0, 0.0), bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    obj['polyKitRole'] = role
    obj['polyKitSemanticName'] = name
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
scene['polyKitConstructionValidation'] = json.dumps(construction_validation, separators=(',', ':'))
if construction_errors:
    raise RuntimeError('construction validation failed: ' + ', '.join(construction_errors))

# Camera and lighting are presentation only; construction correctness above is
# camera-independent.
def light(name, kind, location, energy, color, size=2.0):
    data = bpy.data.lights.new(name=name, type=kind)
    data.energy = energy
    data.color = color
    if hasattr(data, 'shadow_soft_size'):
        data.shadow_soft_size = size
    obj = bpy.data.objects.new(name, data)
    LIGHTING.objects.link(obj)
    obj.location = location
    return obj

key = light('Warm_Key', 'AREA', (-W*0.15, -D*0.3, H*0.8), 260.0, (1.0, 0.5, 0.25), 4.0)
look_at(key, (0.0, 0.0, 1.5))
fill = light('Cool_Fill', 'AREA', (W*0.35, D*0.35, H*0.75), 220.0, (0.5, 0.68, 1.0), 3.0)
look_at(fill, (0.0, 0.0, 1.7))

def camera(name, location, target, lens, view):
    data = bpy.data.cameras.new(name)
    obj = bpy.data.objects.new(name, data)
    LIGHTING.objects.link(obj)
    obj.location = location
    obj.data.lens = lens
    obj['polyKitInspectionView'] = view
    look_at(obj, target)
    return obj

entry_camera = camera(
    'PresentationCamera',
    (-W*0.42, -D*0.72, H*0.48),
    (0.0, D*0.05, H*0.32),
    34.0,
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
scene.camera = entry_camera
try:
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
except Exception:
    scene.render.engine = 'BLENDER_EEVEE'
scene.render.resolution_x = RENDER_WIDTH
scene.render.resolution_y = RENDER_HEIGHT
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.world.color = (0.008, 0.012, 0.025)

root = pathlib.Path(tempfile.mkdtemp(prefix='polykit_blender_cabin_v2_'))
glb_path = root / (SCENE_NAME + '.glb')
blend_path = root / (SCENE_NAME + '.blend')
preview_path = root / (SCENE_NAME + '.png')
scene.render.filepath = str(preview_path)
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format='GLB', export_materials='EXPORT')
preview_view_paths = {}
if RENDER_PREVIEW:
    bpy.ops.render.render(write_still=True)
    for view_name, view_camera in (
        ('entry', entry_camera),
        ('hearth', hearth_camera),
        ('exterior', exterior_camera),
    ):
        scene.camera = view_camera
        view_path = root / (SCENE_NAME + '_view_' + view_name + '.png')
        scene.render.filepath = str(view_path)
        bpy.ops.render.render(write_still=True)
        preview_view_paths[view_name] = view_path
    scene.camera = entry_camera
    scene.render.filepath = str(preview_path)

def b64(path):
    return base64.b64encode(path.read_bytes()).decode('ascii') if path.exists() else ''

result = {
    'scene_name': SCENE_NAME,
    'preset': 'winter_cabin_v2',
    'object_count': len([obj for obj in scene.objects if obj.type == 'MESH']),
    'build_spec': BUILDING_SPEC,
    'construction_validation': construction_validation,
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
        .replace("__CONFIG_JSON__", repr(json.dumps(dict(config), separators=(",", ":"))))
        .replace("__BUILD_SPEC_JSON__", repr(json.dumps(dict(building_spec), separators=(",", ":"))))
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
    workspace_dir = Path(str(payload.get("workspaceDir") or ".")).expanduser().resolve()
    preset = str(params.get("preset") or "cabin")
    if preset != "cabin":
        error(f"blender-scene: unsupported preset '{preset}'")
        return

    scene_name = _slug(str(params.get("scene_name") or "winter_cabin_reference"))
    brief = str(input_data.get("text") or "A compact winter cabin interior with warm firelight.")[:2000]
    config = _cabin_config(params)
    building_spec = _building_spec(config)
    try:
        render_width = max(320, min(1600, int(params.get("render_width") or 768)))
        render_height = max(240, min(1200, int(params.get("render_height") or 512)))
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
            _scene_script(scene_name, brief, render_width, render_height, render_preview, config, building_spec),
        )
        progress(78, "Receiving validated cabin artifacts…")
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
            for view_name in ("entry", "hearth", "exterior"):
                encoded = str(preview_views.get(view_name) or "")
                if not encoded:
                    continue
                view_path = output_dir / f"{scene_name}_view_{view_name}.png"
                view_path.write_bytes(base64.b64decode(encoded, validate=True))
                sidecars.append(str(view_path))
        validation = result.get("construction_validation") if isinstance(result.get("construction_validation"), Mapping) else {}
        emit({"type": "log", "message": f"Cabin v2 construction validation: {validation.get('status', 'unknown')}"})
        progress(100, "Validated cabin ready")
        emit({
            "type": "done",
            "result": {
                "filePath": str(glb_path),
                "sidecars": sidecars,
                "metadata": {
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
