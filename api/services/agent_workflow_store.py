"""Durable workspace store for Agent workflow sessions."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from schemas.agent_workflow import AgentWorkflowSession
from services.runtime_paths import runtime_paths

SESSION_ID_RE = re.compile(r"^awf-[0-9a-f]{32}$")
MAX_AGENT_WORKFLOW_SESSION_BYTES = 2 * 1024 * 1024


class AgentWorkflowStoreError(ValueError):
    pass


class AgentWorkflowSessionNotFound(AgentWorkflowStoreError):
    pass


def validate_agent_workflow_session_id(session_id: str) -> str:
    value = str(session_id or "").strip()
    if not SESSION_ID_RE.fullmatch(value):
        raise AgentWorkflowStoreError("Invalid Agent workflow session id")
    return value


def _session_root() -> Path:
    return runtime_paths.workspace / ".agent-workflows" / "sessions"


def agent_workflow_session_path(session_id: str) -> Path:
    return _session_root() / f"{validate_agent_workflow_session_id(session_id)}.json"


def _validate_session(value: Mapping[str, Any] | AgentWorkflowSession) -> AgentWorkflowSession:
    try:
        if isinstance(value, AgentWorkflowSession):
            return value
        return AgentWorkflowSession.model_validate(dict(value))
    except ValidationError as exc:
        raise AgentWorkflowStoreError(f"Invalid Agent workflow session: {exc}") from exc


def save_agent_workflow_session(value: Mapping[str, Any] | AgentWorkflowSession) -> dict[str, Any]:
    session = _validate_session(value)
    path = agent_workflow_session_path(session.id)
    payload = session.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_AGENT_WORKFLOW_SESSION_BYTES:
        raise AgentWorkflowStoreError("Agent workflow session is too large")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{session.id}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return payload


def load_agent_workflow_session(session_id: str) -> dict[str, Any]:
    path = agent_workflow_session_path(session_id)
    if not path.is_file():
        raise AgentWorkflowSessionNotFound(f"Agent workflow session not found: {session_id}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AgentWorkflowStoreError(f"Could not read Agent workflow session: {exc}") from exc
    if len(raw) > MAX_AGENT_WORKFLOW_SESSION_BYTES:
        raise AgentWorkflowStoreError("Agent workflow session is too large")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentWorkflowStoreError(f"Agent workflow session is not valid JSON: {exc}") from exc
    return _validate_session(parsed).model_dump(mode="json")


__all__ = [
    "AgentWorkflowSessionNotFound",
    "AgentWorkflowStoreError",
    "agent_workflow_session_path",
    "load_agent_workflow_session",
    "save_agent_workflow_session",
    "validate_agent_workflow_session_id",
]
