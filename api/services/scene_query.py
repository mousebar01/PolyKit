"""Deterministic scene-query resolution for AI-authored scene edits.

Natural language is intentionally compiled into ``SceneQuery`` elsewhere. This
module resolves that structured query against ScenePlan semantics, relations,
and instance transforms without starting Blender or scanning workspace files.
Blender receives only stable object/instance IDs after resolution.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.scene_planner import ScenePlan, ScenePlanError, normalize_scene_plan


def _text(value: Any) -> str:
    return str(value or "").strip().casefold()


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
    direct = _strings(payload.get(key))
    constraints = payload.get("constraints")
    nested = _strings(constraints.get(key)) if isinstance(constraints, Mapping) else []
    result = list(direct)
    for value in nested:
        if value not in result:
            result.append(value)
    return result


class SceneQueryRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: str = Field(min_length=1, max_length=40)
    object_id: str = Field(alias="objectId", min_length=1, max_length=120)

    @field_validator("type", "object_id", mode="before")
    @classmethod
    def _strip(cls, value: Any):
        return str(value or "").strip().lower()


class SceneQuery(BaseModel):
    """Structured query produced by an Agent from a natural-language edit."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target: Literal["instance", "object"] = "instance"
    ids: list[str] = Field(default_factory=list, max_length=256)
    terms: list[str] = Field(default_factory=list, max_length=32)
    category: str | None = None
    role: str | None = None
    tags_any: list[str] = Field(default_factory=list, alias="tagsAny", max_length=32)
    collections_any: list[str] = Field(default_factory=list, alias="collectionsAny", max_length=32)
    relation: SceneQueryRelation | None = None
    near_object_id: str | None = Field(default=None, alias="nearObjectId")
    max_distance: float | None = Field(default=None, alias="maxDistance", gt=0, le=100000)
    sort: Literal["id", "name", "distance"] = "id"
    limit: int | None = Field(default=None, ge=1, le=256)

    @field_validator("ids", "terms", "tags_any", "collections_any", mode="before")
    @classmethod
    def _normalise_lists(cls, value: Any):
        return _strings(value)

    @field_validator("category", "role", "near_object_id", mode="before")
    @classmethod
    def _normalise_optional_text(cls, value: Any):
        cleaned = str(value or "").strip()
        return cleaned or None

    @model_validator(mode="after")
    def _validate_distance(self):
        if self.max_distance is not None and not self.near_object_id:
            raise ValueError("maxDistance requires nearObjectId")
        if self.sort == "distance" and not self.near_object_id:
            raise ValueError("distance sort requires nearObjectId")
        return self


class SceneQueryMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    object_id: str = Field(alias="objectId")
    instance_id: str | None = Field(default=None, alias="instanceId")
    name: str
    category: str | None = None
    role: str
    tags: list[str] = Field(default_factory=list)
    collections: list[str] = Field(default_factory=list)
    distance: float | None = None


class SceneQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[SceneQueryMatch]
    total: int


def _plan(value: ScenePlan | Mapping[str, Any]) -> ScenePlan:
    if isinstance(value, ScenePlan):
        return value
    if isinstance(value, Mapping):
        return normalize_scene_plan(value)
    raise ScenePlanError("Scene query requires a ScenePlan or scene-plan mapping")


def _object_payload(obj: Any) -> dict[str, Any]:
    return obj.model_dump(by_alias=True) if hasattr(obj, "model_dump") else dict(obj)


def _matches_terms(payload: Mapping[str, Any], terms: Sequence[str]) -> bool:
    if not terms:
        return True
    values = [
        payload.get("id"),
        payload.get("name"),
        payload.get("category"),
        payload.get("description"),
        *(_strings(payload.get("aliases"))),
        *(_metadata_list(payload, "tags")),
        *(_metadata_list(payload, "collections")),
    ]
    haystack = "\n".join(_text(value) for value in values if value is not None)
    return all(_text(term) in haystack for term in terms)


def _object_matches(payload: Mapping[str, Any], query: SceneQuery) -> bool:
    object_id = str(payload.get("id") or "")
    if query.ids and object_id not in query.ids:
        return False
    if query.category and _text(payload.get("category")) != _text(query.category):
        return False
    if query.role and _text(payload.get("role")) != _text(query.role):
        return False
    if query.tags_any:
        tags = {_text(value) for value in _metadata_list(payload, "tags")}
        if not tags.intersection(_text(value) for value in query.tags_any):
            return False
    if query.collections_any:
        collections = {_text(value) for value in _metadata_list(payload, "collections")}
        if not collections.intersection(_text(value) for value in query.collections_any):
            return False
    return _matches_terms(payload, query.terms)


def _relation_subjects(plan: ScenePlan, query: SceneQuery) -> set[str] | None:
    if query.relation is None:
        return None
    expected_type = _text(query.relation.type)
    expected_object = query.relation.object_id
    return {
        relation.subject
        for relation in plan.relations
        if _text(relation.type) == expected_type and relation.object == expected_object
    }


def _distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(a[index]) - float(b[index])) ** 2 for index in range(3)))


def resolve_scene_query(
    scene: ScenePlan | Mapping[str, Any],
    query: SceneQuery | Mapping[str, Any],
) -> SceneQueryResult:
    """Resolve a structured query to stable semantic object/instance IDs.

    Resolution order is deterministic and deliberately non-embedding based:
    metadata filters -> relation filter -> optional spatial filter/sort -> limit.
    """

    plan = _plan(scene)
    parsed_query = query if isinstance(query, SceneQuery) else SceneQuery.model_validate(query)
    relation_subjects = _relation_subjects(plan, parsed_query)
    objects = {obj.id: obj for obj in plan.objects}
    payloads = {object_id: _object_payload(obj) for object_id, obj in objects.items()}

    candidate_ids = [
        object_id
        for object_id, payload in payloads.items()
        if _object_matches(payload, parsed_query)
        and (relation_subjects is None or object_id in relation_subjects)
    ]

    instances_by_object: dict[str, list[Any]] = {}
    instances_by_id: dict[str, Any] = {}
    for instance in plan.instances:
        instances_by_object.setdefault(instance.object_id, []).append(instance)
        instances_by_id[instance.id] = instance

    anchor_positions: list[Sequence[float]] = []
    if parsed_query.near_object_id:
        if parsed_query.near_object_id not in objects:
            raise ScenePlanError(f"Scene query anchor '{parsed_query.near_object_id}' is unknown")
        anchor_positions = [item.position for item in instances_by_object.get(parsed_query.near_object_id, [])]
        if not anchor_positions:
            raise ScenePlanError(f"Scene query anchor '{parsed_query.near_object_id}' has no positioned instance")

    matches: list[SceneQueryMatch] = []
    for object_id in candidate_ids:
        obj = objects[object_id]
        payload = payloads[object_id]
        tags = _metadata_list(payload, "tags")
        collections = _metadata_list(payload, "collections")
        object_instances = instances_by_object.get(object_id, [])
        if parsed_query.target == "object":
            distance = None
            if anchor_positions and object_instances:
                distance = min(_distance(instance.position, anchor) for instance in object_instances for anchor in anchor_positions)
            if parsed_query.max_distance is not None and (distance is None or distance > parsed_query.max_distance):
                continue
            matches.append(SceneQueryMatch(
                objectId=object_id,
                name=obj.name,
                category=obj.category,
                role=obj.role,
                tags=tags,
                collections=collections,
                distance=distance,
            ))
            continue

        for instance in object_instances:
            if parsed_query.ids and instance.id in parsed_query.ids:
                pass
            elif parsed_query.ids and object_id not in parsed_query.ids:
                continue
            distance = None
            if anchor_positions:
                distance = min(_distance(instance.position, anchor) for anchor in anchor_positions)
            if parsed_query.max_distance is not None and (distance is None or distance > parsed_query.max_distance):
                continue
            matches.append(SceneQueryMatch(
                objectId=object_id,
                instanceId=instance.id,
                name=obj.name,
                category=obj.category,
                role=obj.role,
                tags=tags,
                collections=collections,
                distance=distance,
            ))

    if parsed_query.sort == "distance":
        matches.sort(key=lambda item: (float("inf") if item.distance is None else item.distance, item.instance_id or "", item.object_id))
    elif parsed_query.sort == "name":
        matches.sort(key=lambda item: (_text(item.name), item.instance_id or "", item.object_id))
    else:
        matches.sort(key=lambda item: (item.object_id, item.instance_id or ""))

    total = len(matches)
    if parsed_query.limit is not None:
        matches = matches[: parsed_query.limit]
    return SceneQueryResult(matches=matches, total=total)


__all__ = [
    "SceneQuery",
    "SceneQueryMatch",
    "SceneQueryRelation",
    "SceneQueryResult",
    "resolve_scene_query",
]
