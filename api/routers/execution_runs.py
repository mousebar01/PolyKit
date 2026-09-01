"""Canonical Run API independent of saved Workflow definitions.

The existing /workflow-runs routes remain compatibility endpoints while clients
migrate. New manual, Agent, and CLI integrations should submit application
commands or ExecutionPlans here.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from application.execution import prepare_execution_run
from application.generate_asset import GenerateAssetCommand, compile_generate_asset_plan
from application.run_control import (
    RunNotFoundError,
    RunStateError,
    cancel_run as cancel_application_run,
    inspect_run as inspect_application_run,
    prepare_run_retry,
    prepare_run_signal,
)
from schemas.execution import ExecutionInitiator, ExecutionPlan
from services.execution_runtime import run_execution
from services.run_coordinator import run_coordinator


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


class RunSignalRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    payload: Any = None


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


def _schedule_resume(prepared, background_tasks: BackgroundTasks) -> dict[str, Any]:
    background_tasks.add_task(run_execution, prepared.run_id, prepared.request)
    return {"run_id": prepared.run_id, "status": "pending", "resumed": True}


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


@router.get("/runs/{run_id}/inspect")
async def inspect_run(run_id: str):
    """Inspect durable Run telemetry, checkpoints, and waiting state."""

    try:
        return inspect_application_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/runs/{run_id}/signals")
async def signal_run(run_id: str, signal: RunSignalRequest, background_tasks: BackgroundTasks):
    """Deliver an expected external signal and resume the same Run id."""

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
    result = _schedule_resume(prepared, background_tasks)
    return {**result, "signal_id": accepted["id"], "signal_name": accepted["name"]}


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str, background_tasks: BackgroundTasks):
    """Resume a failed/interrupted Run from durable completed-node checkpoints."""

    try:
        prepared = prepare_run_retry(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RunStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _schedule_resume(prepared, background_tasks)


@router.delete("/runs/{run_id}")
async def cancel_run(run_id: str):
    """Cancel one Run through the shared Application control boundary."""

    try:
        job = cancel_application_run(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"run_id": run_id, "status": job.status}


__all__ = ["router"]
