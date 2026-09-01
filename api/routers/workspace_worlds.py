"""HTTP surface for server-owned schema-v2 World documents."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from pydantic import BaseModel, Field

from application.world import (
    BuildWorldStructureCommand,
    ComposeWorldCommand,
    ResolveWorldAssetsCommand,
    compile_world_asset_resolution,
    prepare_world_composition_run,
    prepare_world_structure_run,
)
from schemas.execution import ExecutionInitiator
from schemas.workflow import WorkflowExecutionRequest
from services.execution_runtime import run_execution
from services.run_coordinator import run_coordinator
from services.scene_planner import ScenePlanError, compile_scene_plan
from services.world_domain import create_world_document
from services.world_plans import compile_scene_composition_plan
from services.world_runtime import attach_scene_plan_to_runtime
from application.execution import prepare_execution_run
from services.world_store import (
    WorldNotFoundError,
    WorldStoreError,
    WorldTooLargeError,
    get_world,
    save_world,
)
from services.world_validation import validate_world


router = APIRouter(prefix="/workspace-library/worlds", tags=["workspace-worlds"])


class WorldCreateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=240)
    prompt: str | None = Field(default=None, max_length=20_000)
    parent_world_id: str | None = Field(default=None, max_length=160)


class ScenePlanCompileRequest(BaseModel):
    """Semantic scene graph accepted by the server planner."""

    plan: dict[str, Any] | None = None
    prompt: str = Field(default="", max_length=20_000)
    scene_kind: str = Field(default="indoor", max_length=24)
    seed: int = 0
    bounds: dict[str, Any] | None = None
    objects: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    relations: list[dict[str, Any]] = Field(default_factory=list, max_length=512)
    solve: bool = True
    resolve_assets: bool = False


class SceneComposeRequest(ComposeWorldCommand):
    """Compatibility HTTP model for the ComposeWorld application command."""


class WorldStructureRequest(BuildWorldStructureCommand):
    """Compatibility HTTP model for the BuildWorldStructure application command."""


class WorldValidationRequest(BaseModel):
    capability: str = Field(min_length=1, max_length=160)
    run_id: str | None = Field(default=None, max_length=160)


def _build_scene_composition_workflow(
    world: dict[str, Any],
    *,
    world_id: str,
    collection: str,
    output_name: str,
    allow_missing: bool,
) -> WorkflowExecutionRequest:
    """Compatibility wrapper around the canonical World composition compiler."""

    plan = compile_scene_composition_plan(
        world,
        world_id=world_id,
        collection=collection,
        output_name=output_name,
        allow_missing=allow_missing,
    )
    return WorkflowExecutionRequest.model_validate(plan.model_dump(mode="python"))


def _schedule_world_run(prepared, background_tasks: BackgroundTasks) -> dict[str, Any]:
    background_tasks.add_task(run_execution, prepared.run_id, prepared.request)
    source = prepared.request.source.model_dump(mode="json") if prepared.request.source else None
    return {
        "run_id": prepared.run_id,
        "status": "pending",
        "source": source,
        "workflow_id": prepared.request.workflow_id,
        "queued_nodes": prepared.queued_nodes,
    }


@router.post("/{world_id}/scene-plan")
async def compile_world_scene_plan(world_id: str, request: ScenePlanCompileRequest):
    """Compile a semantic scene and persist it only at ``runtime.scene``."""

    try:
        current = get_world(world_id)
        if current is None:
            raise HTTPException(status_code=404, detail="World was not found")
        payload = dict(request.plan) if isinstance(request.plan, dict) else {
            "prompt": request.prompt,
            "scene_kind": request.scene_kind,
            "seed": request.seed,
            "bounds": request.bounds or {},
            "objects": request.objects,
            "relations": request.relations,
        }
        compiled = compile_scene_plan(
            payload,
            scene_id=world_id,
            solve=request.solve,
            resolve_assets=request.resolve_assets,
        )
        updated = attach_scene_plan_to_runtime(current, compiled)
        saved = save_world(world_id, updated)
        return {"world_id": world_id, "scene": compiled, "world": saved}
    except HTTPException:
        raise
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (ScenePlanError, WorldStoreError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not compile scene plan: {exc}") from exc


@router.post("/{world_id}/resolve-assets")
async def resolve_world_assets(
    world_id: str,
    request: ResolveWorldAssetsCommand,
    background_tasks: BackgroundTasks,
):
    """Resolve semantic scene slots through existing, procedural, library, then local generation."""

    try:
        world = get_world(world_id)
        if world is None:
            raise HTTPException(status_code=404, detail="World was not found")
        compiled = compile_world_asset_resolution(world, world_id=world_id, command=request)
        updated = attach_scene_plan_to_runtime(world, compiled.scene)
        saved = save_world(world_id, updated)

        runs: list[dict[str, Any]] = []
        for plan in compiled.generation_plans:
            prepared = prepare_execution_run(
                plan,
                initiator=ExecutionInitiator(type="user", surface="worlds.resolve-assets"),
            )
            background_tasks.add_task(run_execution, prepared.run_id, prepared.request)
            runs.append({
                "run_id": prepared.run_id,
                "proto_id": prepared.request.metadata.get("proto_id"),
                "status": "pending",
                "queued_nodes": prepared.queued_nodes,
            })

        return {
            "world_id": world_id,
            "decisions": compiled.decisions,
            "generation_runs": runs,
            "world": saved,
        }
    except HTTPException:
        raise
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (ScenePlanError, WorldStoreError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not resolve World assets: {exc}") from exc


@router.post("/{world_id}/compose")
async def compose_world_scene(
    world_id: str,
    request: SceneComposeRequest,
    background_tasks: BackgroundTasks,
):
    try:
        world = get_world(world_id)
        if world is None:
            raise HTTPException(status_code=404, detail="World was not found")
        prepared = prepare_world_composition_run(
            world,
            world_id=world_id,
            command=ComposeWorldCommand.model_validate(request.model_dump(mode="python")),
            initiator=ExecutionInitiator(type="user", surface="worlds.compose"),
        )
        return _schedule_world_run(prepared, background_tasks)
    except HTTPException:
        raise
    except (ScenePlanError, WorldStoreError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not compose scene: {exc}") from exc


@router.post("/{world_id}/build-structure")
async def build_world_structure(
    world_id: str,
    request: WorldStructureRequest,
    background_tasks: BackgroundTasks,
):
    """Build one precise World structure through the shared Run layer."""

    try:
        world = get_world(world_id)
        if world is None:
            raise HTTPException(status_code=404, detail="World was not found")
        prepared = prepare_world_structure_run(
            world,
            world_id=world_id,
            command=BuildWorldStructureCommand.model_validate(request.model_dump(mode="python")),
            initiator=ExecutionInitiator(type="user", surface="worlds.build-structure"),
        )
        return _schedule_world_run(prepared, background_tasks)
    except HTTPException:
        raise
    except (WorldStoreError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not build world structure: {exc}") from exc


@router.post("/{world_id}/validate")
async def validate_world_for_workflow(world_id: str, request: WorldValidationRequest):
    """Return deterministic validation evidence for one World capability."""

    try:
        world = get_world(world_id)
        if world is None:
            raise HTTPException(status_code=404, detail="World was not found")
        run = None
        if request.run_id:
            job = run_coordinator.jobs.get(request.run_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"Run {request.run_id} was not found")
            run = {
                "run_id": job.job_id,
                "status": job.status,
                "progress": job.progress,
                "error": job.error,
                "meta": job.meta or {},
            }
        return validate_world(world_id, world, request.capability, run=run)
    except HTTPException:
        raise
    except WorldStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
async def create_world(request: WorldCreateRequest | None = Body(default=None)):
    try:
        payload = request or WorldCreateRequest()
        document = create_world_document(
            name=payload.name,
            prompt=payload.prompt,
            parent_world_id=payload.parent_world_id,
        )
        saved = save_world(document["id"], document)
        workspace_path = f"Workflows/{document['id']}.world.json"
        return {
            "world_id": document["id"],
            "workspace_path": workspace_path,
            "url": f"/workspace/{workspace_path}",
            "world": saved,
        }
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WorldStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create world: {exc}") from exc


@router.put("/{world_id:path}")
async def put_world(world_id: str, world: dict[str, Any] = Body(...)):
    try:
        save_world(world_id, world)
        workspace_path = f"Workflows/{world_id.strip()}.world.json"
        return {
            "world_id": world_id.strip(),
            "workspace_path": workspace_path,
            "url": f"/workspace/{workspace_path}",
        }
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WorldStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save world: {exc}") from exc


@router.get("/{world_id:path}")
async def read_world(world_id: str):
    try:
        world = get_world(world_id)
    except WorldTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WorldStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read world: {exc}") from exc
    if world is None:
        raise HTTPException(status_code=404, detail="World was not found")
    return world
