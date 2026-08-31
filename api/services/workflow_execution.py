"""Durable execution state for canonical WorkflowRuns.

This module is deliberately separate from ``run_observability``. Observability
records what happened; this execution state owns the minimum facts required to
resume the same WorkflowRun after a process restart or an explicit interrupt.

The state itself is persisted with ``JobStatus.meta`` by RunStore. Potentially
large execution requests and node outputs are file-backed under
``WORKSPACE/.run-checkpoints/<run-id>`` so SQLite does not become an artifact
store.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from schemas.workflow import WorkflowExecutionRequest
from services.image_artifacts import ImageArtifact
from services.mesh_artifacts import MeshArtifact
from services.runtime_paths import runtime_paths
from services.workspace_paths import resolve_workspace_path


EXECUTION_STATE_KEY = "execution"
EXECUTION_STATE_VERSION = 1
INTERRUPT_NODE = "polykit.interrupt"
_CHECKPOINT_DIR = ".run-checkpoints"
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta(job: Any) -> dict[str, Any]:
    raw = getattr(job, "meta", None)
    value = dict(raw) if isinstance(raw, Mapping) else {}
    job.meta = value
    return value


def execution_state(job: Any) -> dict[str, Any] | None:
    value = _meta(job).get(EXECUTION_STATE_KEY)
    return value if isinstance(value, dict) else None


def _safe_component(value: str) -> str:
    cleaned = _SAFE_RE.sub("_", str(value or "")).strip("._-")[:48]
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:10]
    return f"{cleaned or 'node'}-{digest}"


def checkpoint_root(run_id: str, *, workspace_root: Path | None = None) -> Path:
    root = (workspace_root or runtime_paths.workspace).expanduser().resolve()
    return root / _CHECKPOINT_DIR / _safe_component(run_id)


def _workspace_ref(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def initialize_workflow_execution(
    job: Any,
    request: WorkflowExecutionRequest,
    order: Sequence[str],
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Create the durable execution snapshot before a run is queued.

    Calling this again for the same run is idempotent; existing step/checkpoint
    state wins so a resume cannot accidentally erase completed work.
    """

    existing = execution_state(job)
    if existing is not None:
        if int(existing.get("version") or 0) != EXECUTION_STATE_VERSION:
            raise ValueError("WorkflowRun execution state version is not supported")
        return existing

    root = (workspace_root or runtime_paths.workspace).expanduser().resolve()
    run_root = checkpoint_root(str(getattr(job, "job_id", "")), workspace_root=root)
    run_root.mkdir(parents=True, exist_ok=True)
    request_path = run_root / "request.json"
    request_payload = request.model_dump(mode="json")
    request_path.write_text(
        json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    steps: dict[str, Any] = {}
    for node_id in order:
        node = request.prompt[node_id]
        steps[node_id] = {
            "node_id": node_id,
            "class_type": node.class_type,
            "status": "pending",
            "attempt": 0,
            "input_signature": None,
            "checkpoint": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
    state = {
        "version": EXECUTION_STATE_VERSION,
        "request_ref": _workspace_ref(request_path, root),
        "order": list(order),
        "steps": steps,
        "waiting": None,
        "signals": [],
    }
    _meta(job)[EXECUTION_STATE_KEY] = state
    return state


def load_workflow_execution_request(
    job: Any,
    *,
    workspace_root: Path | None = None,
) -> WorkflowExecutionRequest:
    state = execution_state(job)
    if state is None:
        raise ValueError("WorkflowRun has no durable execution snapshot")
    ref = state.get("request_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("WorkflowRun execution request snapshot is missing")
    root = (workspace_root or runtime_paths.workspace).expanduser().resolve()
    path = resolve_workspace_path(root, ref)
    if not path.is_file():
        raise ValueError("WorkflowRun execution request snapshot no longer exists")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("WorkflowRun execution request snapshot is unreadable") from exc
    return WorkflowExecutionRequest.model_validate(value)


def _step(job: Any, node_id: str) -> dict[str, Any]:
    state = execution_state(job)
    if state is None:
        raise ValueError("WorkflowRun execution state is not initialized")
    steps = state.get("steps")
    if not isinstance(steps, dict) or not isinstance(steps.get(node_id), dict):
        raise ValueError(f"WorkflowRun has no durable step '{node_id}'")
    return steps[node_id]


def mark_step_started(job: Any, node_id: str) -> None:
    step = _step(job, node_id)
    step["status"] = "running"
    step["attempt"] = int(step.get("attempt") or 0) + 1
    step["started_at"] = _now()
    step["finished_at"] = None
    step["error"] = None


def _copy_file(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Workflow checkpoint source does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _serialize_value(
    value: Any,
    *,
    node_root: Path,
    workspace_root: Path,
    counter: list[int],
) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        index = counter[0]
        counter[0] += 1
        path = node_root / f"bytes-{index}.bin"
        path.write_bytes(value)
        return {"$type": "bytes", "workspace_path": _workspace_ref(path, workspace_root)}
    if isinstance(value, MeshArtifact):
        index = counter[0]
        counter[0] += 1
        suffix = value.path.suffix or ".bin"
        path = node_root / f"mesh-{index}{suffix}"
        _copy_file(value.path, path)
        return {
            "$type": "mesh",
            "workspace_path": _workspace_ref(path, workspace_root),
            "coordinate_space": value.coordinate_space,
        }
    if isinstance(value, ImageArtifact):
        index = counter[0]
        counter[0] += 1
        suffix = value.path.suffix or ".bin"
        path = node_root / f"image-{index}{suffix}"
        _copy_file(value.path, path)
        return {"$type": "image", "workspace_path": _workspace_ref(path, workspace_root)}
    if isinstance(value, Path):
        index = counter[0]
        counter[0] += 1
        suffix = value.suffix or ".bin"
        path = node_root / f"file-{index}{suffix}"
        _copy_file(value, path)
        return {"$type": "path", "workspace_path": _workspace_ref(path, workspace_root)}
    if isinstance(value, Mapping):
        return {
            str(key): _serialize_value(
                item,
                node_root=node_root,
                workspace_root=workspace_root,
                counter=counter,
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _serialize_value(
                item,
                node_root=node_root,
                workspace_root=workspace_root,
                counter=counter,
            )
            for item in value
        ]
    raise ValueError(f"Workflow node output is not checkpointable: {type(value).__name__}")


def _deserialize_value(value: Any, *, workspace_root: Path) -> Any:
    if isinstance(value, list):
        return [_deserialize_value(item, workspace_root=workspace_root) for item in value]
    if isinstance(value, dict):
        kind = value.get("$type")
        ref = value.get("workspace_path")
        if isinstance(kind, str) and isinstance(ref, str):
            path = resolve_workspace_path(workspace_root, ref)
            if not path.is_file():
                raise ValueError(f"Workflow checkpoint artifact is missing: {ref}")
            if kind == "bytes":
                return path.read_bytes()
            if kind == "mesh":
                return MeshArtifact(
                    path=path,
                    coordinate_space=str(value.get("coordinate_space") or "unknown"),
                    persistent=True,
                    origin="run-checkpoint",
                )
            if kind == "image":
                return ImageArtifact(path=path, persistent=True, origin="run-checkpoint")
            if kind == "path":
                return path
            raise ValueError(f"Unknown workflow checkpoint value type: {kind}")
        return {str(key): _deserialize_value(item, workspace_root=workspace_root) for key, item in value.items()}
    return value


def mark_step_completed(
    job: Any,
    node_id: str,
    outputs: Mapping[str, Any],
    *,
    input_signature: str,
    workspace_root: Path | None = None,
) -> None:
    root = (workspace_root or runtime_paths.workspace).expanduser().resolve()
    node_root = checkpoint_root(str(getattr(job, "job_id", "")), workspace_root=root) / "steps" / _safe_component(node_id)
    shutil.rmtree(node_root, ignore_errors=True)
    node_root.mkdir(parents=True, exist_ok=True)
    checkpoint = _serialize_value(
        dict(outputs),
        node_root=node_root,
        workspace_root=root,
        counter=[0],
    )
    step = _step(job, node_id)
    step["status"] = "done"
    step["input_signature"] = input_signature
    step["checkpoint"] = checkpoint
    step["finished_at"] = _now()
    step["error"] = None


def mark_step_failed(job: Any, node_id: str, error: str) -> None:
    step = _step(job, node_id)
    step["status"] = "failed"
    step["finished_at"] = _now()
    step["error"] = {"message": str(error)}


def mark_current_step_failed(job: Any, error: str) -> str | None:
    state = execution_state(job)
    steps = state.get("steps") if isinstance(state, Mapping) else None
    order = state.get("order") if isinstance(state, Mapping) else None
    if not isinstance(steps, Mapping) or not isinstance(order, list):
        return None
    for node_id in reversed(order):
        step = steps.get(node_id)
        if isinstance(step, dict) and step.get("status") == "running":
            mark_step_failed(job, str(node_id), error)
            return str(node_id)
    return None


def restore_completed_steps(
    job: Any,
    *,
    workspace_root: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    root = (workspace_root or runtime_paths.workspace).expanduser().resolve()
    state = execution_state(job)
    if state is None:
        return {}, {}
    steps = state.get("steps")
    order = state.get("order")
    if not isinstance(steps, dict) or not isinstance(order, list):
        return {}, {}
    outputs: dict[str, dict[str, Any]] = {}
    signatures: dict[str, str] = {}
    for raw_node_id in order:
        node_id = str(raw_node_id)
        step = steps.get(node_id)
        if not isinstance(step, dict) or step.get("status") != "done":
            continue
        checkpoint = step.get("checkpoint")
        signature = step.get("input_signature")
        try:
            restored = _deserialize_value(checkpoint, workspace_root=root)
        except (OSError, ValueError):
            step["status"] = "pending"
            step["checkpoint"] = None
            step["input_signature"] = None
            step["finished_at"] = None
            continue
        if not isinstance(restored, dict) or not isinstance(signature, str) or not signature:
            step["status"] = "pending"
            continue
        outputs[node_id] = restored
        signatures[node_id] = signature
    return outputs, signatures


def mark_step_waiting(job: Any, node_id: str, *, signal_name: str, prompt: str) -> dict[str, Any]:
    state = execution_state(job)
    if state is None:
        raise ValueError("WorkflowRun execution state is not initialized")
    step = _step(job, node_id)
    step["status"] = "waiting"
    step["finished_at"] = None
    waiting = {
        "node_id": node_id,
        "signal_name": signal_name,
        "prompt": prompt,
        "created_at": _now(),
    }
    state["waiting"] = waiting
    return waiting


def current_waiting(job: Any) -> dict[str, Any] | None:
    state = execution_state(job)
    value = state.get("waiting") if isinstance(state, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else None


def submit_signal(job: Any, *, name: str, payload: Any) -> dict[str, Any]:
    state = execution_state(job)
    if state is None:
        raise ValueError("WorkflowRun has no durable execution state")
    waiting = current_waiting(job)
    if waiting is None:
        raise ValueError("WorkflowRun is not waiting for a signal")
    expected = str(waiting.get("signal_name") or "")
    if name != expected:
        raise ValueError(f"WorkflowRun is waiting for signal '{expected}', not '{name}'")
    signals = state.setdefault("signals", [])
    if not isinstance(signals, list):
        signals = []
        state["signals"] = signals
    signal = {
        "id": str(uuid.uuid4()),
        "node_id": str(waiting.get("node_id") or ""),
        "name": name,
        "payload": payload,
        "created_at": _now(),
        "consumed_at": None,
    }
    signals.append(signal)
    return signal


def pending_signal(job: Any, node_id: str, signal_name: str) -> dict[str, Any] | None:
    state = execution_state(job)
    signals = state.get("signals") if isinstance(state, Mapping) else None
    if not isinstance(signals, list):
        return None
    for item in signals:
        if not isinstance(item, Mapping):
            continue
        if (
            item.get("node_id") == node_id
            and item.get("name") == signal_name
            and item.get("consumed_at") is None
        ):
            return dict(item)
    return None


def consume_signal(job: Any, signal_id: str) -> None:
    state = execution_state(job)
    if state is None:
        return
    signals = state.get("signals")
    if isinstance(signals, list):
        for item in signals:
            if isinstance(item, dict) and item.get("id") == signal_id:
                item["consumed_at"] = _now()
                break
    state["waiting"] = None


def prepare_execution_resume(job: Any) -> None:
    """Reset only incomplete work; completed checkpoints remain authoritative."""
    state = execution_state(job)
    if state is None:
        raise ValueError("WorkflowRun has no durable execution state")
    steps = state.get("steps")
    if isinstance(steps, dict):
        for step in steps.values():
            if isinstance(step, dict) and step.get("status") in {"running", "failed"}:
                step["status"] = "pending"
                step["started_at"] = None
                step["finished_at"] = None
                step["error"] = None


def execution_summary(job: Any) -> dict[str, Any] | None:
    state = execution_state(job)
    if state is None:
        return None
    steps = state.get("steps")
    return {
        "version": int(state.get("version") or EXECUTION_STATE_VERSION),
        "order": list(state.get("order") or []),
        "steps": {
            str(node_id): {
                key: value
                for key, value in step.items()
                if key != "checkpoint"
            }
            for node_id, step in (steps.items() if isinstance(steps, Mapping) else [])
            if isinstance(step, Mapping)
        },
        "waiting": current_waiting(job),
        "signals": [
            {
                "id": item.get("id"),
                "node_id": item.get("node_id"),
                "name": item.get("name"),
                "created_at": item.get("created_at"),
                "consumed_at": item.get("consumed_at"),
            }
            for item in (state.get("signals") or [])
            if isinstance(item, Mapping)
        ],
    }


def delete_run_checkpoints(run_id: str, *, workspace_root: Path | None = None) -> None:
    shutil.rmtree(checkpoint_root(run_id, workspace_root=workspace_root), ignore_errors=True)


__all__ = [
    "EXECUTION_STATE_KEY",
    "EXECUTION_STATE_VERSION",
    "INTERRUPT_NODE",
    "checkpoint_root",
    "consume_signal",
    "current_waiting",
    "delete_run_checkpoints",
    "execution_state",
    "execution_summary",
    "initialize_workflow_execution",
    "load_workflow_execution_request",
    "mark_current_step_failed",
    "mark_step_completed",
    "mark_step_started",
    "mark_step_waiting",
    "pending_signal",
    "prepare_execution_resume",
    "restore_completed_steps",
    "submit_signal",
]
