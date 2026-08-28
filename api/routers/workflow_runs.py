import asyncio
import json
import traceback
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from schemas.generation import JobStatus
from schemas.workflow import WorkflowExecutionRequest
from services.image_generation import enqueue_generation_job, texture_refiner_id, workspace_url
from services.model_runtime_registry import model_runtime_registry
from services.run_coordinator import run_coordinator
from services.node_catalog import is_known
from services.process_runner import ProcessExecutionError
from services.workflow_engine import WorkflowEngine
from services.workflow_executor import (
    SINK_NODES,
    WorkflowError,
    topological_order,
    validate_prompt_links,
)
from services.runtime_paths import runtime_paths
from services.workspace_paths import normalize_collection

router = APIRouter(tags=["workflow-runs"])


def _require_known_class_type(class_type: str) -> None:
    """Reject unknown nodes at submit time so users get a 400, not a failed run."""
    if not is_known(class_type):
        raise ValueError(f"Unknown executable node '{class_type}'")


async def _run_workflow_dag(job_id: str, request: WorkflowExecutionRequest) -> None:
    """Execute a workflow DAG in the shared single-GPU server job slot."""
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
        run_coordinator.persist(job)

        engine = WorkflowEngine()
        final_mesh = await engine.run(
            job_id=job_id,
            request=request,
            job=job,
            persist=lambda: run_coordinator.persist(job),
            cancel_event=cancel_event,
            is_cancelled=lambda: run_coordinator.is_cancelled(job_id),
        )

        if run_coordinator.is_cancelled(job_id):
            return
        if final_mesh is None or not final_mesh.exists():
            raise WorkflowError("Workflow completed without a mesh output")

        job.status = "done"
        job.progress = 100
        job.step = "Workflow complete"
        job.output_url = workspace_url(final_mesh, collection)
        run_coordinator.mark_completed(job)
        thumbnail_target = final_mesh
        try:
            thumbnail_workspace_path = final_mesh.relative_to(runtime_paths.workspace).as_posix()
        except ValueError:
            thumbnail_target = None

    except (WorkflowError, ProcessExecutionError) as exc:
        if run_coordinator.is_cancelled(job_id):
            return
        job = run_coordinator.jobs[job_id]
        job.status = "error"
        job.error = str(exc)
        run_coordinator.mark_completed(job)
    except Exception as exc:
        if run_coordinator.is_cancelled(job_id):
            return
        tb = traceback.format_exc()
        msg = f"[Workflow ERROR] {exc}\n{tb}"
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode("ascii", errors="replace").decode("ascii"))
        job = run_coordinator.jobs[job_id]
        job.status = "error"
        job.error = tb.strip()
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
                print(f"[Thumbnails] workflow prewarm could not be queued: {exc}")


class WorkflowRunStatus(BaseModel):
    run_id: str
    status: str
    progress: int = 0
    step: Optional[str] = None
    output_url: Optional[str] = None
    error: Optional[str] = None
    scene_candidate: Optional[dict] = None
    meta: Optional[dict] = None


@router.post("/from-image")
async def create_run_from_image(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    model_id: str = Form(""),
    collection: str = Form("Default"),
    remesh: str = Form("quad"),
    enable_texture: bool = Form(False),
    texture_resolution: int = Form(1024),
    params: str = Form("{}"),
    workflow_id: str = Form(""),
    node_id: str = Form(""),
    world_id: str = Form(""),
    proto_id: str = Form(""),
):
    """Canonical single-image generation entry point."""
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
    """Accept compiled workflow JSON and enqueue its generic DAG execution."""
    try:
        if request.schema_version != 1:
            raise ValueError(f"Unsupported workflow execution schema: {request.schema_version}")
        collection = normalize_collection(request.collection or "Workflows")
        order = topological_order(request.prompt)
        for node in request.prompt.values():
            _require_known_class_type(node.class_type)
        if request.output_node_id is not None:
            output_node = request.prompt.get(request.output_node_id)
            if output_node is None or output_node.class_type not in SINK_NODES:
                raise ValueError("output_node_id must point to polykit.output or polykit.preview")
        if not any(request.prompt[node_id].class_type in SINK_NODES for node_id in order):
            raise ValueError("Workflow must include a polykit.output or polykit.preview node")
        validate_prompt_links(request)
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc

    run_coordinator.purge_old_jobs()
    job_id = str(uuid.uuid4())
    job = JobStatus(
        job_id=job_id,
        status="pending",
        progress=0,
        meta={"workflow_id": request.workflow_id, "collection": collection},
    )
    run_coordinator.register(job)
    model_runtime_registry.begin_generation(job_id)
    background_tasks.add_task(_run_workflow_dag, job_id, request)
    return {
        "run_id": job_id,
        "status": "pending",
        "workflow_id": request.workflow_id,
        "queued_nodes": len(request.prompt),
    }


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
    if run_coordinator.cancel(run_id) is None:
        raise HTTPException(404, f"Run {run_id} not found")
    return {"cancelled": True}
