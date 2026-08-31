"""EmbodiedGen-inspired scene planning and deterministic layout helpers.

The reference project separates *what a scene means* from the backend that
generates an asset.  PolyKit keeps that separation on the server as a small,
JSON-first compiler:

``prompt/objects -> ScenePlan -> constrained instances``

This module deliberately does not call an LLM or a renderer.  An Agent can
author the plan through MCP, while the planner validates it and produces
repeatable transforms that can be stored in a ``WorldDocument``.  Heavy model
backends remain ordinary workflow nodes.
"""
from __future__ import annotations

import math
import random
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


SCENE_PLAN_SCHEMA_VERSION = 1
SCENE_PLAN_KIND = "polykit.scene-plan"
RELATION_TYPES = (
    "floor",
    "on",
    "inside",
    "in_room",
    "near",
    "beside",
    "away_from",
    "overlooking",
)
SUPPORT_RELATION_TYPES = ("floor", "on", "inside", "in_room")
SPATIAL_RELATION_TYPES = ("near", "beside", "away_from", "overlooking")
OBJECT_ROLES = ("room", "background", "context", "hero", "manipulated", "distractor")


class ScenePlanError(ValueError):
    """Raised when a plan cannot be normalized or laid out safely."""


def _clean_text(value: Any, label: str, *, max_length: int = 240) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    text = value.strip()
    if len(text) > max_length:
        raise ValueError(f"{label} is too long")
    return text


def _slug(value: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return result or "object"


def _vector(value: Any, label: str, *, length: int, positive: bool = False) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != length:
        raise ValueError(f"{label} must contain {length} numbers")
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{label} must contain numbers")
        number = float(item)
        if not math.isfinite(number) or (positive and number <= 0):
            raise ValueError(f"{label} contains an invalid number")
        numbers.append(number)
    return tuple(numbers)


class SceneBounds(BaseModel):
    """Axis-aligned planning volume in world units."""

    model_config = ConfigDict(extra="forbid")

    width: float = Field(default=12.0, gt=0, le=10000)
    depth: float = Field(default=12.0, gt=0, le=10000)
    height: float = Field(default=4.0, gt=0, le=10000)


class SceneAssetRef(BaseModel):
    """Stable asset identity; filenames are never used as semantic IDs."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    workspace_path: str | None = Field(default=None, alias="workspacePath")
    asset_id: str | None = Field(default=None, alias="assetId")
    run_id: str | None = Field(default=None, alias="runId")
    source: str | None = None


class SceneObject(BaseModel):
    """One semantic object requested by the scene plan."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    role: str = "context"
    category: str | None = None
    description: str = ""
    aliases: list[str] = Field(default_factory=list, max_length=32)
    size: tuple[float, float, float] = (1.0, 1.0, 1.0)
    asset: SceneAssetRef | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "name", "description", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any, info):
        if (value is None or value == "") and info.field_name == "description":
            return ""
        return _clean_text(value, info.field_name, max_length=2400 if info.field_name == "description" else 240)

    @field_validator("role", mode="before")
    @classmethod
    def _normalise_role(cls, value: Any):
        role = str(value or "context").strip().lower()
        return role if role in OBJECT_ROLES else "context"

    @field_validator("aliases", mode="before")
    @classmethod
    def _normalise_aliases(cls, value: Any):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, Sequence) or isinstance(value, (bytes, str)):
            raise ValueError("aliases must be a list of strings")
        result: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                cleaned = item.strip()
                if cleaned not in result:
                    result.append(cleaned)
        return result

    @field_validator("size", mode="before")
    @classmethod
    def _normalise_size(cls, value: Any):
        return _vector(value, "size", length=3, positive=True)


class SceneRelation(BaseModel):
    """A relation in the scene graph, matching EmbodiedGen's vocabulary."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    subject: str = Field(min_length=1, max_length=120)
    type: str = Field(min_length=1, max_length=40)
    object: str = Field(min_length=1, max_length=120)
    distance: float | None = Field(default=None, gt=0, le=10000)
    tolerance: float = Field(default=0.35, ge=0, le=10000)
    clearance: float = Field(default=0.0, ge=0, le=10000)
    side: Literal["left", "right", "front", "back"] | None = None

    @field_validator("subject", "object", mode="before")
    @classmethod
    def _strip_ids(cls, value: Any, info):
        return _clean_text(value, info.field_name, max_length=120)

    @field_validator("type", mode="before")
    @classmethod
    def _normalise_type(cls, value: Any):
        relation = str(value or "").strip().lower().replace("-", "_")
        if relation not in RELATION_TYPES:
            raise ValueError(f"Unknown relation '{relation}'; expected one of {', '.join(RELATION_TYPES)}")
        return relation

    @field_validator("side", mode="before")
    @classmethod
    def _normalise_side(cls, value: Any):
        if value is None or value == "":
            return None
        side = str(value).strip().lower()
        if side not in {"left", "right", "front", "back"}:
            raise ValueError("side must be one of left, right, front, back")
        return side


class SceneInstance(BaseModel):
    """Renderer-neutral instance transform produced by the layout solver.

    ``position`` is the object's ground/contact point (not its geometric
    centre), matching the existing Three.js world renderer and the reference
    layout export.  The renderer or composer accounts for the object's height.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    object_id: str = Field(alias="objectId")
    position: tuple[float, float, float]
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: float = Field(default=1.0, gt=0)
    room_id: str | None = Field(default=None, alias="roomId")

    @field_validator("position", "rotation", mode="before")
    @classmethod
    def _normalise_vector(cls, value: Any, info):
        return _vector(value, info.field_name, length=3)


class ScenePlan(BaseModel):
    """Validated intermediate representation for an editable scene."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: Literal[1] = SCENE_PLAN_SCHEMA_VERSION
    kind: Literal["polykit.scene-plan"] = SCENE_PLAN_KIND
    scene_id: str | None = Field(default=None, alias="sceneId")
    scene_kind: Literal["indoor", "outdoor", "mixed"] = Field(default="indoor", alias="sceneKind")
    prompt: str = ""
    seed: int = 0
    bounds: SceneBounds = Field(default_factory=SceneBounds)
    objects: list[SceneObject] = Field(default_factory=list, max_length=256)
    relations: list[SceneRelation] = Field(default_factory=list, max_length=512)
    instances: list[SceneInstance] = Field(default_factory=list, max_length=256)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scene_id", mode="before")
    @classmethod
    def _normalise_scene_id(cls, value: Any):
        if value is None or value == "":
            return None
        return _clean_text(value, "scene_id", max_length=160)

    @field_validator("prompt", mode="before")
    @classmethod
    def _normalise_prompt(cls, value: Any):
        if value is None or value == "":
            return ""
        return _clean_text(value, "prompt", max_length=20_000)

    @model_validator(mode="after")
    def _validate_graph(self):
        object_ids = [item.id for item in self.objects]
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("Scene object ids must be unique")
        known = set(object_ids)
        for relation in self.relations:
            if relation.subject not in known or relation.object not in known:
                raise ValueError(
                    f"Relation '{relation.subject} {relation.type} {relation.object}' references an unknown object"
                )
            if relation.subject == relation.object:
                raise ValueError("A scene relation cannot reference the same object twice")
        support_by_subject: dict[str, list[SceneRelation]] = {}
        for relation in self.relations:
            if relation.type in SUPPORT_RELATION_TYPES:
                support_by_subject.setdefault(relation.subject, []).append(relation)
        conflicting = next(
            (object_id for object_id, relations in support_by_subject.items() if len(relations) > 1),
            None,
        )
        if conflicting:
            raise ValueError(
                f"Scene object '{conflicting}' must have at most one floor/on/inside/in_room relation"
            )
        instance_ids = [item.object_id for item in self.instances]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("Scene instances must contain at most one transform per object")
        if not set(instance_ids).issubset(known):
            raise ValueError("Scene instances reference unknown objects")
        return self


def _coerce_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Accept EmbodiedGen-like layout payloads and PolyKit's naming variants."""

    value = dict(payload)
    nested = value.get("scene_plan") or value.get("scenePlan") or value.get("plan")
    if isinstance(nested, Mapping):
        value = {**dict(nested), **{key: child for key, child in value.items() if key not in {"scene_plan", "scenePlan", "plan"}}}
    if "assets" in value and "objects" not in value and isinstance(value["assets"], Sequence):
        value["objects"] = value.pop("assets")
    if "relations" not in value and isinstance(value.get("relation"), Sequence):
        value["relations"] = value["relation"]
    if "sceneKind" in value and "scene_kind" not in value:
        value["scene_kind"] = value.pop("sceneKind")
    if "sceneId" in value and "scene_id" not in value:
        value["scene_id"] = value.pop("sceneId")
    return value


def normalize_scene_plan(payload: Mapping[str, Any], *, scene_id: str | None = None) -> ScenePlan:
    """Validate an Agent-authored plan and fill safe defaults."""

    if not isinstance(payload, Mapping):
        raise ScenePlanError("Scene plan must be a JSON object")
    value = _coerce_payload(payload)
    if scene_id and not value.get("scene_id"):
        value["scene_id"] = scene_id
    try:
        return ScenePlan.model_validate(value)
    except ValidationError as exc:
        raise ScenePlanError(str(exc)) from exc


def _relation_map(plan: ScenePlan) -> dict[str, list[SceneRelation]]:
    result: dict[str, list[SceneRelation]] = {}
    for relation in plan.relations:
        result.setdefault(relation.subject, []).append(relation)
    return result


def _aabb(instance: SceneInstance, object_by_id: dict[str, SceneObject]) -> tuple[float, float, float, float]:
    obj = object_by_id[instance.object_id]
    half_x = obj.size[0] * instance.scale / 2.0
    half_z = obj.size[2] * instance.scale / 2.0
    x, _, z = instance.position
    return x - half_x, x + half_x, z - half_z, z + half_z


def _overlaps(
    candidate: SceneInstance,
    placed: list[SceneInstance],
    objects: dict[str, SceneObject],
    spacing: float,
    *,
    ignore_object_ids: set[str] | None = None,
) -> bool:
    candidate_object = objects[candidate.object_id]
    if candidate_object.role in {"room", "background"}:
        return False
    left, right, front, back = _aabb(candidate, objects)
    for current in placed:
        if ignore_object_ids and current.object_id in ignore_object_ids:
            continue
        if objects[current.object_id].role in {"room", "background"}:
            continue
        c_left, c_right, c_front, c_back = _aabb(current, objects)
        if right + spacing <= c_left or c_right + spacing <= left:
            continue
        if back + spacing <= c_front or c_back + spacing <= front:
            continue
        return True
    return False


def _inside_bounds(instance: SceneInstance, plan: ScenePlan) -> bool:
    obj = next(item for item in plan.objects if item.id == instance.object_id)
    half_x = obj.size[0] * instance.scale / 2.0
    half_z = obj.size[2] * instance.scale / 2.0
    x, y, z = instance.position
    return (
        -plan.bounds.width / 2 + half_x <= x <= plan.bounds.width / 2 - half_x
        and -plan.bounds.depth / 2 + half_z <= z <= plan.bounds.depth / 2 - half_z
        and 0 <= y <= plan.bounds.height - obj.size[1] * instance.scale
    )


def _inside_parent_bounds(
    instance: SceneInstance,
    parent: SceneInstance,
    object_by_id: dict[str, SceneObject],
    *,
    vertical: bool,
    margin: float = 0.0,
) -> bool:
    """Conservative AABB containment used when no mesh hull is available."""

    child = object_by_id[instance.object_id]
    parent_object = object_by_id[parent.object_id]
    child_half_x = child.size[0] * instance.scale / 2.0
    child_half_z = child.size[2] * instance.scale / 2.0
    parent_half_x = parent_object.size[0] * parent.scale / 2.0
    parent_half_z = parent_object.size[2] * parent.scale / 2.0
    cx, cy, cz = instance.position
    px, py, pz = parent.position
    margin = max(0.0, float(margin))
    if not (
        px - parent_half_x + child_half_x + margin <= cx <= px + parent_half_x - child_half_x - margin
        and pz - parent_half_z + child_half_z + margin <= cz <= pz + parent_half_z - child_half_z - margin
    ):
        return False
    if not vertical:
        return True
    child_height = child.size[1] * instance.scale
    parent_height = parent_object.size[1] * parent.scale
    return py + margin <= cy <= py + parent_height - child_height - margin


def _object_clearance(obj: SceneObject) -> float:
    raw = obj.constraints.get("clearance", 0.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value >= 0 else 0.0


def _relation_distance(
    relation: SceneRelation,
    subject: SceneObject,
    target: SceneObject,
    *,
    subject_scale: float = 1.0,
    target_scale: float = 1.0,
) -> float:
    if relation.distance is not None:
        return float(relation.distance)
    subject_radius = max(subject.size[0], subject.size[2]) * subject_scale / 2.0
    target_radius = max(target.size[0], target.size[2]) * target_scale / 2.0
    clearance = max(relation.clearance, _object_clearance(subject), _object_clearance(target))
    if relation.type == "near":
        return subject_radius + target_radius + max(clearance, 0.35)
    if relation.type == "beside":
        return subject_radius + target_radius + clearance
    if relation.type == "overlooking":
        return subject_radius + target_radius + max(clearance, 1.0)
    return (subject_radius + target_radius) * 2.0 + max(clearance, 0.75)


def _relation_margin(relation: SceneRelation) -> float:
    return max(0.0, float(relation.clearance))


def _horizontal_distance(a: SceneInstance, b: SceneInstance) -> float:
    return math.hypot(a.position[0] - b.position[0], a.position[2] - b.position[2])


def _clamp_inside_parent(
    x: float,
    z: float,
    instance: SceneInstance,
    parent: SceneInstance,
    relation: SceneRelation,
    object_by_id: dict[str, SceneObject],
) -> tuple[float, float]:
    child = object_by_id[instance.object_id]
    parent_object = object_by_id[parent.object_id]
    child_half_x = child.size[0] * instance.scale / 2.0
    child_half_z = child.size[2] * instance.scale / 2.0
    margin = _relation_margin(relation)
    min_x = parent.position[0] - parent_object.size[0] * parent.scale / 2.0 + child_half_x + margin
    max_x = parent.position[0] + parent_object.size[0] * parent.scale / 2.0 - child_half_x - margin
    min_z = parent.position[2] - parent_object.size[2] * parent.scale / 2.0 + child_half_z + margin
    max_z = parent.position[2] + parent_object.size[2] * parent.scale / 2.0 - child_half_z - margin
    if min_x <= max_x:
        x = max(min_x, min(max_x, x))
    if min_z <= max_z:
        z = max(min_z, min(max_z, z))
    return x, z


def solve_scene_layout(plan: ScenePlan, *, spacing: float = 0.12, max_attempts: int = 96) -> ScenePlan:
    """Place a relation graph deterministically using 2D footprint checks."""

    object_by_id = {item.id: item for item in plan.objects}
    relation_map = _relation_map(plan)
    rng = random.Random(plan.seed)
    existing = {item.object_id: item for item in plan.instances}
    placed: list[SceneInstance] = []
    diagnostics = [item for item in plan.diagnostics if item.get("code") != "layout"]

    role_order = {"room": 0, "background": 1, "context": 2, "hero": 3, "manipulated": 4, "distractor": 5}
    indexed_objects = {obj.id: (index, obj) for index, obj in enumerate(plan.objects)}
    remaining = set(indexed_objects)
    ordered_objects: list[SceneObject] = []
    placed_ids = set(existing)
    while remaining:
        ready: list[tuple[int, SceneObject]] = []
        for object_id in remaining:
            _, candidate_object = indexed_objects[object_id]
            dependencies = [
                item.object
                for item in relation_map.get(object_id, [])
                if item.type in {*SUPPORT_RELATION_TYPES, *SPATIAL_RELATION_TYPES}
            ]
            if not dependencies or all(parent_id in placed_ids for parent_id in dependencies):
                ready.append((indexed_objects[object_id][0], candidate_object))
        if not ready:
            ready = [min((indexed_objects[item][0], indexed_objects[item][1]) for item in remaining)]
            diagnostics.append({
                "code": "layout",
                "severity": "warning",
                "message": "Relation cycle detected; used stable fallback order.",
            })
        _, next_object = min(ready, key=lambda pair: (role_order.get(pair[1].role, 2), pair[0]))
        ordered_objects.append(next_object)
        remaining.remove(next_object.id)
        placed_ids.add(next_object.id)

    def parent_instance(target_id: str) -> SceneInstance | None:
        return next((item for item in placed if item.object_id == target_id), existing.get(target_id))

    floor_cursor = 0
    for obj in ordered_objects:
        if obj.id in existing and obj.id not in {item.object_id for item in placed}:
            candidate = existing[obj.id]
            if _inside_bounds(candidate, plan):
                placed.append(candidate)
                continue

        relations = relation_map.get(obj.id, [])
        support = next((item for item in relations if item.type in SUPPORT_RELATION_TYPES), None)
        spatial = next((item for item in relations if item.type in SPATIAL_RELATION_TYPES), None)
        parent = parent_instance(support.object) if support else None
        spatial_parent = parent_instance(spatial.object) if spatial else None
        scale = float(obj.constraints.get("scale", 1.0) or 1.0)
        if not math.isfinite(scale) or scale <= 0:
            scale = 1.0
        y = 0.0
        x = 0.0
        z = 0.0
        room_id: str | None = None
        if parent and support:
            px, py, pz = parent.position
            parent_obj = object_by_id[parent.object_id]
            if support.type == "on":
                x, z = px, pz
                y = py + parent_obj.size[1] * parent.scale
            elif support.type in {"floor", "inside", "in_room"}:
                x, z = px, pz
                room_id = parent.object_id
                if support.type == "inside":
                    y = py + _relation_margin(support)
                elif support.type == "in_room":
                    y = py

        spatial_yaw = 0.0
        if spatial_parent and spatial:
            tx, ty, tz = spatial_parent.position
            target_obj = object_by_id[spatial_parent.object_id]
            distance = _relation_distance(
                spatial,
                obj,
                target_obj,
                subject_scale=scale,
                target_scale=spatial_parent.scale,
            )
            angle = rng.random() * math.tau
            if spatial.side:
                angle = {"left": math.pi, "right": 0.0, "front": math.pi / 2, "back": -math.pi / 2}[spatial.side]
            elif spatial.type == "away_from":
                angle += math.pi
            x = tx + math.cos(angle) * distance
            z = tz + math.sin(angle) * distance
            if not parent:
                y = ty
            if spatial.type == "overlooking":
                spatial_yaw = math.atan2(tx - x, tz - z)
        elif not (parent and support) and obj.role in {"room", "background"}:
            x, z = 0.0, 0.0
        elif not (parent and support):
            columns = max(1, int(math.sqrt(max(len(plan.objects), 1))))
            row, column = divmod(floor_cursor, columns)
            floor_cursor += 1
            x = -plan.bounds.width / 2 + obj.size[0] / 2 + 0.5 + column * (obj.size[0] + 0.8)
            z = -plan.bounds.depth / 2 + obj.size[2] / 2 + 0.5 + row * (obj.size[2] + 0.8)

        candidate = SceneInstance(
            id=f"instance_{obj.id}",
            objectId=obj.id,
            position=(x, y, z),
            rotation=(0.0, spatial_yaw, 0.0),
            scale=scale,
            roomId=room_id,
        )
        if parent and support:
            x, z = _clamp_inside_parent(x, z, candidate, parent, support, object_by_id)
            candidate = candidate.model_copy(update={"position": (x, y, z)})

        collision_exclusions = (
            {support.object}
            if support and support.type in {"on", "inside", "in_room"}
            else set()
        )
        contained = True
        if parent and support and support.type in SUPPORT_RELATION_TYPES:
            contained = _inside_parent_bounds(
                candidate,
                parent,
                object_by_id,
                vertical=support.type == "inside",
                margin=_relation_margin(support),
            )
        if not _inside_bounds(candidate, plan) or not contained or _overlaps(
            candidate,
            placed,
            object_by_id,
            spacing,
            ignore_object_ids=collision_exclusions,
        ):
            found = False
            for attempt in range(max_attempts):
                angle = (attempt / max_attempts) * math.tau + rng.random() * 0.15
                radius = 0.55 + (attempt // 8) * 0.65
                trial = candidate.model_copy(update={
                    "position": (
                        max(-plan.bounds.width / 2 + obj.size[0] / 2, min(plan.bounds.width / 2 - obj.size[0] / 2, x + math.cos(angle) * radius)),
                        y,
                        max(-plan.bounds.depth / 2 + obj.size[2] / 2, min(plan.bounds.depth / 2 - obj.size[2] / 2, z + math.sin(angle) * radius)),
                    )
                })
                if parent and support:
                    trial_x, trial_z = _clamp_inside_parent(
                        trial.position[0],
                        trial.position[2],
                        trial,
                        parent,
                        support,
                        object_by_id,
                    )
                    trial = trial.model_copy(update={"position": (trial_x, y, trial_z)})
                trial_contained = True
                if parent and support and support.type in SUPPORT_RELATION_TYPES:
                    trial_contained = _inside_parent_bounds(
                        trial,
                        parent,
                        object_by_id,
                        vertical=support.type == "inside",
                        margin=_relation_margin(support),
                    )
                if _inside_bounds(trial, plan) and trial_contained and not _overlaps(
                    trial,
                    placed,
                    object_by_id,
                    spacing,
                    ignore_object_ids=collision_exclusions,
                ):
                    candidate = trial
                    found = True
                    break
            if not found:
                diagnostics.append({
                    "code": "layout",
                    "severity": "warning",
                    "object_id": obj.id,
                    "message": "No collision-free position found inside the scene bounds.",
                })

        placed.append(candidate)

    diagnostics.append({
        "code": "layout",
        "severity": "info",
        "message": f"Placed {len(placed)} object(s) with deterministic seed {plan.seed}.",
    })
    result = plan.model_copy(update={"instances": placed, "diagnostics": diagnostics})
    return _audit_scene_layout(result)


def _audit_scene_layout(plan: ScenePlan) -> ScenePlan:
    """Audit the whole plan in world space, independently of any camera."""

    object_by_id = {item.id: item for item in plan.objects}
    instance_by_id = {item.object_id: item for item in plan.instances}
    diagnostics = [item for item in plan.diagnostics if item.get("code") != "layout-quality"]
    errors = sum(1 for item in diagnostics if item.get("severity") == "error")
    warnings = sum(1 for item in diagnostics if item.get("severity") == "warning")

    def report(
        severity: str,
        message: str,
        *,
        object_id: str | None = None,
        relation: SceneRelation | None = None,
        **details: Any,
    ) -> None:
        nonlocal errors, warnings
        if severity == "error":
            errors += 1
        elif severity == "warning":
            warnings += 1
        item: dict[str, Any] = {"code": "layout-quality", "severity": severity, "message": message}
        if object_id:
            item["object_id"] = object_id
        if relation:
            item["relation"] = {
                "subject": relation.subject,
                "type": relation.type,
                "object": relation.object,
            }
        item.update(details)
        diagnostics.append(item)

    for instance in plan.instances:
        if instance.object_id not in object_by_id:
            report("error", "Instance references an unknown scene object.", object_id=instance.object_id)
            continue
        if not _inside_bounds(instance, plan):
            report(
                "error",
                "Object footprint or height leaves the declared scene bounds.",
                object_id=instance.object_id,
            )

    for relation in plan.relations:
        subject = instance_by_id.get(relation.subject)
        target = instance_by_id.get(relation.object)
        subject_obj = object_by_id.get(relation.subject)
        target_obj = object_by_id.get(relation.object)
        if not subject or not target or not subject_obj or not target_obj:
            report("error", "Relation has no compiled instance for one of its endpoints.", object_id=relation.subject, relation=relation)
            continue

        if relation.type in SUPPORT_RELATION_TYPES:
            if relation.type == "on":
                contained = _inside_parent_bounds(subject, target, object_by_id, vertical=False, margin=_relation_margin(relation))
                expected_y = target.position[1] + target_obj.size[1] * target.scale
                contact_error = abs(subject.position[1] - expected_y)
                if not contained:
                    report("error", "Object placed on a surface is not contained by the support footprint.", object_id=relation.subject, relation=relation)
                if contact_error > max(0.05, relation.tolerance):
                    report("error", "Object does not touch the top surface at the requested contact height.", object_id=relation.subject, relation=relation, error=round(contact_error, 4))
            else:
                contained = _inside_parent_bounds(
                    subject,
                    target,
                    object_by_id,
                    vertical=relation.type == "inside",
                    margin=_relation_margin(relation),
                )
                if not contained:
                    report("error", "Object leaves the declared parent footprint or volume.", object_id=relation.subject, relation=relation)
                if relation.type in {"floor", "in_room"}:
                    contact_error = abs(subject.position[1] - target.position[1])
                    if contact_error > max(0.05, relation.tolerance):
                        report("warning", "Object is not aligned with the parent floor plane.", object_id=relation.subject, relation=relation, error=round(contact_error, 4))
            continue

        distance = _horizontal_distance(subject, target)
        expected = _relation_distance(
            relation,
            subject_obj,
            target_obj,
            subject_scale=subject.scale,
            target_scale=target.scale,
        )
        tolerance = max(0.1, relation.tolerance)
        if relation.type == "near" and distance > expected + tolerance:
            report("error", "Object is farther from its near target than the declared tolerance.", object_id=relation.subject, relation=relation, distance=round(distance, 4), max_distance=round(expected + tolerance, 4))
        elif relation.type == "beside":
            minimum = max(0.0, subject_obj.size[0] * subject.scale / 2 + target_obj.size[0] * target.scale / 2 + relation.clearance)
            if distance < minimum - tolerance:
                report("error", "Beside relation penetrates the target footprint.", object_id=relation.subject, relation=relation, distance=round(distance, 4), min_distance=round(minimum, 4))
        elif relation.type == "away_from" and distance < expected - tolerance:
            report("error", "Object is closer than the declared away-from distance.", object_id=relation.subject, relation=relation, distance=round(distance, 4), min_distance=round(expected - tolerance, 4))
        elif relation.type == "overlooking":
            if distance > expected + tolerance:
                report("error", "Overlooking object is outside the declared viewing distance.", object_id=relation.subject, relation=relation, distance=round(distance, 4), max_distance=round(expected + tolerance, 4))
            expected_yaw = math.atan2(target.position[0] - subject.position[0], target.position[2] - subject.position[2])
            yaw_error = abs((subject.rotation[1] - expected_yaw + math.pi) % math.tau - math.pi)
            if yaw_error > 0.7:
                report("warning", "Overlooking object is not oriented toward its target.", object_id=relation.subject, relation=relation, yaw_error=round(yaw_error, 4))

    support_edges = {(item.subject, item.object) for item in plan.relations if item.type in SUPPORT_RELATION_TYPES}
    collidable = [
        instance
        for instance in plan.instances
        if instance.object_id in object_by_id and object_by_id[instance.object_id].role not in {"room", "background"}
    ]
    for index, current in enumerate(collidable):
        for other in collidable[index + 1 :]:
            if (current.object_id, other.object_id) in support_edges or (other.object_id, current.object_id) in support_edges:
                continue
            if _overlaps(current, [other], object_by_id, 0.0):
                report(
                    "warning",
                    "Object footprints overlap without a supporting relation.",
                    object_id=current.object_id,
                    other_object_id=other.object_id,
                )

    status = "pass" if errors == 0 and warnings == 0 else ("needs_review" if errors == 0 else "invalid")
    metadata = dict(plan.metadata)
    metadata["layoutQuality"] = {
        "status": status,
        "cameraIndependent": True,
        "checkedInstances": len(plan.instances),
        "checkedRelations": len(plan.relations),
        "errors": errors,
        "warnings": warnings,
        "checks": ["scene_bounds", "support_contact", "containment", "relations", "pairwise_footprints"],
    }
    return plan.model_copy(update={"diagnostics": diagnostics, "metadata": metadata})


def compile_scene_plan(
    payload: Mapping[str, Any],
    *,
    scene_id: str | None = None,
    solve: bool = True,
    resolve_assets: bool = False,
) -> dict[str, Any]:
    """Normalize and optionally solve a plan, returning JSON-safe data."""

    plan = normalize_scene_plan(payload, scene_id=scene_id)
    if resolve_assets:
        from services.scene_assets import resolve_scene_assets

        plan = resolve_scene_assets(plan)
    if solve:
        plan = solve_scene_layout(plan)
    return plan.model_dump(mode="json", by_alias=True, exclude_none=True)


__all__ = [
    "OBJECT_ROLES",
    "RELATION_TYPES",
    "SPATIAL_RELATION_TYPES",
    "SCENE_PLAN_KIND",
    "SCENE_PLAN_SCHEMA_VERSION",
    "SUPPORT_RELATION_TYPES",
    "SceneAssetRef",
    "SceneBounds",
    "SceneInstance",
    "SceneObject",
    "ScenePlan",
    "ScenePlanError",
    "SceneRelation",
    "compile_scene_plan",
    "normalize_scene_plan",
    "solve_scene_layout",
]
