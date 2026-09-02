"""Blender-side adapter for PolyKit semantic scene identity.

This module intentionally does not perform fuzzy or natural-language search.
The server resolves SceneQuery first, then Blender maps stable semantic IDs to
actual bpy objects and applies JSON-safe custom properties/collection scopes.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


OBJECT_ID_PROP = "polykit_object_id"
INSTANCE_ID_PROP = "polykit_instance_id"
ROOT_COLLECTION = "PolyKit"


def _property_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def apply_custom_properties(obj: Any, properties: Mapping[str, Any]) -> None:
    """Write projection metadata to a bpy Object or dict-like test double."""

    for key, value in properties.items():
        if value is None:
            continue
        obj[str(key)] = _property_value(value)


def ensure_collection(bpy: Any, name: str, *, root_name: str = ROOT_COLLECTION) -> Any:
    """Return a Blender collection, creating it under the PolyKit root."""

    scene_collection = bpy.context.scene.collection
    root = bpy.data.collections.get(root_name)
    if root is None:
        root = bpy.data.collections.new(root_name)
    if scene_collection.children.get(root.name) is None:
        scene_collection.children.link(root)
    if name == root_name:
        return root
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if root.children.get(collection.name) is None:
        root.children.link(collection)
    return collection


def link_object_collections(bpy: Any, obj: Any, collection_names: Iterable[str]) -> None:
    """Link an object to semantic scope collections without using them as tags."""

    for name in collection_names:
        cleaned = str(name or "").strip()
        if not cleaned:
            continue
        collection = ensure_collection(bpy, cleaned)
        if collection.objects.get(obj.name) is None:
            collection.objects.link(obj)


def apply_projection_entry(bpy: Any, obj: Any, entry: Mapping[str, Any]) -> None:
    properties = entry.get("customProperties")
    if isinstance(properties, Mapping):
        apply_custom_properties(obj, properties)
    collections = entry.get("collections")
    if isinstance(collections, list):
        link_object_collections(bpy, obj, collections)


def semantic_index(bpy: Any) -> dict[str, dict[str, Any]]:
    """Build exact semantic-ID lookups from Blender custom properties in one scan.

    ``objectId`` is one-to-many because multiple Blender instances may share one
    semantic prototype. ``instanceId`` is unique and maps to one Blender object.
    """

    objects: dict[str, list[Any]] = {}
    instances: dict[str, Any] = {}
    for obj in bpy.data.objects:
        object_id = obj.get(OBJECT_ID_PROP)
        instance_id = obj.get(INSTANCE_ID_PROP)
        if isinstance(object_id, str) and object_id:
            objects.setdefault(object_id, []).append(obj)
        if isinstance(instance_id, str) and instance_id:
            instances[instance_id] = obj
    return {"objects": objects, "instances": instances}


def resolve_semantic_objects(
    bpy: Any,
    *,
    object_ids: Iterable[str] = (),
    instance_ids: Iterable[str] = (),
) -> list[Any]:
    """Resolve stable PolyKit IDs to bpy objects, preserving requested order."""

    index = semantic_index(bpy)
    result: list[Any] = []
    seen: set[int] = set()
    for instance_id in instance_ids:
        obj = index["instances"].get(str(instance_id))
        if obj is not None and id(obj) not in seen:
            result.append(obj)
            seen.add(id(obj))
    for object_id in object_ids:
        for obj in index["objects"].get(str(object_id), []):
            if id(obj) not in seen:
                result.append(obj)
                seen.add(id(obj))
    return result


__all__ = [
    "INSTANCE_ID_PROP",
    "OBJECT_ID_PROP",
    "ROOT_COLLECTION",
    "apply_custom_properties",
    "apply_projection_entry",
    "ensure_collection",
    "link_object_collections",
    "resolve_semantic_objects",
    "semantic_index",
]
