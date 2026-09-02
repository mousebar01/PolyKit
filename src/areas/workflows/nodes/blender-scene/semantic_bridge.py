"""Blender-side adapter for PolyKit semantic scene identity.

This module intentionally does not perform fuzzy or natural-language search.
The server resolves SceneQuery first, then Blender maps stable semantic IDs to
actual bpy objects and applies JSON-safe custom properties/collection scopes.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any


OBJECT_ID_PROP = "polykit_object_id"
INSTANCE_ID_PROP = "polykit_instance_id"
ROOT_COLLECTION = "PolyKit"
_PROPERTY_KEY_RE = re.compile(r"^polykit_[a-z0-9_]{1,119}$")


class SemanticIdentityError(ValueError):
    """Raised when Blender contains an ambiguous PolyKit semantic identity."""


def _property_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Blender custom properties must contain finite numbers")
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Blender custom properties must be JSON serializable") from exc


def _property_key(key: Any) -> str:
    if not isinstance(key, str) or not _PROPERTY_KEY_RE.fullmatch(key):
        raise ValueError("PolyKit custom property keys must match polykit_<lowercase_name>")
    return key


def _semantic_id(obj: Any, property_name: str) -> str | None:
    value = obj.get(property_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        object_name = str(getattr(obj, "name", "<unnamed>"))
        raise SemanticIdentityError(
            f"{property_name} on '{object_name}' must be a non-empty string"
        )
    return value.strip()


def apply_custom_properties(obj: Any, properties: Mapping[str, Any]) -> None:
    """Write projection metadata to a bpy Object or dict-like test double."""

    for key, value in properties.items():
        if value is None:
            continue
        obj[_property_key(key)] = _property_value(value)


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
        object_id = _semantic_id(obj, OBJECT_ID_PROP)
        instance_id = _semantic_id(obj, INSTANCE_ID_PROP)
        if object_id is not None:
            objects.setdefault(object_id, []).append(obj)
        if instance_id is not None:
            previous = instances.get(instance_id)
            if previous is not None and previous is not obj:
                previous_name = str(getattr(previous, "name", "<unnamed>"))
                current_name = str(getattr(obj, "name", "<unnamed>"))
                raise SemanticIdentityError(
                    f"Duplicate PolyKit instance id '{instance_id}' on '{previous_name}' and '{current_name}'"
                )
            instances[instance_id] = obj
    return {"objects": objects, "instances": instances}


def _object_sort_key(obj: Any) -> tuple[str, str]:
    instance_id = obj.get(INSTANCE_ID_PROP)
    name = str(getattr(obj, "name", ""))
    return (instance_id if isinstance(instance_id, str) else "", name)


def _requested_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must contain non-empty strings")
    return value.strip()


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
        obj = index["instances"].get(_requested_id(instance_id, "instance_ids"))
        if obj is not None and id(obj) not in seen:
            result.append(obj)
            seen.add(id(obj))
    for object_id in object_ids:
        candidates = sorted(
            index["objects"].get(_requested_id(object_id, "object_ids"), []),
            key=_object_sort_key,
        )
        for obj in candidates:
            if id(obj) not in seen:
                result.append(obj)
                seen.add(id(obj))
    return result


__all__ = [
    "INSTANCE_ID_PROP",
    "OBJECT_ID_PROP",
    "ROOT_COLLECTION",
    "SemanticIdentityError",
    "apply_custom_properties",
    "apply_projection_entry",
    "ensure_collection",
    "link_object_collections",
    "resolve_semantic_objects",
    "semantic_index",
]
