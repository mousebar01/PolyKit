"""Project PolyKit scene semantics onto Blender-native organization.

Blender owns DCC concerns (Objects, Collections, transforms). PolyKit keeps the
stable semantic identity and query vocabulary. The projection produced here is
small, JSON-safe, and suitable for applying as Blender custom properties.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from services.scene_planner import ScenePlan, ScenePlanError, normalize_scene_plan


POLYKIT_ROOT_COLLECTION = "PolyKit"


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _metadata_list(payload: Mapping[str, Any], key: str) -> list[str]:
    result = _strings(payload.get(key))
    constraints = payload.get("constraints")
    nested = _strings(constraints.get(key)) if isinstance(constraints, Mapping) else []
    for value in nested:
        if value not in result:
            result.append(value)
    return result


def _safe_name(value: str) -> str:
    original = str(value or "").strip()
    cleaned = re.sub(r"[^0-9A-Za-z_\-.]+", "_", original).strip("_") or "object"
    # Blender names are presentation only, but they still need to be unique so
    # two semantic IDs cannot become ambiguous after sanitisation (for example
    # ``a/b`` and ``a?b``).  The digest is deterministic and the custom
    # ``polykit_*_id`` properties remain the source of truth for identity.
    digest = hashlib.sha1(original.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[:108]}_{digest}"


def _plan(value: ScenePlan | Mapping[str, Any]) -> ScenePlan:
    if isinstance(value, ScenePlan):
        return value
    if isinstance(value, Mapping):
        return normalize_scene_plan(value)
    raise ScenePlanError("Blender scene projection requires a ScenePlan or scene-plan mapping")


def _json_property(values: Sequence[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def compile_blender_scene_projection(scene: ScenePlan | Mapping[str, Any]) -> dict[str, Any]:
    """Compile a ScenePlan to Blender collection/property metadata.

    Collection membership is intentionally a coarse scope. Multi-dimensional
    semantics stay in custom properties so one object does not require a deep or
    duplicated collection hierarchy.
    """

    plan = _plan(scene)
    instance_by_object: dict[str, list[Any]] = {}
    for instance in plan.instances:
        instance_by_object.setdefault(instance.object_id, []).append(instance)

    collection_names: list[str] = [POLYKIT_ROOT_COLLECTION]
    objects: list[dict[str, Any]] = []
    instances: list[dict[str, Any]] = []

    for obj in plan.objects:
        payload = obj.model_dump(by_alias=True)
        tags = _metadata_list(payload, "tags")
        collections = _metadata_list(payload, "collections") or [f"Role/{obj.role}"]
        for name in collections:
            if name not in collection_names:
                collection_names.append(name)

        aliases = _strings(payload.get("aliases"))
        asset_id = obj.asset.asset_id if obj.asset else None
        source = obj.asset.source if obj.asset else None
        properties: dict[str, Any] = {
            "polykit_object_id": obj.id,
            "polykit_name": obj.name,
            "polykit_role": obj.role,
            "polykit_aliases": _json_property(aliases),
            "polykit_tags": _json_property(tags),
            "polykit_collections": _json_property(collections),
        }
        if obj.category:
            properties["polykit_category"] = obj.category
        if obj.description:
            properties["polykit_description"] = obj.description
        if asset_id:
            properties["polykit_asset_id"] = asset_id
        if source:
            properties["polykit_asset_source"] = source

        objects.append({
            "objectId": obj.id,
            "blenderName": f"PK_{_safe_name(obj.id)}",
            "collections": collections,
            "customProperties": properties,
        })

        for instance in instance_by_object.get(obj.id, []):
            instance_payload = instance.model_dump(by_alias=True)
            instance_collections = list(collections)
            for name in _metadata_list(instance_payload, "collections"):
                if name not in instance_collections:
                    instance_collections.append(name)
                if name not in collection_names:
                    collection_names.append(name)
            if instance.room_id:
                room_collection = f"Room/{instance.room_id}"
                if room_collection not in instance_collections:
                    instance_collections.append(room_collection)
                if room_collection not in collection_names:
                    collection_names.append(room_collection)
            instance_properties = dict(properties)
            instance_properties.update({
                "polykit_instance_id": instance.id,
                "polykit_object_id": obj.id,
                "polykit_collections": _json_property(instance_collections),
            })
            if instance.room_id:
                instance_properties["polykit_room_id"] = instance.room_id
            instances.append({
                "instanceId": instance.id,
                "objectId": obj.id,
                "blenderName": f"PK_{_safe_name(obj.id)}__{_safe_name(instance.id)}",
                "collections": instance_collections,
                "transform": {
                    "position": list(instance.position),
                    "rotation": list(instance.rotation),
                    "scale": instance.scale,
                },
                "customProperties": instance_properties,
            })

    return {
        "schemaVersion": 1,
        "kind": "polykit.blender-scene-projection",
        "sceneId": plan.scene_id,
        "rootCollection": POLYKIT_ROOT_COLLECTION,
        "collections": collection_names,
        "objects": objects,
        "instances": instances,
    }


__all__ = ["POLYKIT_ROOT_COLLECTION", "compile_blender_scene_projection"]
