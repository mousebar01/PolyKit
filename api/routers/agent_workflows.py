"""HTTP API for durable Agent Workflow Protocol v1."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.agent_workflow_registry import (
    AgentWorkflowDefinitionError,
    get_agent_workflow,
    list_agent_workflows,
)
from services.agent_workflow_runtime import (
    AgentWorkflowStateError,
    begin_agent_workflow_step,
    cancel_agent_workflow_session,
    complete_agent_workflow_step,
    create_agent_workflow_session,
    get_agent_workflow_session,
    next_agent_workflow_action,
    pause_agent_workflow_session,
    resume_agent_workflow_session,
    wait_agent_workflow_session,
)
from services.agent_workflow_store import AgentWorkflowSessionNotFound, AgentWorkflowStoreError

router = APIRouter(prefix="/agent-workflows", tags=["agent-workflows"])


class AgentWorkflowStartRequest(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=120)
    subject_kind: str = Field(min_length=1, max_length=120)
    subject_id: str = Field(min_length=1, max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentWorkflowEvidenceInput(BaseModel):
    kind: str = Field(min_length=1, max_length=120)
    ref: str = Field(min_length=1, max_length=2000)
    summary: str | None = Field(default=None, max_length=4000)


class AgentWorkflowCompleteRequest(BaseModel):
    outcome: str = Field(min_length=1, max_length=120)
    evidence: list[AgentWorkflowEvidenceInput] = Field(default_factory=list, max_length=128)
    diagnostic: str | None = Field(default=None, max_length=8000)


class AgentWorkflowWaitRequest(BaseModel):
    kind: Literal["user", "run"]
    ref: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=4000)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentWorkflowSessionNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/definitions")
async def list_definitions():
    try:
        return [item.model_dump(mode="json") for item in list_agent_workflows()]
    except AgentWorkflowDefinitionError as exc:
        raise _http_error(exc) from exc


@router.get("/definitions/{workflow_id}")
async def read_definition(workflow_id: str):
    try:
        return get_agent_workflow(workflow_id).model_dump(mode="json")
    except AgentWorkflowDefinitionError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions")
async def start_session(request: AgentWorkflowStartRequest):
    try:
        return create_agent_workflow_session(
            request.workflow_id,
            subject_kind=request.subject_kind,
            subject_id=request.subject_id,
            metadata=request.metadata,
        )
    except (AgentWorkflowDefinitionError, AgentWorkflowStateError, AgentWorkflowStoreError) as exc:
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}")
async def read_session(session_id: str):
    try:
        return get_agent_workflow_session(session_id)
    except AgentWorkflowStoreError as exc:
        raise _http_error(exc) from exc


@router.get("/sessions/{session_id}/next")
async def next_action(session_id: str):
    try:
        return next_agent_workflow_action(session_id)
    except (AgentWorkflowDefinitionError, AgentWorkflowStateError, AgentWorkflowStoreError) as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/begin")
async def begin_step(session_id: str):
    try:
        return begin_agent_workflow_step(session_id)
    except (AgentWorkflowDefinitionError, AgentWorkflowStateError, AgentWorkflowStoreError) as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/complete")
async def complete_step(session_id: str, request: AgentWorkflowCompleteRequest):
    try:
        return complete_agent_workflow_step(
            session_id,
            outcome=request.outcome,
            evidence=[item.model_dump(mode="json") for item in request.evidence],
            diagnostic=request.diagnostic,
        )
    except (AgentWorkflowDefinitionError, AgentWorkflowStateError, AgentWorkflowStoreError) as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/wait")
async def wait_session(session_id: str, request: AgentWorkflowWaitRequest):
    try:
        return wait_agent_workflow_session(session_id, kind=request.kind, ref=request.ref, reason=request.reason)
    except (AgentWorkflowDefinitionError, AgentWorkflowStateError, AgentWorkflowStoreError) as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/pause")
async def pause_session(session_id: str):
    try:
        return pause_agent_workflow_session(session_id)
    except (AgentWorkflowDefinitionError, AgentWorkflowStateError, AgentWorkflowStoreError) as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str):
    try:
        return resume_agent_workflow_session(session_id)
    except (AgentWorkflowDefinitionError, AgentWorkflowStateError, AgentWorkflowStoreError) as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str):
    try:
        return cancel_agent_workflow_session(session_id)
    except (AgentWorkflowDefinitionError, AgentWorkflowStateError, AgentWorkflowStoreError) as exc:
        raise _http_error(exc) from exc
