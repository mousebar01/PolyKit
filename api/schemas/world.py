"""Strict schema-v2 vocabulary for server-owned world runtimes.

A world has one runtime contract.  Outdoor build input, semantic scene data,
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


class WorldRuntimeCompiled(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instances: list[dict[str, Any]] = Field(default_factory=list)


class WorldRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = WORLD_RUNTIME_VERSION
    intent: WorldRuntimeIntent
    build: dict[str, Any] | None = None
    scene: dict[str, Any] | None = None
    compiled: WorldRuntimeCompiled
    game: dict[str, Any]
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
