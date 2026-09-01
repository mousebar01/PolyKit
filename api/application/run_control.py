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


_MAX_INSPECT_EVENTS = 200


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


def _event_seq(event: Any) -> int | None:
    if not isinstance(event, dict):
        return None
    seq = event.get("seq")
    if isinstance(seq, bool):
        return None
    try:
        value = int(seq)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _project_inspection_events(
    value: dict[str, Any],
    *,
    since_seq: int | None,
    before_seq: int | None,
    events_limit: int | None,
    include_events: bool,
) -> dict[str, Any]:
    """Project one reversible event page without changing durable Run state."""

    if since_seq is not None and before_seq is not None:
        raise RunStateError("since_seq and before_seq are mutually exclusive")
    if since_seq is not None and since_seq < 0:
        raise RunStateError("since_seq must be non-negative")
    if before_seq is not None and before_seq < 0:
        raise RunStateError("before_seq must be non-negative")
    if events_limit is not None and not (1 <= events_limit <= _MAX_INSPECT_EVENTS):
        raise RunStateError(f"events_limit must be between 1 and {_MAX_INSPECT_EVENTS}")

    result = dict(value)
    raw_events = value.get("events")
    events = list(raw_events) if isinstance(raw_events, list) else []
    sequenced = [(seq, event) for event in events if (seq := _event_seq(event)) is not None]
    latest_seq = max((seq for seq, _ in sequenced), default=0)
    earliest_seq = min((seq for seq, _ in sequenced), default=0)

    def _first(items: list[Any]) -> list[Any]:
        return items if events_limit is None else items[:events_limit]

    def _last(items: list[Any]) -> list[Any]:
        return items if events_limit is None else items[-events_limit:]

    if not include_events:
        selected: list[Any] = []
        next_seq = latest_seq
        previous_seq = 0
        has_more = False
        has_older = False
        truncated_before = bool(events)
    elif since_seq is not None:
        candidates = [event for seq, event in sequenced if seq > since_seq]
        selected = _first(candidates)
        next_seq = _event_seq(selected[-1]) if selected else since_seq
        previous_seq = _event_seq(selected[0]) if selected else since_seq
        has_more = len(candidates) > len(selected)
        has_older = any(seq <= since_seq for seq, _ in sequenced)
        truncated_before = False
    elif before_seq is not None:
        candidates = [event for seq, event in sequenced if seq < before_seq]
        selected = _last(candidates)
        previous_seq = _event_seq(selected[0]) if selected else before_seq
        next_seq = latest_seq
        has_more = False
        has_older = len(candidates) > len(selected)
        truncated_before = has_older
    else:
        selected = _last(events)
        next_seq = _event_seq(selected[-1]) if selected else latest_seq
        previous_seq = _event_seq(selected[0]) if selected else earliest_seq
        has_more = False
        has_older = len(events) > len(selected)
        truncated_before = has_older

    result["events"] = selected
    result["next_event_seq"] = int(next_seq or 0)
    result["previous_event_seq"] = int(previous_seq or 0)
    result["latest_event_seq"] = latest_seq
    result["has_more_events"] = has_more
    result["has_older_events"] = has_older
    result["events_truncated_before"] = truncated_before
    return result


def inspect_run(
    run_id: str,
    *,
    since_seq: int | None = None,
    before_seq: int | None = None,
    events_limit: int | None = None,
    include_events: bool = True,
) -> dict[str, Any]:
    """Return telemetry plus authoritative durable execution/checkpoint state.

    Event paging is a read projection only. Omitting all paging arguments keeps
    the full legacy inspection response; Agent callers can request a bounded
    page and continue in either direction with the returned cursors.
    """

    job = _job(run_id)
    value = inspect_workflow_run(job)
    value["execution"] = execution_summary(job)
    value["waiting"] = current_waiting(job)
    return _project_inspection_events(
        value,
        since_seq=since_seq,
        before_seq=before_seq,
        events_limit=events_limit,
        include_events=include_events,
    )


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
