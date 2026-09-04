import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from application.execution import prepare_execution_run
from application.generate_asset import GenerateAssetCommand, compile_generate_asset_plan
from application.generate_asset_upload import prepare_uploaded_image_asset_run
from application.run_control import (
    RunNotFoundError,
    RunStateError,
    cancel_run as cancel_application_run,
    inspect_run as inspect_application_run,
    prepare_run_retry,
    prepare_run_signal,
)
from schemas.execution import ExecutionInitiator, ExecutionPlan, ExecutionSource
from schemas.workflow import WorkflowExecutionRequest
from services.execution_runtime import run_execution
from services.model_runtime_registry import model_runtime_registry
from services.run_coordinator import run_coordinator
from services.runtime_paths import runtime_paths
from services.workflow_execution import load_workflow_execution_request, prepare_execution_resume

router = APIRouter(tags=["workflow-runs"])

# Private compatibility symbol retained for startup recovery, old imports, and
# tests while the canonical runtime lives in services.execution_runtime.
_run_workflow_dag = run_execution


class WorkflowRunStatus(BaseModel):
    """Compatibility response name for the canonical Run status."""

    run_id: str
    status: str
    progress: int = 0
    step: Optional[str] = None
    output_url: Optional[str] = None
    error: Optional[str] = None
    scene_candidate: Optional[dict] = None
    meta: Optional[dict] = None


class WorkflowSignalRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    payload: Any = None


class TextToAssetRequest(GenerateAssetCommand):
    """Compatibility request name for the shared GenerateAsset command."""


def build_text_to_asset_workflow(request: TextToAssetRequest) -> WorkflowExecutionRequest:
    """Compatibility compiler backed by the canonical GenerateAsset command."""

    command = GenerateAssetCommand.model_validate(request.model_dump(mode="python"))
    plan = compile_generate_asset_plan(command)
    return WorkflowExecutionRequest.model_validate(plan.model_dump(mode="python"))


@router.post("/from-image")
async def create_run_from_image(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    model_id: str = Form(""),
    collection: str = Form("Workflows"),
    remesh: str = Form("none"),
    enable_texture: bool = Form(False),
    enable_optimize: bool = Form(False),
    target_faces: int = Form(1_000_000),
    texture_resolution: int = Form(1024),
    params: str = Form("{}"),
    workflow_id: str = Form(""),
    node_id: str = Form(""),
    world_id: str = Form(""),
    proto_id: str = Form(""),
):
    """Compatibility multipart transport backed by the shared asset command."""

    try:
        parsed_params = json.loads(params)
    except (json.JSONDecodeError, TypeError):
        parsed_params = {}
    model_params = dict(parsed_params) if isinstance(parsed_params, dict) else {}
    image_bytes = await image.read()
    selected_model_id = model_id or model_runtime_registry.active_status()["id"]

    try:
        prepared = prepare_uploaded_image_asset_run(
            image_bytes=image_bytes,
            content_type=image.content_type,
            image_name=image.filename,
            model_id=selected_model_id,
            collection=collection,
            remesh=remesh,
            enable_texture=enable_texture,
            enable_optimize=enable_optimize,
            target_faces=target_faces,
            texture_resolution=texture_resolution,
            model_params=model_params,
            workflow_id=workflow_id,
            node_id=node_id,
            world_id=world_id,
            proto_id=proto_id,
            initiator=ExecutionInitiator(type="user", surface="assets.generate.image"),
        )
    except ValueError as exc:
        status = 413 if "larger than 50 MiB" in str(exc) else 400
        raise HTTPException(status, str(exc)) from exc
    except (KeyError, OSError) as exc:
        raise HTTPException(500, f"Could not prepare Run input: {exc}") from exc

    background_tasks.add_task(run_execution, prepared.run_id, prepared.request)
    return {"run_id": prepared.run_id, "status": "pending"}


@router.post("/execute")
async def execute_workflow(
    request: WorkflowExecutionRequest,
    background_tasks: BackgroundTasks,
):
    """Compatibility submit endpoint backed by the generic Application layer."""

    plan = ExecutionPlan.model_validate(request.model_dump(mode="python"))
    if plan.source is None:
        plan = plan.model_copy(
            update={"source": ExecutionSource(kind="workflow", id=request.workflow_id)}
        )
    try:
        prepared = prepare_execution_run(
            plan,
            initiator=ExecutionInitiator(type="system", surface="workflow-runs.execute"),
        )
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc

    background_tasks.add_task(run_execution, prepared.run_id, prepared.request)
    return {
        "run_id": prepared.run_id,
        "status": "pending",
        "workflow_id": prepared.request.workflow_id,
        "queued_nodes": prepared.queued_nodes,
    }


@router.post("/{run_id}/signals")
async def signal_run(
    run_id: str,
    signal: WorkflowSignalRequest,
    background_tasks: BackgroundTasks,
):
    """Compatibility alias for canonical Run signal delivery."""
    try:
        prepared, accepted = prepare_run_signal(
            run_id,
            name=signal.name,
            payload=signal.payload,
        )
    except RunNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RunStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    background_tasks.add_task(run_execution, prepared.run_id, prepared.request)
    return {
        "run_id": prepared.run_id,
        "status": "pending",
        "resumed": True,
        "signal_id": accepted["id"],
        "signal_name": accepted["name"],
    }


@router.post("/{run_id}/retry")
async def retry_run(run_id: str, background_tasks: BackgroundTasks):
    """Compatibility alias for canonical Run retry."""
    try:
        prepared = prepare_run_retry(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RunStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    background_tasks.add_task(run_execution, prepared.run_id, prepared.request)
    return {"run_id": prepared.run_id, "status": "pending", "resumed": True}


async def recover_interrupted_workflow_runs() -> None:
    """Requeue interrupted durable executions after FastAPI startup.

    Runs created before the ExecutionPlan migration may have no durable
    execution snapshot and remain interrupted instead of being guessed/replayed.
    """
    for job in list(run_coordinator.jobs.values()):
        if job.status != "interrupted":
            continue
        try:
            request = load_workflow_execution_request(job, workspace_root=runtime_paths.workspace)
            prepare_execution_resume(job)
        except ValueError:
            run_coordinator.mark_completed(job)
            continue
        run_coordinator.clear_completed(job.job_id)
        run_coordinator.ensure_cancel_event(job.job_id)
        job.status = "pending"
        job.error = None
        job.step = "Recovering interrupted execution"
        run_coordinator.persist(job)
        model_runtime_registry.begin_generation(job.job_id)
        asyncio.create_task(run_execution(job.job_id, request))


@router.post("/text-to-asset")
async def create_text_to_asset_run(
    request: TextToAssetRequest,
    background_tasks: BackgroundTasks,
):
    """Compatibility alias for the shared GenerateAsset command."""

    workflow = build_text_to_asset_workflow(request)
    return await execute_workflow(workflow, background_tasks)


@router.get("", response_model=list[WorkflowRunStatus])
async def list_runs(
    limit: int = 20,
    workflow_id: Optional[str] = None,
    collection: Optional[str] = None,
):
    """Return recent server-owned runs so a refreshed web client can reconnect."""
    limit = max(1, min(limit, 100))
    runs = []
    for job in reversed(list(run_coordinator.jobs.values())):
        meta = job.meta or {}
        if workflow_id and meta.get("workflow_id") != workflow_id:
            continue
        if collection:
            job_collection = meta.get("collection")
            if job_collection and job_collection != collection:
                continue
            if not job_collection and job.output_url and f"/{collection}/" not in job.output_url:
                continue
        scene_candidate = None
        if job.status == "done" and job.output_url:
            scene_candidate = {"workspace_path": job.output_url.removeprefix("/workspace/")}
        runs.append(
            WorkflowRunStatus(
                run_id=job.job_id,
                status=job.status,
                progress=job.progress,
                step=job.step,
                output_url=job.output_url,
                error=job.error,
                scene_candidate=scene_candidate,
                meta=meta,
            )
        )
        if len(runs) >= limit:
            break
    return runs


@router.get("/{run_id}/inspect")
async def inspect_run(run_id: str):
    """Compatibility alias for canonical Run inspection."""
    try:
        return inspect_application_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{run_id}", response_model=WorkflowRunStatus)
async def get_run(run_id: str):
    job = run_coordinator.jobs.get(run_id)
    if not job:
        raise HTTPException(404, f"Run {run_id} not found")

    scene_candidate = None
    if job.status == "done" and job.output_url:
        scene_candidate = {"workspace_path": job.output_url.removeprefix("/workspace/")}

    return WorkflowRunStatus(
        run_id=job.job_id,
        status=job.status,
        progress=job.progress,
        step=job.step,
        output_url=job.output_url,
        error=job.error,
        scene_candidate=scene_candidate,
        meta=job.meta,
    )


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Compatibility alias for canonical Run cancellation."""
    try:
        cancel_application_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"cancelled": True}
