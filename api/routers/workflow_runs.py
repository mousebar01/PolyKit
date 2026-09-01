import asyncio
import json
import traceback
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from application.execution import prepare_execution_run
from application.generate_asset import GenerateAssetCommand, compile_generate_asset_plan
from schemas.execution import ExecutionInitiator, ExecutionPlan, ExecutionSource
from schemas.workflow import WorkflowExecutionRequest
from services.execution_engine import ExecutionEngine, ExecutionWait
from services.image_generation import enqueue_generation_job, texture_refiner_id, workspace_url
from services.model_runtime_registry import model_runtime_registry
from services.run_coordinator import run_coordinator
from services.run_observability import (
    finalize_workflow_run,
    inspect_workflow_run,
    mark_workflow_run_started,
    observe_workflow_checkpoint,
)
from services.process_runner import ProcessExecutionError
from services.workflow_execution import (
    current_waiting,
    execution_summary,
    load_workflow_execution_request,
    mark_current_step_failed,
    prepare_execution_resume,
    submit_signal,
)
from services.workflow_executor import WorkflowError
from services.runtime_paths import runtime_paths
from services.workspace_paths import normalize_collection

router = APIRouter(tags=["workflow-runs"])


async def _run_workflow_dag(job_id: str, request: WorkflowExecutionRequest) -> None:
    """Execute or resume a canonical plan in the shared single-GPU server slot.

    The function name remains for compatibility with startup recovery and older
    imports. New scheduling code should think in terms of ExecutionPlan/Run.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_coordinator.generation_lock.acquire)
    run_coordinator.set_active(job_id)
    cancel_event = run_coordinator.cancel_events.get(job_id)
    collection = normalize_collection(request.collection or "Workflows")
    thumbnail_target = None
    thumbnail_workspace_path = None

    try:
        if run_coordinator.is_cancelled(job_id):
            return
        job = run_coordinator.jobs[job_id]
        job.status = "running"
        job.error = None
        mark_workflow_run_started(job)
        run_coordinator.persist(job)

        def persist_observed() -> None:
            observe_workflow_checkpoint(job)
            run_coordinator.persist(job)

        engine = ExecutionEngine()
        result = await engine.run(
            job_id=job_id,
            request=request,
            job=job,
            persist=persist_observed,
            cancel_event=cancel_event,
            is_cancelled=lambda: run_coordinator.is_cancelled(job_id),
        )

        if run_coordinator.is_cancelled(job_id):
            return
        if isinstance(result, ExecutionWait):
            job.status = "waiting"
            job.step = f"Waiting for signal '{result.signal_name}'"
            job.error = None
            run_coordinator.persist(job)
            return

        final_artifact = result
        if final_artifact is None or not final_artifact.exists():
            raise WorkflowError("Execution completed without an output artifact")

        job.status = "done"
        job.progress = 100
        job.step = "Execution complete"
        job.output_url = workspace_url(final_artifact, collection)
        finalize_workflow_run(job, status="done", output_url=job.output_url)
        run_coordinator.mark_completed(job)
        if (job.meta or {}).get("artifact_kind", "mesh") == "mesh":
            thumbnail_target = final_artifact
            try:
                thumbnail_workspace_path = final_artifact.relative_to(runtime_paths.workspace).as_posix()
            except ValueError:
                thumbnail_target = None

    except (WorkflowError, ProcessExecutionError) as exc:
        if run_coordinator.is_cancelled(job_id):
            return
        job = run_coordinator.jobs[job_id]
        mark_current_step_failed(job, str(exc))
        job.status = "error"
        job.error = str(exc)
        finalize_workflow_run(job, status="error", error=job.error)
        run_coordinator.mark_completed(job)
    except Exception as exc:
        if run_coordinator.is_cancelled(job_id):
            return
        tb = traceback.format_exc()
        msg = f"[Execution ERROR] {exc}\n{tb}"
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode("ascii", errors="replace").decode("ascii"))
        job = run_coordinator.jobs[job_id]
        mark_current_step_failed(job, str(exc))
        job.status = "error"
        job.error = tb.strip()
        finalize_workflow_run(job, status="error", error=str(exc))
        run_coordinator.mark_completed(job)
    finally:
        run_coordinator.clear_active(job_id)
        model_runtime_registry.end_generation(job_id)
        run_coordinator.generation_lock.release()
        if thumbnail_target is not None and thumbnail_workspace_path is not None:
            try:
                from services.asset_thumbnails import _LIBRARY_SIZE, prewarm_thumbnail
                prewarm_thumbnail(thumbnail_workspace_path, thumbnail_target, _LIBRARY_SIZE)
            except Exception as exc:
                print(f"[Thumbnails] execution prewarm could not be queued: {exc}")


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
    remesh: str = Form("quad"),
    enable_texture: bool = Form(False),
    texture_resolution: int = Form(1024),
    params: str = Form("{}"),
    workflow_id: str = Form(""),
    node_id: str = Form(""),
    world_id: str = Form(""),
    proto_id: str = Form(""),
):
    """Compatibility multipart entry point for manual single-image generation.

    Multipart upload transport still uses the proven direct image runner. New
    JSON/manual/Agent generation should use /commands/generate-asset; a later
    migration can materialize multipart uploads into ExecutionPlan inputs.
    """
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")
    if remesh not in ("quad", "triangle", "none"):
        raise HTTPException(400, "remesh must be 'quad', 'triangle', or 'none'")

    collection = normalize_collection(collection)
    model_id = model_id or model_runtime_registry.active_status()["id"]
    try:
        model_runtime_registry.get_generator(model_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if enable_texture and texture_refiner_id(model_id) is None:
        raise HTTPException(
            400,
            f"Model '{model_id}' does not provide a compatible Texture Mesh node",
        )

    try:
        model_params = json.loads(params)
    except (json.JSONDecodeError, TypeError):
        model_params = {}

    full_params = {
        "remesh": remesh,
        "enable_texture": enable_texture,
        "texture_resolution": texture_resolution,
        **model_params,
    }
    metadata = {
        key: value
        for key, value in {
            "workflow_id": workflow_id.strip(),
            "node_id": node_id.strip(),
            "world_id": world_id.strip(),
            "proto_id": proto_id.strip(),
            "image_name": (image.filename or "").strip(),
        }.items()
        if value
    }
    metadata["initiator"] = {"type": "user", "surface": "assets.generate.image"}
    metadata["execution_source"] = {"kind": "direct", "id": "assets.generate.image"}

    image_bytes = await image.read()
    job_id = enqueue_generation_job(
        background_tasks,
        image_bytes,
        full_params,
        collection,
        model_id,
        metadata,
    )
    return {"run_id": job_id, "status": "pending"}


@router.post("/execute")
async def execute_workflow(
    request: WorkflowExecutionRequest,
    background_tasks: BackgroundTasks,
):
    """Compatibility submit endpoint backed by the generic Application layer."""

    plan = ExecutionPlan.model_validate(request.model_dump(mode="python"))
    if plan.source is None:
        plan = plan.model_copy(
            update={
                "source": ExecutionSource(kind="workflow", id=request.workflow_id),
            }
        )
    try:
        prepared = prepare_execution_run(
            plan,
            initiator=ExecutionInitiator(type="system", surface="workflow-runs.execute"),
        )
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc

    background_tasks.add_task(_run_workflow_dag, prepared.run_id, prepared.request)
    return {
        "run_id": prepared.run_id,
        "status": "pending",
        "workflow_id": prepared.request.workflow_id,
        "queued_nodes": prepared.queued_nodes,
    }


def _queue_existing_run(job_id: str, request: WorkflowExecutionRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    job = run_coordinator.jobs[job_id]
    prepare_execution_resume(job)
    run_coordinator.clear_completed(job_id)
    run_coordinator.ensure_cancel_event(job_id)
    job.status = "pending"
    job.error = None
    job.step = "Queued for resume"
    run_coordinator.persist(job)
    model_runtime_registry.begin_generation(job_id)
    background_tasks.add_task(_run_workflow_dag, job_id, request)
    return {"run_id": job_id, "status": "pending", "resumed": True}


@router.post("/{run_id}/signals")
async def signal_run(run_id: str, signal: WorkflowSignalRequest, background_tasks: BackgroundTasks):
    """Deliver the expected signal and resume the same durable Run."""
    job = run_coordinator.jobs.get(run_id)
    if job is None:
        raise HTTPException(404, f"Run {run_id} not found")
    if job.status != "waiting":
        raise HTTPException(409, "Run is not waiting for a signal")
    try:
        accepted = submit_signal(job, name=signal.name, payload=signal.payload)
        request = load_workflow_execution_request(job, workspace_root=runtime_paths.workspace)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    run_coordinator.persist(job)
    result = _queue_existing_run(run_id, request, background_tasks)
    return {**result, "signal_id": accepted["id"], "signal_name": accepted["name"]}


@router.post("/{run_id}/retry")
async def retry_run(run_id: str, background_tasks: BackgroundTasks):
    """Retry only incomplete steps using completed run-owned checkpoints."""
    job = run_coordinator.jobs.get(run_id)
    if job is None:
        raise HTTPException(404, f"Run {run_id} not found")
    if job.status not in {"error", "interrupted"}:
        raise HTTPException(409, "Only failed or interrupted Runs can be retried")
    try:
        request = load_workflow_execution_request(job, workspace_root=runtime_paths.workspace)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _queue_existing_run(run_id, request, background_tasks)


async def recover_interrupted_workflow_runs() -> None:
    """Requeue interrupted durable executions after FastAPI startup.

    Legacy multipart image-generation jobs have no durable execution snapshot
    and remain interrupted instead of being guessed or replayed.
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
        asyncio.create_task(_run_workflow_dag(job.job_id, request))


@router.post("/text-to-asset")
async def create_text_to_asset_run(
    request: TextToAssetRequest,
    background_tasks: BackgroundTasks,
):
    """Compatibility alias for the shared GenerateAsset command."""

    workflow = build_text_to_asset_workflow(request)
    return await execute_workflow(workflow, background_tasks)


@router.get("", response_model=list[WorkflowRunStatus])
async def list_runs(limit: int = 20, workflow_id: Optional[str] = None, collection: Optional[str] = None):
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
    """Return persisted telemetry plus authoritative durable execution steps."""
    job = run_coordinator.jobs.get(run_id)
    if not job:
        raise HTTPException(404, f"Run {run_id} not found")
    value = inspect_workflow_run(job)
    value["execution"] = execution_summary(job)
    value["waiting"] = current_waiting(job)
    return value


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
    job = run_coordinator.cancel(run_id)
    if job is None:
        raise HTTPException(404, f"Run {run_id} not found")
    finalize_workflow_run(job, status="cancelled")
    run_coordinator.persist(job)
    return {"cancelled": True}
