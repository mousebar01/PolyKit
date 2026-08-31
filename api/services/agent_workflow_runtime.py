"""Durable execution semantics for Agent Workflow Protocol v1."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from schemas.agent_workflow import (
    AgentWorkflowDefinition,
    AgentWorkflowEvidence,
    AgentWorkflowSession,
    AgentWorkflowStep,
)
from services.agent_workflow_registry import get_agent_workflow
from services.agent_workflow_store import load_agent_workflow_session, save_agent_workflow_session


class AgentWorkflowStateError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _definition_steps(definition: AgentWorkflowDefinition) -> dict[str, AgentWorkflowStep]:
    return {step.id: step for step in definition.steps}


def _load(session_id: str) -> tuple[AgentWorkflowDefinition, AgentWorkflowSession]:
    session = AgentWorkflowSession.model_validate(load_agent_workflow_session(session_id))
    definition = get_agent_workflow(session.workflow_id)
    if session.workflow_version != definition.version:
        raise AgentWorkflowStateError(
            f"Workflow version mismatch for {session.id}: session={session.workflow_version}, definition={definition.version}"
        )
    return definition, session


def _save(session: AgentWorkflowSession) -> dict[str, Any]:
    session.updated_at = _now()
    return save_agent_workflow_session(session)


def create_agent_workflow_session(
    workflow_id: str,
    *,
    subject_kind: str,
    subject_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    definition = get_agent_workflow(workflow_id)
    if definition.subject_kind and definition.subject_kind != subject_kind:
        raise AgentWorkflowStateError(
            f"Workflow '{definition.id}' expects subject kind '{definition.subject_kind}', got '{subject_kind}'"
        )
    timestamp = _now()
    states = {step.id: {"status": "pending", "attempts": 0, "evidence": []} for step in definition.steps}
    states[definition.start_step]["status"] = "ready"
    session = AgentWorkflowSession.model_validate({
        "id": f"awf-{uuid4().hex}",
        "workflow_id": definition.id,
        "workflow_version": definition.version,
        "subject": {"kind": subject_kind, "id": subject_id},
        "status": "running",
        "current_step": definition.start_step,
        "steps": states,
        "metadata": dict(metadata or {}),
        "created_at": timestamp,
        "updated_at": timestamp,
    })
    return save_agent_workflow_session(session)


def get_agent_workflow_session(session_id: str) -> dict[str, Any]:
    return load_agent_workflow_session(session_id)


def next_agent_workflow_action(session_id: str) -> dict[str, Any]:
    definition, session = _load(session_id)
    if session.status != "running" or session.current_step is None:
        return {
            "session_id": session.id,
            "workflow_id": session.workflow_id,
            "status": session.status,
            "step": None,
            "action": "wait" if session.status.startswith("waiting_") else "none",
            "wait": session.wait.model_dump(mode="json") if session.wait else None,
        }

    step = _definition_steps(definition)[session.current_step]
    state = session.steps[step.id]
    if state.status not in {"ready", "running"}:
        raise AgentWorkflowStateError(
            f"Current step '{step.id}' has non-executable status '{state.status}'"
        )
    return {
        "session_id": session.id,
        "workflow_id": session.workflow_id,
        "status": session.status,
        "action": "execute" if state.status == "ready" else "resume",
        "step": step.model_dump(mode="json"),
        "attempt": state.attempts + (1 if state.status == "ready" else 0),
        "max_attempts": definition.limits.max_attempts_per_step,
        "corrections": session.corrections,
        "max_corrections": definition.limits.max_corrections,
    }


def begin_agent_workflow_step(session_id: str) -> dict[str, Any]:
    definition, session = _load(session_id)
    if session.status != "running" or session.current_step is None:
        raise AgentWorkflowStateError(f"Session '{session.id}' is not ready to execute a step")
    state = session.steps[session.current_step]
    if state.status == "running":
        return session.model_dump(mode="json")
    if state.status != "ready":
        raise AgentWorkflowStateError(f"Step '{session.current_step}' is not ready")
    if state.attempts >= definition.limits.max_attempts_per_step:
        raise AgentWorkflowStateError(
            f"Step '{session.current_step}' exceeded max attempts ({definition.limits.max_attempts_per_step})"
        )
    state.status = "running"
    state.attempts += 1
    state.started_at = _now()
    state.diagnostic = None
    session.steps[session.current_step] = state
    return _save(session)


def _normalized_evidence(values: list[Mapping[str, Any]] | None) -> list[AgentWorkflowEvidence]:
    return [AgentWorkflowEvidence.model_validate(dict(value)) for value in (values or [])]


def complete_agent_workflow_step(
    session_id: str,
    *,
    outcome: str,
    evidence: list[Mapping[str, Any]] | None = None,
    diagnostic: str | None = None,
) -> dict[str, Any]:
    definition, session = _load(session_id)
    if session.status != "running" or session.current_step is None:
        raise AgentWorkflowStateError(f"Session '{session.id}' is not running")

    steps = _definition_steps(definition)
    step = steps[session.current_step]
    state = session.steps[step.id]
    if state.status != "running":
        raise AgentWorkflowStateError(f"Step '{step.id}' must be running before it can complete")
    target = step.transitions.get(str(outcome or "").strip())
    if target is None:
        raise AgentWorkflowStateError(
            f"Outcome '{outcome}' is not allowed for step '{step.id}'; expected one of {', '.join(step.transitions)}"
        )

    submitted = _normalized_evidence(evidence)
    all_evidence = [*state.evidence, *submitted]
    evidence_kinds = {item.kind for item in all_evidence}
    missing = [kind for kind in step.completion.evidence if kind not in evidence_kinds]
    if missing:
        raise AgentWorkflowStateError(
            f"Step '{step.id}' is missing required evidence: {', '.join(missing)}"
        )

    timestamp = _now()
    state.status = "completed"
    state.evidence = all_evidence
    state.last_outcome = outcome
    state.diagnostic = diagnostic
    state.completed_at = timestamp
    session.steps[step.id] = state
    session.transition_count += 1
    if session.transition_count > definition.limits.max_transitions:
        session.status = "failed"
        session.current_step = None
        return _save(session)

    session.history.append({
        "from_step": step.id,
        "outcome": outcome,
        "to_step": target,
        "at": timestamp,
    })

    if target == "$complete":
        session.status = "completed"
        session.current_step = None
        return _save(session)
    if target == "$stop":
        session.status = "failed"
        session.current_step = None
        return _save(session)

    ordered = [item.id for item in definition.steps]
    if ordered.index(target) <= ordered.index(step.id):
        session.corrections += 1
        if session.corrections > definition.limits.max_corrections:
            session.status = "failed"
            session.current_step = None
            return _save(session)

    target_state = session.steps[target]
    target_state.status = "ready"
    target_state.completed_at = None
    target_state.last_outcome = None
    target_state.diagnostic = None
    session.steps[target] = target_state
    session.current_step = target
    return _save(session)


def wait_agent_workflow_session(
    session_id: str,
    *,
    kind: str,
    ref: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    _, session = _load(session_id)
    if session.status != "running":
        raise AgentWorkflowStateError(f"Session '{session.id}' is not running")
    if kind not in {"user", "run"}:
        raise AgentWorkflowStateError("Wait kind must be 'user' or 'run'")
    session.status = "waiting_for_user" if kind == "user" else "waiting_for_run"
    session.wait = {"kind": kind, "ref": ref, "reason": reason}
    return _save(session)


def pause_agent_workflow_session(session_id: str) -> dict[str, Any]:
    _, session = _load(session_id)
    if session.status in {"completed", "failed", "cancelled"}:
        raise AgentWorkflowStateError(f"Session '{session.id}' is terminal")
    session.status = "paused"
    return _save(session)


def resume_agent_workflow_session(session_id: str) -> dict[str, Any]:
    _, session = _load(session_id)
    if session.status not in {"paused", "waiting_for_user", "waiting_for_run"}:
        raise AgentWorkflowStateError(f"Session '{session.id}' is not paused or waiting")
    session.status = "running"
    session.wait = None
    return _save(session)


def cancel_agent_workflow_session(session_id: str) -> dict[str, Any]:
    _, session = _load(session_id)
    if session.status in {"completed", "failed", "cancelled"}:
        return session.model_dump(mode="json")
    session.status = "cancelled"
    session.current_step = None
    session.wait = None
    return _save(session)


__all__ = [
    "AgentWorkflowStateError",
    "begin_agent_workflow_step",
    "cancel_agent_workflow_session",
    "complete_agent_workflow_step",
    "create_agent_workflow_session",
    "get_agent_workflow_session",
    "next_agent_workflow_action",
    "pause_agent_workflow_session",
    "resume_agent_workflow_session",
    "wait_agent_workflow_session",
]
