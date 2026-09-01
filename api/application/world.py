"""World application commands shared by Web and Agent entry points."""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, Field

from application.execution import PreparedExecution, prepare_execution_run
from schemas.execution import ExecutionInitiator
from services.world_plans import compile_scene_composition_plan
from services.world_workflows import compile_structure_plan


class ComposeWorldCommand(BaseModel):
    collection: str = Field(default="Scenes", max_length=160)
    output_name: str = Field(default="scene", min_length=1, max_length=120)
    allow_missing: bool = False


class BuildWorldStructureCommand(BaseModel):
    building_id: str | None = Field(default=None, max_length=160)
    collection: str = Field(default="Scenes", max_length=160)
    render_preview: bool = True
    render_profile: str = Field(default="production", max_length=32)


def prepare_world_composition_run(
    world: Mapping[str, Any],
    *,
    world_id: str,
    command: ComposeWorldCommand,
    initiator: ExecutionInitiator,
) -> PreparedExecution:
    plan = compile_scene_composition_plan(
        world,
        world_id=world_id,
        collection=command.collection,
        output_name=command.output_name,
        allow_missing=command.allow_missing,
    )
    return prepare_execution_run(plan, initiator=initiator)


def prepare_world_structure_run(
    world: Mapping[str, Any],
    *,
    world_id: str,
    command: BuildWorldStructureCommand,
    initiator: ExecutionInitiator,
) -> PreparedExecution:
    plan = compile_structure_plan(
        world,
        world_id=world_id,
        building_id=command.building_id,
        collection=command.collection,
        render_preview=command.render_preview,
        render_profile=command.render_profile,
    )
    return prepare_execution_run(plan, initiator=initiator)


__all__ = [
    "BuildWorldStructureCommand",
    "ComposeWorldCommand",
    "prepare_world_composition_run",
    "prepare_world_structure_run",
]
