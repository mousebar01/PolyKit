from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from services.runtime_paths import runtime_paths


_lock = threading.RLock()


def _workflows_dir() -> Path:
    path = runtime_paths.workflows
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_workflows_dir(path: Path) -> Path:
    """Update the canonical workflow-definition root."""
    with _lock:
        return runtime_paths.update(workflows_dir=path).workflows


def _workflow_id(workflow: dict[str, Any]) -> str:
    workflow_id = str(workflow.get("id") or "").strip()
    if not workflow_id:
        raise ValueError("Workflow id is required")
    return workflow_id


def _validate_workflow(workflow: dict[str, Any]) -> None:
    _workflow_id(workflow)
    if not isinstance(workflow.get("name"), str):
        raise ValueError("Workflow name must be a string")

    nodes = workflow.get("nodes")
    edges = workflow.get("edges")
    if isinstance(nodes, list) and isinstance(edges, list):
        return
    if isinstance(workflow.get("blocks"), list):
        return
    raise ValueError("Workflow must contain nodes/edges or a legacy blocks list")


def _workflow_path(workflow_id: str) -> Path:
    digest = hashlib.sha256(workflow_id.encode("utf-8")).hexdigest()
    return _workflows_dir() / f"{digest}.json"


def _read_workflow(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None
        _validate_workflow(value)
        return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def _write_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    _validate_workflow(workflow)
    workflow_id = _workflow_id(workflow)
    encoded = json.dumps(workflow, ensure_ascii=False, indent=2)
    normalized = json.loads(encoded)
    workflows_dir = _workflows_dir()
    destination = _workflow_path(workflow_id)

    temporary = workflows_dir / f".{destination.stem}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(encoded + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return normalized


def _matching_workflow_files(workflow_id: str) -> list[Path]:
    matches: list[Path] = []
    for path in _workflows_dir().glob("*.json"):
        value = _read_workflow(path)
        if value is not None and _workflow_id(value) == workflow_id:
            matches.append(path)
    return matches


def _remove_duplicate_files(workflow_id: str, keep: Path) -> None:
    for path in _matching_workflow_files(workflow_id):
        if path != keep:
            path.unlink(missing_ok=True)


def list_workflows() -> list[dict[str, Any]]:
    with _lock:
        newest_by_id: dict[str, tuple[dict[str, Any], Path]] = {}
        for path in _workflows_dir().glob("*.json"):
            value = _read_workflow(path)
            if value is None:
                continue
            workflow_id = _workflow_id(value)
            current = newest_by_id.get(workflow_id)
            if current is None:
                newest_by_id[workflow_id] = (value, path)
                continue

            current_value, current_path = current
            candidate_key = (
                str(value.get("updatedAt") or ""),
                path == _workflow_path(workflow_id),
                path.stat().st_mtime_ns,
            )
            current_key = (
                str(current_value.get("updatedAt") or ""),
                current_path == _workflow_path(workflow_id),
                current_path.stat().st_mtime_ns,
            )
            if candidate_key > current_key:
                newest_by_id[workflow_id] = (value, path)

        workflows: list[dict[str, Any]] = []
        for workflow_id, (workflow, source_path) in newest_by_id.items():
            destination = _workflow_path(workflow_id)
            if source_path != destination:
                workflow = _write_workflow(workflow)
            _remove_duplicate_files(workflow_id, destination)
            workflows.append(workflow)

        workflows.sort(key=lambda workflow: str(workflow.get("updatedAt") or ""), reverse=True)
        return workflows


def save_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        normalized = _write_workflow(workflow)
        workflow_id = _workflow_id(normalized)
        _remove_duplicate_files(workflow_id, _workflow_path(workflow_id))
        return normalized


def delete_workflow(workflow_id: str) -> bool:
    workflow_id = workflow_id.strip()
    if not workflow_id:
        raise ValueError("Workflow id is required")
    with _lock:
        paths = _matching_workflow_files(workflow_id)
        canonical = _workflow_path(workflow_id)
        if canonical.exists() and canonical not in paths:
            paths.append(canonical)
        for path in paths:
            path.unlink(missing_ok=True)
        return bool(paths)
