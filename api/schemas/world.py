"""Strict schema-v2 vocabulary for server-owned world runtimes.

A world has one runtime contract. Build authoring input, semantic scene data,
compiled transforms, gameplay and Agent quality state are all nested under
``runtime`` and are never mirrored as legacy top-level fields.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


WORLD_SCHEMA_VERSION = 2
WORLD_KIND = "polykit.world"
WORLD_RUNTIME_VERSION = 1

WorldStageId = Literal[
    "intent",
    "blockout",
    "structure",
    "environment",
    "assets",
    "materials",
    "lighting",
    "gameplay",
    "optimization",
]
WorldStageStatus = Literal["locked", "ready", "running", "passed", "failed"]
WorldGateStatus = Literal["pending", "pass", "needs_review", "fail"]


class WorldRuntimeStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: WorldStageId
    status: WorldStageStatus
    note: str | None = None
    updated_at: str | None = None


class WorldRuntimeIssue(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    code: str
    gate: Literal["construction", "visual", "gameplay"]
    severity: Literal["info", "warning", "error"]
    message: str


class WorldRuntimeGateState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WorldGateStatus = "pending"
    issues: list[WorldRuntimeIssue] = Field(default_factory=list)
    checked_at: str | None = None


class WorldRuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[WorldRuntimeStage]
    gates: dict[str, WorldRuntimeGateState]
    updated_at: str | None = None


class WorldRuntimeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = ""


class BuildAnchorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    partId: str | None = None
    position: tuple[float, float, float] | None = None
    normal: tuple[float, float, float] | None = None


class BuildAttachmentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    from_: str = Field(alias="from")
    to: str
    mode: Literal["flush", "support", "inside", "passes-through"]
    tolerance: float = Field(ge=0)


class BuildingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    generator: Literal["blender-parametric"]
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    anchors: list[BuildAnchorSpec] = Field(default_factory=list)
    attachments: list[BuildAttachmentSpec] = Field(default_factory=list)


class WorldBuildSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["polykit.build-spec"]
    version: Literal[1]
    environment: dict[str, Any] | None = None
    buildings: list[BuildingSpec] = Field(default_factory=list)


class GamePlayerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controller: Literal["walk"]
    radius: float = Field(gt=0)
    height: float = Field(gt=0)
    move_speed: float = Field(gt=0)
    spawn: dict[str, Any]


class GameCollisionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["semantic-aabb", "manifest"]


class GameInteractionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    objectId: str
    action: str
    socket: str | None = None


class GameObjectiveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    trigger: str
    targetId: str | None = None


class WorldGameSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["polykit.game-spec"]
    version: Literal[1]
    player: GamePlayerSpec
    collision: GameCollisionSpec
    interactions: list[GameInteractionSpec] = Field(default_factory=list)
    objectives: list[GameObjectiveSpec] = Field(default_factory=list)


class WorldRuntimeCompiled(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instances: list[dict[str, Any]] = Field(default_factory=list)


class WorldRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = WORLD_RUNTIME_VERSION
    intent: WorldRuntimeIntent
    build: WorldBuildSpec
    scene: dict[str, Any] | None = None
    compiled: WorldRuntimeCompiled
    game: WorldGameSpec
    state: WorldRuntimeState


class WorldDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    kind: Literal["polykit.world"]
    id: str
    name: str
    created_at: str
    updated_at: str
    run_id: str | None = None
    parent_world_id: str | None = None
    runtime: WorldRuntime
    artifacts: dict[str, Any] = Field(default_factory=dict)


WorldPayload = WorldDocument
