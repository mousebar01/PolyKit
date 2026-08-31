import asyncio
import json
import traceback
import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from schemas.generation import JobStatus
from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest
from services.image_generation import enqueue_generation_job, texture_refiner_id, workspace_url
from services.model_runtime_registry import model_runtime_registry
from services.run_coordinator import run_coordinator
from services.run_observability import (
    finalize_workflow_run,
    init_workflow_observability,
    inspect_workflow_run,
    mark_workflow_run_started,
    observe_workflow_checkpoint,
)
from services.node_catalog import is_known
from services.process_runner import ProcessExecutionError
from services.workflow_engine import WorkflowEngine, WorkflowWait
from services.workflow_execution import (
    current_waiting,
    execution_summary,
    initialize_workflow_execution,
    load_workflow_execution_request,
    mark_current_step_failed,
    prepare_execution_resume,
    submit_signal,
)
from services.workflow_executor import (
    SINK_NODES,
    WorkflowError,
    select_execution_prompt,
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
    """Execute or resume a workflow DAG in the shared single-GPU server job slot."""
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

        engine = WorkflowEngine()
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
        if isinstance(result, WorkflowWait):
            job.status = "waiting"
            job.step = f"Waiting for signal '{result.signal_name}'"
            job.error = None
            run_coordinator.persist(job)
            return

        final_artifact = result
        if final_artifact is None or not final_artifact.exists():
            raise WorkflowError("Workflow completed without an output artifact")

        job.status = "done"
        job.progress = 100
        job.step = "Workflow complete"
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
        msg = f"[Workflow ERROR] {exc}\n{tb}"
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


class WorkflowSignalRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    payload: Any = None


class TextToAssetRequest(BaseModel):
    """Convenience compiler for the reference project's text-to-asset chain."""

    prompt: str = Field(min_length=1, max_length=20_000)
    image_model_id: str = "anima/generate"
    mesh_model_id: str = "trellis2/generate"
    enable_texture: bool = True
    enable_optimize: bool = True
    target_faces: int = Field(default=100_000, ge=100, le=1_000_000)
    collection: str = "Workflows"
    workflow_id: Optional[str] = None
    world_id: Optional[str] = None
    proto_id: Optional[str] = None
    image_params: dict = Field(default_factory=dict)
    mesh_params: dict = Field(default_factory=dict)
    texture_params: dict = Field(default_factory=dict)


def build_text_to_asset_workflow(request: TextToAssetRequest) -> WorkflowExecutionRequest:
    """Build a typed DAG without executing or hiding any model decisions."""

    image_params = dict(request.image_params)
    image_params.setdefault("filename_stem", "scene-asset")
    mesh_params = dict(request.mesh_params)
    mesh_params.setdefault("remesh", "none")
    mesh_params.setdefault("enable_texture", False)
    nodes = {
        "text": WorkflowExecutionNode(
            class_type="polykit.text",
            inputs={"text": request.prompt},
        ),
        "image": WorkflowExecutionNode(
            class_type=request.image_model_id,
            inputs={"text": ["text", "text"], "params": image_params},
        ),
        "cutout": WorkflowExecutionNode(
            class_type="image-background-remover/remove-background",
            inputs={
                "image": ["image", "image"],
                "params": {"model": "isnet-anime"},
            },
        ),
        "mesh": WorkflowExecutionNode(
            class_type=request.mesh_model_id,
            inputs={"image": ["cutout", "image"], "params": mesh_params},
        ),
    }
    final_node_id = "mesh"
    if request.enable_texture:
        texture_params = dict(request.texture_params)
        texture_params.setdefault("texture_resolution", 1024)
        texture_params.setdefault("texture_size", 2048)
        texture_params.setdefault("texture_steps", 12)
        nodes["texture"] = WorkflowExecutionNode(
            class_type="trellis2/refine",
            inputs={
                "image": ["cutout", "image"],
                "mesh": ["mesh", "mesh"],
                "params": texture_params,
            },
        )
        final_node_id = "texture"
    if request.enable_optimize:
        nodes["optimize"] = WorkflowExecutionNode(
            class_type="mesh-optimizer/optimize",
            inputs={
                "mesh": [final_node_id, "mesh"],
                "params": {"target_faces": request.target_faces},
            },
        )
        final_node_id = "optimize"
    nodes["output"] = WorkflowExecutionNode(
        class_type="polykit.output",
        inputs={"mesh": [final_node_id, "mesh"]},
    )
    return WorkflowExecutionRequest(
        workflow_id=request.workflow_id,
        prompt=nodes,
        output_node_id="output",
        collection=request.collection,
        metadata={
            key: value
            for key, value in {
                "world_id": request.world_id,
                "proto_id": request.proto_id,
            }.items()
            if isinstance(value, str) and value.strip()
        },
    )


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
        execution_prompt = select_execution_prompt(request)
        order = topological_order(execution_prompt)
        for node in execution_prompt.values():
            _require_known_class_type(node.class_type)
        if request.output_node_id is not None:
            output_node = request.prompt.get(request.output_node_id)
            if output_node is None or output_node.class_type not in SINK_NODES:
                raise ValueError("output_node_id must point to an output or preview sink")
        if not any(execution_prompt[node_id].class_type in SINK_NODES for node_id in order):
            raise ValueError("Workflow must include an output or preview sink")
        validate_prompt_links(request, execution_prompt)
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc

    run_coordinator.purge_old_jobs()
    job_id = str(uuid.uuid4())
    job = JobStatus(
        job_id=job_id,
        status="pending",
        progress=0,
        meta={
            "workflow_id": request.workflow_id,
            "collection": collection,
            **(
                {"workflow_metadata": dict(request.metadata)}
                if request.metadata
                else {}
            ),
        },
    )
    initialize_workflow_execution(job, request, order, workspace_root=runtime_paths.workspace)
    init_workflow_observability(job, request, execution_prompt, order)
    run_coordinator.register(job)
    model_runtime_registry.begin_generation(job_id)
    background_tasks.add_task(_run_workflow_dag, job_id, request)
    return {
        "run_id": job_id,
        "status": "pending",
        "workflow_id": request.workflow_id,
        "queued_nodes": len(execution_prompt),
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
    """Deliver the expected signal and resume the same durable WorkflowRun."""
    job = run_coordinator.jobs.get(run_id)
    if job is None:
        raise HTTPException(404, f"Run {run_id} not found")
    if job.status != "waiting":
        raise HTTPException(409, "WorkflowRun is not waiting for a signal")
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
        raise HTTPException(409, "Only failed or interrupted WorkflowRuns can be retried")
    try:
        request = load_workflow_execution_request(job, workspace_root=runtime_paths.workspace)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _queue_existing_run(run_id, request, background_tasks)


async def recover_interrupted_workflow_runs() -> None:
    """Requeue interrupted canonical workflows after FastAPI startup.

    Legacy image-generation jobs have no durable workflow execution snapshot and
    remain interrupted instead of being guessed/replayed.
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
        job.step = "Recovering interrupted workflow"
        run_coordinator.persist(job)
        model_runtime_registry.begin_generation(job.job_id)
        asyncio.create_task(_run_workflow_dag(job.job_id, request))


@router.post("/text-to-asset")
async def create_text_to_asset_run(
    request: TextToAssetRequest,
    background_tasks: BackgroundTasks,
):
    """Submit text → illustration → cutout → mesh → texture as one DAG."""

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
