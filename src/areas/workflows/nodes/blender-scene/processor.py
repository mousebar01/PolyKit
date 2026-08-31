"""Blender scene entrypoint with stable provenance and scoped part repair.

The existing v2 cabin builder remains the full-build implementation. This
entrypoint adds two things without creating another execution runtime:

* full builds export stable BuildSpec part ids as glTF node extras/names;
* ``repair-parts`` consumes an existing GLB and applies a bounded translation
  correction only to explicitly selected BuildSpec parts.

WorkflowRun still owns lifecycle and the World document remains immutable here.
"""
from __future__ import annotations

import io
import json
import math
import os
import socket
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import processor_v2 as base


_PART_NAME_MAP = {
    "Cabin_Floor": "floor",
    "Cabin_Wall_Left": "left-wall",
    "Cabin_Wall_Right": "right-wall",
    "Cabin_Wall_Back": "back-wall",
    "Cabin_Roof_Left": "left-roof",
    "Cabin_Roof_Right": "right-roof",
}


def _patched_scene_script(*args: Any, **kwargs: Any) -> str:
    """Add stable BuildSpec provenance to the existing full-build script."""

    script = base._scene_script(*args, **kwargs)
    mapping_json = json.dumps(_PART_NAME_MAP, separators=(",", ":"))
    script = script.replace(
        "BUILDING_SPEC = json.loads(__BUILD_SPEC_JSON__)\n",
        "BUILDING_SPEC = json.loads(__BUILD_SPEC_JSON__)\n"
        f"PART_NAME_MAP = json.loads({mapping_json!r})\n",
        1,
    )
    script = script.replace(
        "    obj['polyKitSemanticName'] = name\n",
        "    obj['polyKitSemanticName'] = name\n"
        "    part_id = PART_NAME_MAP.get(name)\n"
        "    if part_id:\n"
        "        obj['polyKitProvenanceVersion'] = 1\n"
        "        obj['polyKitProvenanceKind'] = 'build-part'\n"
        "        obj['polyKitPartId'] = part_id\n"
        "        obj['polyKitBuildingId'] = BUILDING_SPEC.get('id') or SCENE_NAME\n"
        "        obj.name = part_id\n",
        1,
    )
    script = script.replace(
        "bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format='GLB', export_materials='EXPORT')",
        "bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format='GLB', export_materials='EXPORT', export_extras=True)",
        1,
    )
    return script


def _ids(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in result:
            result.append(item.strip())
    return result


def _repair_script(
    *,
    source_path: str,
    scene_name: str,
    building_spec: Mapping[str, Any],
    part_ids: Sequence[str],
    attachment_ids: Sequence[str],
    render_preview: bool,
) -> str:
    """Return Blender code for a bounded translation-only part repair."""

    script = r'''
import base64
import json
import math
import pathlib
import tempfile
import bpy
from mathutils import Vector

SOURCE_PATH = __SOURCE_PATH__
SCENE_NAME = __SCENE_NAME__
BUILDING_SPEC = json.loads(__BUILD_SPEC_JSON__)
PART_IDS = set(json.loads(__PART_IDS_JSON__))
ATTACHMENT_IDS = set(json.loads(__ATTACHMENT_IDS_JSON__))
RENDER_PREVIEW = __RENDER_PREVIEW__

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
for collection in list(bpy.data.collections):
    bpy.data.collections.remove(collection)

bpy.ops.import_scene.gltf(filepath=SOURCE_PATH)
scene = bpy.context.scene
scene.name = SCENE_NAME
scene['polyKitRepairStrategy'] = 'translation-anchor-snap-v1'
scene['polyKitBuildSpec'] = json.dumps(BUILDING_SPEC, separators=(',', ':'))


def canonical_to_blender(value):
    return Vector((value[0], -value[2], value[1]))


def closest_point_world(obj, point):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    matrix = evaluated.matrix_world
    local_point = matrix.inverted() @ point
    hit, nearest, _normal, _index = evaluated.closest_point_on_mesh(local_point)
    if not hit:
        return None
    return matrix @ nearest


def part_id_for(obj):
    value = obj.get('polyKitPartId')
    if isinstance(value, str) and value:
        return value
    # Compatibility with old artifacts. New artifacts should always carry the
    # custom property, but exact stable node names make one migration possible.
    if obj.name in PART_IDS:
        return obj.name
    return None


objects = {}
for obj in scene.objects:
    if obj.type != 'MESH':
        continue
    part_id = part_id_for(obj)
    if part_id:
        objects.setdefault(part_id, []).append(obj)

missing = sorted(part_id for part_id in PART_IDS if not objects.get(part_id))
if missing:
    raise RuntimeError('repair provenance missing for parts: ' + ', '.join(missing))

anchors = [item for item in BUILDING_SPEC.get('anchors', []) if isinstance(item, dict)]
anchors_by_part = {}
for anchor in anchors:
    part_id = anchor.get('partId') or anchor.get('part_id')
    position = anchor.get('position')
    if isinstance(part_id, str) and isinstance(position, list) and len(position) == 3:
        anchors_by_part.setdefault(part_id, []).append(anchor)

repair_results = []
for part_id in sorted(PART_IDS):
    part_objects = objects.get(part_id, [])
    part_anchors = anchors_by_part.get(part_id, [])
    if len(part_objects) != 1:
        raise RuntimeError('repair requires exactly one mesh for part: ' + part_id)
    if not part_anchors:
        raise RuntimeError('repair has no BuildSpec anchor for part: ' + part_id)
    obj = part_objects[0]
    corrections = []
    before = []
    for anchor in part_anchors:
        target = canonical_to_blender(anchor['position'])
        nearest = closest_point_world(obj, target)
        if nearest is None:
            continue
        delta = target - nearest
        corrections.append(delta)
        before.append(delta.length)
    if not corrections:
        raise RuntimeError('repair could not measure part surface: ' + part_id)
    correction = sum(corrections, Vector((0.0, 0.0, 0.0))) / len(corrections)
    obj.location += correction
    obj['polyKitProvenanceVersion'] = 1
    obj['polyKitProvenanceKind'] = 'build-part'
    obj['polyKitPartId'] = part_id
    obj['polyKitBuildingId'] = BUILDING_SPEC.get('id') or SCENE_NAME
    obj['polyKitLastRepair'] = 'translation-anchor-snap-v1'
    obj.name = part_id
    repair_results.append({
        'part_id': part_id,
        'translation': [float(correction.x), float(correction.y), float(correction.z)],
        'max_anchor_surface_gap_before': max(before) if before else None,
    })

# Validate only the selected relationships, using the same anchor/surface
# contract as the full builder. This does not change World/BuildSpec facts.
anchor_index = {item.get('id'): item for item in anchors if isinstance(item.get('id'), str)}
attachment_results = []
for attachment in BUILDING_SPEC.get('attachments', []):
    if not isinstance(attachment, dict):
        continue
    attachment_id = str(attachment.get('id') or '')
    if ATTACHMENT_IDS and attachment_id not in ATTACHMENT_IDS:
        continue
    source = anchor_index.get(attachment.get('from'))
    target = anchor_index.get(attachment.get('to'))
    if not isinstance(source, dict) or not isinstance(target, dict):
        raise RuntimeError('repair attachment anchors missing: ' + attachment_id)
    source_part = source.get('partId') or source.get('part_id')
    target_part = target.get('partId') or target.get('part_id')
    if not isinstance(source_part, str) or not isinstance(target_part, str):
        raise RuntimeError('repair attachment part ids missing: ' + attachment_id)
    if len(objects.get(source_part, [])) != 1 or len(objects.get(target_part, [])) != 1:
        raise RuntimeError('repair attachment provenance unresolved: ' + attachment_id)
    point_a = canonical_to_blender(source['position'])
    point_b = canonical_to_blender(target['position'])
    nearest_a = closest_point_world(objects[source_part][0], point_a)
    nearest_b = closest_point_world(objects[target_part][0], point_b)
    if nearest_a is None or nearest_b is None:
        raise RuntimeError('repair attachment surface unresolved: ' + attachment_id)
    source_distance = (nearest_a - point_a).length
    target_distance = (nearest_b - point_b).length
    anchor_gap = (point_a - point_b).length
    tolerance = float(attachment.get('tolerance') or 0.05)
    measured = max(source_distance, target_distance, anchor_gap)
    passed = math.isfinite(measured) and measured <= tolerance
    attachment_results.append({
        'id': attachment_id,
        'source_part': source_part,
        'target_part': target_part,
        'measured': measured,
        'tolerance': tolerance,
        'status': 'pass' if passed else 'fail',
    })
    if not passed:
        raise RuntimeError('repair did not satisfy attachment: ' + attachment_id)

root = pathlib.Path(tempfile.mkdtemp(prefix='polykit_blender_repair_parts_'))
glb_path = root / (SCENE_NAME + '.glb')
blend_path = root / (SCENE_NAME + '.blend')
preview_path = root / (SCENE_NAME + '.png')
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
bpy.ops.export_scene.gltf(
    filepath=str(glb_path),
    export_format='GLB',
    export_materials='EXPORT',
    export_extras=True,
)
if RENDER_PREVIEW:
    cameras = [obj for obj in scene.objects if obj.type == 'CAMERA']
    if cameras:
        scene.camera = cameras[0]
        scene.render.resolution_x = 768
        scene.render.resolution_y = 512
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = 'PNG'
        scene.render.filepath = str(preview_path)
        bpy.ops.render.render(write_still=True)


def b64(path):
    return base64.b64encode(path.read_bytes()).decode('ascii') if path.exists() else ''

result = {
    'glb_b64': b64(glb_path),
    'blend_b64': b64(blend_path),
    'preview_b64': b64(preview_path) if RENDER_PREVIEW else '',
    'repair_validation': {
        'status': 'pass',
        'strategy': 'translation-anchor-snap-v1',
        'parts': repair_results,
        'attachments': attachment_results,
    },
}
'''
    return (
        script.replace("__SOURCE_PATH__", repr(source_path))
        .replace("__SCENE_NAME__", repr(scene_name))
        .replace("__BUILD_SPEC_JSON__", repr(json.dumps(dict(building_spec), separators=(",", ":"))))
        .replace("__PART_IDS_JSON__", repr(json.dumps(list(part_ids), separators=(",", ":"))))
        .replace("__ATTACHMENT_IDS_JSON__", repr(json.dumps(list(attachment_ids), separators=(",", ":"))))
        .replace("__RENDER_PREVIEW__", "True" if render_preview else "False")
    )


def _repair_main(payload: Mapping[str, Any]) -> None:
    params = payload.get("params") if isinstance(payload.get("params"), Mapping) else {}
    input_data = payload.get("input") if isinstance(payload.get("input"), Mapping) else {}
    source = str(input_data.get("filePath") or "").strip()
    if not source or not Path(source).is_file():
        base.error("blender-scene repair-parts requires an existing mesh input")
        return
    building_spec = params.get("building_spec")
    if not isinstance(building_spec, Mapping):
        base.error("blender-scene repair-parts requires building_spec")
        return
    part_ids = _ids(params.get("part_ids"))
    attachment_ids = _ids(params.get("attachment_ids"))
    if not part_ids:
        base.error("blender-scene repair-parts requires at least one explicit part id")
        return
    scene_name = base._slug(str(params.get("scene_name") or f"{building_spec.get('id') or 'building'}_repair"))
    render_preview = bool(params.get("render_preview", True))
    workspace_dir = Path(str(payload.get("workspaceDir") or ".")).expanduser().resolve()
    output_dir = workspace_dir / "Workflows"
    output_dir.mkdir(parents=True, exist_ok=True)
    glb_path = output_dir / f"{scene_name}.glb"
    blend_path = output_dir / f"{scene_name}.blend"
    preview_path = output_dir / f"{scene_name}.png"
    try:
        port = int(params.get("blender_port") or os.environ.get("POLYKIT_BLENDER_MCP_PORT") or os.environ.get("BLENDER_MCP_PORT") or 9876)
        host = str(params.get("blender_host") or os.environ.get("POLYKIT_BLENDER_MCP_HOST") or os.environ.get("BLENDER_MCP_HOST") or "127.0.0.1")
        base.progress(5, f"Connecting to Blender MCP at {host}:{port}…")
        result = base._send_blender_code(
            host,
            port,
            _repair_script(
                source_path=source,
                scene_name=scene_name,
                building_spec=building_spec,
                part_ids=part_ids,
                attachment_ids=attachment_ids,
                render_preview=render_preview,
            ),
        )
        base.progress(82, "Receiving scoped repair artifacts…")
        encoded = str(result.get("glb_b64") or "")
        if not encoded:
            raise RuntimeError("Blender did not return a repaired GLB")
        import base64
        glb_path.write_bytes(base64.b64decode(encoded, validate=True))
        sidecars: list[str] = []
        for key, path in (("blend_b64", blend_path), ("preview_b64", preview_path)):
            value = str(result.get(key) or "")
            if value:
                path.write_bytes(base64.b64decode(value, validate=True))
                sidecars.append(str(path))
        validation = result.get("repair_validation") if isinstance(result.get("repair_validation"), Mapping) else {}
        base.progress(100, "Scoped part repair ready")
        base.emit({
            "type": "done",
            "result": {
                "filePath": str(glb_path),
                "sidecars": sidecars,
                "metadata": {
                    "repairValidation": validation,
                    "buildSpec": {"kind": "polykit.build-spec", "version": 1, "environment": None, "buildings": [dict(building_spec)]},
                    "repairScope": {
                        "partIds": part_ids,
                        "attachmentIds": attachment_ids,
                        "strategy": "translation-anchor-snap-v1",
                    },
                },
            },
        })
    except (OSError, ValueError, RuntimeError, socket.timeout, ConnectionError) as exc:
        base.error(f"blender-scene repair-parts: {exc}")


def main() -> None:
    raw = sys.stdin.readline()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        base.error(f"blender-scene: invalid process payload ({exc})")
        return
    params = payload.get("params") if isinstance(payload.get("params"), Mapping) else {}
    if params.get("repair_mode") == "parts":
        _repair_main(payload)
        return

    # Reuse the validated v2 full builder but enrich its GLB export with stable
    # provenance. Feed the already-read process payload back to v2 unchanged.
    base._scene_script = _patched_scene_script
    original_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(raw + "\n")
        base.main()
    finally:
        sys.stdin = original_stdin


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        base.error(f"blender-scene: {exc}")
