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
from datetime import datetime, timezone
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
            if relation.type in {"floor", "on", "inside", "in_room"}:
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
    # Rooms and visual backgrounds are containers, not collidable props.  A
    # floor object explicitly related to a room should be allowed to occupy
    # the room's footprint.
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
) -> bool:
    """Conservative AABB containment used when no mesh hull is available.

    EmbodiedGen computes a convex-hull surface from the loaded parent mesh. A
    ScenePlan may be compiled before the GLB exists, so the portable contract
    uses semantic object dimensions and keeps the child inside the parent's
    footprint/volume until a mesh-aware backend is available.
    """

    child = object_by_id[instance.object_id]
    parent_object = object_by_id[parent.object_id]
    child_half_x = child.size[0] * instance.scale / 2.0
    child_half_z = child.size[2] * instance.scale / 2.0
    parent_half_x = parent_object.size[0] * parent.scale / 2.0
    parent_half_z = parent_object.size[2] * parent.scale / 2.0
    cx, cy, cz = instance.position
    px, py, pz = parent.position
    if not (
        px - parent_half_x + child_half_x <= cx <= px + parent_half_x - child_half_x
        and pz - parent_half_z + child_half_z <= cz <= pz + parent_half_z - child_half_z
    ):
        return False
    if not vertical:
        return True
    child_height = child.size[1] * instance.scale
    parent_height = parent_object.size[1] * parent.scale
    return (
        py <= cy <= py + parent_height - child_height
    )


def solve_scene_layout(plan: ScenePlan, *, spacing: float = 0.12, max_attempts: int = 96) -> ScenePlan:
    """Place a relation graph deterministically using 2D footprint checks.

    This intentionally mirrors the useful part of EmbodiedGen's BFS placement
    code without pretending to be a full navmesh or physics solver.  The
    resulting transforms are stable for a given plan seed and can be replaced
    later by a heavier backend without changing the scene-plan contract.
    """

    object_by_id = {item.id: item for item in plan.objects}
    relation_map = _relation_map(plan)
    rng = random.Random(plan.seed)
    existing = {item.object_id: item for item in plan.instances}
    placed: list[SceneInstance] = []
    diagnostics = [item for item in plan.diagnostics if item.get("code") != "layout"]

    # Context and hero objects get stable positions first.  This makes the
    # output readable and keeps dependent objects close to their parents.
    role_order = {"room": 0, "background": 1, "context": 2, "hero": 3, "manipulated": 4, "distractor": 5}
    indexed_objects = {obj.id: (index, obj) for index, obj in enumerate(plan.objects)}
    remaining = set(indexed_objects)
    ordered_objects: list[SceneObject] = []
    placed_ids = set(existing)
    # A small topological pass mirrors EmbodiedGen's BFS relation traversal:
    # parents are laid out before children, while unrelated objects retain
    # their stable role/order priority.  Cycles are still accepted by the
    # schema, but get a deterministic fallback instead of hanging the run.
    while remaining:
        ready: list[tuple[int, SceneObject]] = []
        for object_id in remaining:
            _, candidate_object = indexed_objects[object_id]
            dependencies = [
                item.object
                for item in relation_map.get(object_id, [])
                if item.type in {"floor", "on", "inside", "in_room", "near", "beside", "overlooking"}
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
        support = next((item for item in relations if item.type in {"floor", "on", "inside", "in_room", "near", "beside", "overlooking"}), None)
        parent = parent_instance(support.object) if support else None
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
                    # Start at the container's lower centre; the containment
                    # retry loop below distributes siblings if needed.
                    y = py
                elif support.type == "in_room":
                    y = py
            else:
                radius = max(parent_obj.size[0], parent_obj.size[2]) * parent.scale / 2 + max(obj.size[0], obj.size[2]) / 2 + 0.55
                angle = rng.random() * math.tau
                x, z = px + math.cos(angle) * radius, pz + math.sin(angle) * radius
                if support.type == "overlooking":
                    room_id = parent.room_id or parent.object_id
        elif obj.role in {"room", "background"}:
            # A container is centered by default.  Its dimensions describe
            # the available volume rather than a prop that needs spacing.
            x, z = 0.0, 0.0
        else:
            # Deterministic expanding grid for independent floor objects.
            columns = max(1, int(math.sqrt(max(len(plan.objects), 1))))
            row, column = divmod(floor_cursor, columns)
            floor_cursor += 1
            x = -plan.bounds.width / 2 + obj.size[0] / 2 + 0.5 + column * (obj.size[0] + 0.8)
            z = -plan.bounds.depth / 2 + obj.size[2] / 2 + 0.5 + row * (obj.size[2] + 0.8)

        candidate = SceneInstance(
            id=f"instance_{obj.id}",
            objectId=obj.id,
            position=(x, y, z),
            rotation=(0.0, 0.0, 0.0),
            scale=float(obj.constraints.get("scale", 1.0) or 1.0),
            roomId=room_id,
        )

        # A supported object shares the parent's footprint by definition.  The
        # reference placer checks a child against sibling boxes on the parent,
        # not against the parent mesh itself.  Keep that rule for ``on`` and
        # ``inside`` (and for an explicit room container) so valid contact
        # placements do not produce spurious collision diagnostics.
        collision_exclusions = (
            {support.object}
            if support and support.type in {"on", "inside", "in_room"}
            else set()
        )
        contained = True
        if parent and support and support.type in {"inside", "in_room"}:
            contained = _inside_parent_bounds(
                candidate,
                parent,
                object_by_id,
                vertical=support.type == "inside",
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
                trial_contained = True
                if parent and support and support.type in {"inside", "in_room"}:
                    trial_contained = _inside_parent_bounds(
                        trial,
                        parent,
                        object_by_id,
                        vertical=support.type == "inside",
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
    return plan.model_copy(update={"instances": placed, "diagnostics": diagnostics})


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
        # Import lazily so the planner remains usable in lightweight CLI/test
        # environments that do not need to scan the workspace.
        from services.scene_assets import resolve_scene_assets

        plan = resolve_scene_assets(plan)
    if solve:
        plan = solve_scene_layout(plan)
    return plan.model_dump(mode="json", by_alias=True, exclude_none=True)


def apply_scene_plan_to_world(world: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the additive plan/instances fields to an existing world record."""

    if not isinstance(world, Mapping):
        raise ScenePlanError("World document must be an object")
    compiled = compile_scene_plan(plan, scene_id=str(world.get("id") or world.get("world_id") or ""), solve=False)
    # A generated asset is attached through the world artifact map after the
    # initial plan was compiled.  Carry that stable reference back into the
    # semantic object so a later recompile (and the Three.js plan preview) can
    # use the real mesh instead of falling back to a box.
    existing_artifacts = world.get("artifacts")
    if isinstance(existing_artifacts, Mapping):
        objects: list[dict[str, Any]] = []
        changed = False
        for raw_object in compiled.get("objects", []):
            obj = dict(raw_object) if isinstance(raw_object, Mapping) else {}
            object_id = obj.get("id")
            artifact = existing_artifacts.get(object_id) if isinstance(object_id, str) else None
            mesh = artifact.get("mesh") if isinstance(artifact, Mapping) else None
            mesh_path = mesh.get("workspace_path") if isinstance(mesh, Mapping) else None
            if mesh_path and not obj.get("asset"):
                obj["asset"] = {
                    "workspacePath": mesh_path,
                    **({"runId": mesh["run_id"]} if isinstance(mesh.get("run_id"), str) else {}),
                    **({"source": "world-artifact"}),
                }
                changed = True
            objects.append(obj)
        if changed:
            compiled = compile_scene_plan({**compiled, "objects": objects}, scene_id=str(world.get("id") or world.get("world_id") or ""), solve=False)
    if not compiled.get("instances"):
        compiled = compile_scene_plan(compiled, scene_id=str(world.get("id") or world.get("world_id") or ""), solve=True)
    result = dict(world)
    result["scene_plan"] = compiled
    # Keep the existing outdoor renderer contract (`protoId`/`regionId`) on
    # the world envelope while the richer plan uses semantic `objectId` and
    # `roomId` names.  This is an additive adapter, not a renderer rewrite.
    result["instances"] = [
        {
            "id": item.get("id"),
            "protoId": item.get("objectId"),
            "position": item.get("position", [0, 0, 0]),
            "rotation": item.get("rotation", [0, 0, 0]),
            "scale": item.get("scale", 1),
            "regionId": item.get("roomId"),
        }
        for item in compiled.get("instances", [])
        if isinstance(item, Mapping)
    ]
    spec = result.get("spec")
    if isinstance(spec, Mapping):
        spec_copy = dict(spec)
        spec_copy["scene_plan"] = compiled
        result["spec"] = spec_copy
    else:
        result["spec"] = {"scene_plan": compiled}
    agent_plan = result.get("agent_plan")
    agent_copy = dict(agent_plan) if isinstance(agent_plan, Mapping) else {}
    agent_copy["scene_plan"] = compiled
    agent_copy["layout"] = {
        "status": "done",
        "instance_count": len(compiled.get("instances", [])),
        "diagnostics": compiled.get("diagnostics", []),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    stages = agent_copy.get("stages")
    if isinstance(stages, list):
        stage_map = {
            item.get("id"): dict(item)
            for item in stages
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        for stage_id in ("intent", "plan"):
            entry = stage_map.setdefault(stage_id, {"id": stage_id})
            entry["status"] = "done"
        placement = stage_map.setdefault("placement", {"id": "placement"})
        placement["status"] = "done" if compiled.get("instances") else "blocked"
        placement["diagnostics"] = compiled.get("diagnostics", [])
        placement["updated_at"] = datetime.now(timezone.utc).isoformat()
        agent_copy["stages"] = [stage_map[key] for key in stage_map]
    result["agent_plan"] = agent_copy
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    return result


__all__ = [
    "OBJECT_ROLES",
    "RELATION_TYPES",
    "SCENE_PLAN_KIND",
    "SCENE_PLAN_SCHEMA_VERSION",
    "SceneAssetRef",
    "SceneBounds",
    "SceneInstance",
    "SceneObject",
    "ScenePlan",
    "ScenePlanError",
    "SceneRelation",
    "apply_scene_plan_to_world",
    "compile_scene_plan",
    "normalize_scene_plan",
    "solve_scene_layout",
]
