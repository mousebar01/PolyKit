"""Canonical Run API independent of saved Workflow definitions.

The existing /workflow-runs routes remain compatibility endpoints while clients
migrate. New manual, Agent, and CLI integrations should submit application
commands or ExecutionPlans here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from application.execution import prepare_execution_run
from application.generate_asset import GenerateAssetCommand, compile_generate_asset_plan
from schemas.execution import ExecutionInitiator, ExecutionPlan
from services.execution_runtime import run_execution
from services.run_coordinator import run_coordinator
from services.run_observability import finalize_workflow_run


router = APIRouter(tags=["runs"])


class RunSubmission(BaseModel):
    plan: ExecutionPlan
    initiator: ExecutionInitiator = Field(
        default_factory=lambda: ExecutionInitiator(type="user", surface="api")
    )


class GenerateAssetSubmission(GenerateAssetCommand):
    initiator: ExecutionInitiator = Field(
        default_factory=lambda: ExecutionInitiator(type="user", surface="assets.generate")
    )


class RunStatus(BaseModel):
    run_id: str
    status: str
    progress: int = 0
    step: Optional[str] = None
    output_url: Optional[str] = None
    error: Optional[str] = None
    scene_candidate: Optional[dict] = None
    meta: Optional[dict] = None


def _schedule(prepared, background_tasks: BackgroundTasks) -> dict:
    background_tasks.add_task(run_execution, prepared.run_id, prepared.request)
    source = prepared.request.source.model_dump(mode="json") if prepared.request.source else None
    return {
        "run_id": prepared.run_id,
        "status": "pending",
        "source": source,
        "workflow_id": prepared.request.workflow_id,
        "queued_nodes": prepared.queued_nodes,
    }


@router.post("/runs")
async def create_run(submission: RunSubmission, background_tasks: BackgroundTasks):
    """Validate an ExecutionPlan, create a durable Run, and queue execution."""

    try:
        prepared = prepare_execution_run(
            submission.plan,
            initiator=submission.initiator,
        )
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _schedule(prepared, background_tasks)


@router.post("/commands/generate-asset")
async def generate_asset(submission: GenerateAssetSubmission, background_tasks: BackgroundTasks):
    """Run the same GenerateAsset command for manual, Agent, or CLI callers."""

    command = GenerateAssetCommand.model_validate(
        submission.model_dump(exclude={"initiator"})
    )
    try:
        plan = compile_generate_asset_plan(command)
        prepared = prepare_execution_run(plan, initiator=submission.initiator)
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return _schedule(prepared, background_tasks)


@router.get("/runs/{run_id}", response_model=RunStatus)
async def get_run(run_id: str):
    """Read one Run regardless of whether it came from UI, Agent, Workflow, or World."""

    run_coordinator.purge_old_jobs()
    job = run_coordinator.jobs.get(run_id)
    if job is None:
        raise HTTPException(404, "Run not found")
    scene_candidate = None
    if job.status == "done" and job.output_url:
        scene_candidate = {"workspace_path": job.output_url.removeprefix("/workspace/")}
    return RunStatus(
        run_id=job.job_id,
        status=job.status,
        progress=job.progress,
        step=job.step,
        output_url=job.output_url,
        error=job.error,
        scene_candidate=scene_candidate,
        meta=job.meta,
    )


@router.delete("/runs/{run_id}")
async def cancel_run(run_id: str):
    """Cancel one Run through the shared coordinator."""

    job = run_coordinator.cancel(run_id)
    if job is None:
        raise HTTPException(404, "Run not found")
    finalize_workflow_run(job, status="cancelled")
    run_coordinator.persist(job)
    return {"run_id": run_id, "status": job.status}


__all__ = ["router"]
