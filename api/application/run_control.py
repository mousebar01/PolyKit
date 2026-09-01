"""Application-level control operations for durable Runs.

Run retry, signal delivery, inspection, and cancellation are product operations,
not Workflow-definition behavior. HTTP and MCP adapters share these helpers so
all initiators observe the same durable state transitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemas.workflow import WorkflowExecutionRequest
from services.model_runtime_registry import model_runtime_registry
from services.run_coordinator import run_coordinator
from services.run_observability import finalize_workflow_run, inspect_workflow_run
from services.runtime_paths import runtime_paths
from services.workflow_execution import (
    current_waiting,
    execution_summary,
    load_workflow_execution_request,
    prepare_execution_resume,
    submit_signal,
)


class RunNotFoundError(LookupError):
    """Raised when a durable Run id is unknown."""


class RunStateError(ValueError):
    """Raised when an operation is invalid for the Run's current state."""


@dataclass(frozen=True)
class PreparedResume:
    """An existing durable Run prepared for another execution pass."""

    run_id: str
    request: WorkflowExecutionRequest


def _job(run_id: str):
    job = run_coordinator.jobs.get(run_id)
    if job is None:
        raise RunNotFoundError(f"Run {run_id} not found")
    return job


def _prepare_resume(run_id: str, request: WorkflowExecutionRequest) -> PreparedResume:
    job = _job(run_id)
    prepare_execution_resume(job)
    run_coordinator.clear_completed(run_id)
    run_coordinator.ensure_cancel_event(run_id)
    job.status = "pending"
    job.error = None
    job.step = "Queued for resume"
    run_coordinator.persist(job)
    model_runtime_registry.begin_generation(run_id)
    return PreparedResume(run_id=run_id, request=request)


def prepare_run_retry(run_id: str) -> PreparedResume:
    """Prepare a failed/interrupted Run to resume from durable checkpoints."""

    job = _job(run_id)
    if job.status not in {"error", "interrupted"}:
        raise RunStateError("Only failed or interrupted Runs can be retried")
    try:
        request = load_workflow_execution_request(job, workspace_root=runtime_paths.workspace)
    except ValueError as exc:
        raise RunStateError(str(exc)) from exc
    return _prepare_resume(run_id, request)


def prepare_run_signal(run_id: str, *, name: str, payload: Any = None) -> tuple[PreparedResume, dict[str, Any]]:
    """Deliver one expected signal and prepare the same Run for resume."""

    job = _job(run_id)
    if job.status != "waiting":
        raise RunStateError("Run is not waiting for a signal")
    try:
        accepted = submit_signal(job, name=name, payload=payload)
        request = load_workflow_execution_request(job, workspace_root=runtime_paths.workspace)
    except ValueError as exc:
        raise RunStateError(str(exc)) from exc
    run_coordinator.persist(job)
    return _prepare_resume(run_id, request), accepted


def inspect_run(run_id: str) -> dict[str, Any]:
    """Return telemetry plus authoritative durable execution/checkpoint state."""

    job = _job(run_id)
    value = inspect_workflow_run(job)
    value["execution"] = execution_summary(job)
    value["waiting"] = current_waiting(job)
    return value


def cancel_run(run_id: str):
    """Cancel one Run and finalize its observability record."""

    job = run_coordinator.cancel(run_id)
    if job is None:
        raise RunNotFoundError(f"Run {run_id} not found")
    finalize_workflow_run(job, status="cancelled")
    run_coordinator.persist(job)
    return job


__all__ = [
    "PreparedResume",
    "RunNotFoundError",
    "RunStateError",
    "cancel_run",
    "inspect_run",
    "prepare_run_retry",
    "prepare_run_signal",
]
