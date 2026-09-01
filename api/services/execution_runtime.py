"""Runtime lifecycle for durable ExecutionPlan Runs.

HTTP routers and Agent adapters schedule this function; none of the execution
lifecycle belongs to a transport module. The durable checkpoint/observability
helpers still have legacy workflow-prefixed names during migration, but their
state is now used by every canonical Run.
"""
from __future__ import annotations

import asyncio
import traceback

from schemas.workflow import WorkflowExecutionRequest
from services.execution_engine import ExecutionEngine, ExecutionWait
from services.image_generation import workspace_url
from services.model_runtime_registry import model_runtime_registry
from services.process_runner import ProcessExecutionError
from services.run_coordinator import run_coordinator
from services.run_observability import (
    finalize_workflow_run,
    mark_workflow_run_started,
    observe_workflow_checkpoint,
)
from services.runtime_paths import runtime_paths
from services.workflow_execution import mark_current_step_failed
from services.workflow_executor import WorkflowError
from services.workspace_paths import normalize_collection


async def run_execution(job_id: str, request: WorkflowExecutionRequest) -> None:
    """Execute or resume a durable plan in the shared accelerator slot."""

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

        result = await ExecutionEngine().run(
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

        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        if metadata.get("bind_world_artifact") is True:
            world_id = str(metadata.get("world_id") or "").strip()
            proto_id = str(metadata.get("proto_id") or "").strip()
            if not world_id or not proto_id:
                raise WorkflowError("World asset binding requires world_id and proto_id metadata")
            try:
                from services.world_domain import attach_world_artifact
                from services.world_store import get_world, save_world
                world = get_world(world_id)
                if world is None:
                    raise WorkflowError(f"World '{world_id}' was not found for generated asset binding")
                workspace_path = final_artifact.relative_to(runtime_paths.workspace).as_posix()
                updated_world = attach_world_artifact(
                    world,
                    proto_id=proto_id,
                    workspace_path=workspace_path,
                    workflow_id=request.workflow_id,
                    run_id=job_id,
                )
                save_world(world_id, updated_world)
            except ValueError as exc:
                raise WorkflowError(f"Could not bind generated asset to World: {exc}") from exc

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


__all__ = ["run_execution"]
