"""Structured, persisted observability for canonical Workflow Runs.

The data lives inside ``JobStatus.meta`` so it follows the existing RunStore
lifecycle. It is execution telemetry, not an orchestration state machine: it
records what ran, what happened, and what artifacts/evidence were produced.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from schemas.workflow import WorkflowExecutionNode, WorkflowExecutionRequest


OBSERVABILITY_KEY = "observability"
OBSERVABILITY_VERSION = 1
_NODE_INDEX_RE = re.compile(r"\((\d+)/(\d+)\)\s*$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta(job: Any) -> dict[str, Any]:
    value = getattr(job, "meta", None)
    meta = dict(value) if isinstance(value, Mapping) else {}
    job.meta = meta
    return meta


def _obs(job: Any) -> dict[str, Any] | None:
    value = _meta(job).get(OBSERVABILITY_KEY)
    return value if isinstance(value, dict) else None


def _event(obs: dict[str, Any], event_type: str, **payload: Any) -> None:
    events = obs.setdefault("events", [])
    if not isinstance(events, list):
        events = []
        obs["events"] = events
    events.append({
        "seq": len(events) + 1,
        "type": event_type,
        "time": _now(),
        **{key: value for key, value in payload.items() if value is not None},
    })


def init_workflow_observability(
    job: Any,
    request: WorkflowExecutionRequest,
    execution_prompt: Mapping[str, WorkflowExecutionNode],
    order: Sequence[str],
) -> dict[str, Any]:
    """Attach a versioned, queryable execution record before the run is queued."""

    nodes: dict[str, Any] = {}
    total = len(order)
    for index, node_id in enumerate(order):
        node = execution_prompt[node_id]
        nodes[node_id] = {
            "node_id": node_id,
            "class_type": node.class_type,
            "index": index + 1,
            "total": total,
            "status": "pending",
            "progress": 0,
            "cached": False,
            "started_at": None,
            "finished_at": None,
            "phase": None,
            "error": None,
        }

    obs: dict[str, Any] = {
        "version": OBSERVABILITY_VERSION,
        "workflow_id": request.workflow_id,
        "current_node_id": None,
        "nodes": nodes,
        "order": list(order),
        "artifacts": [],
        "evidence": [],
        "events": [],
        "last_checkpoint": {"step": None, "progress": 0},
    }
    _meta(job)[OBSERVABILITY_KEY] = obs
    _event(obs, "run.queued", workflow_id=request.workflow_id, node_count=total)
    return obs


def mark_workflow_run_started(job: Any) -> None:
    obs = _obs(job)
    if obs is None:
        return
    if not any(item.get("type") == "run.started" for item in obs.get("events", []) if isinstance(item, Mapping)):
        _event(obs, "run.started")


def _finish_node(obs: dict[str, Any], node_id: str, *, status: str = "done", error: str | None = None) -> None:
    nodes = obs.get("nodes")
    if not isinstance(nodes, dict):
        return
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        return
    if node.get("status") in {"done", "cached", "failed", "cancelled"}:
        return
    node["status"] = status
    node["progress"] = 100 if status in {"done", "cached"} else node.get("progress", 0)
    node["finished_at"] = _now()
    if error:
        node["error"] = {"message": error}
    _event(
        obs,
        "node.completed" if status in {"done", "cached"} else f"node.{status}",
        node_id=node_id,
        class_type=node.get("class_type"),
        cached=bool(node.get("cached")) if status in {"done", "cached"} else None,
        error=error,
    )


def _node_progress(node: Mapping[str, Any], run_progress: int) -> int:
    index = int(node.get("index") or 1) - 1
    total = max(1, int(node.get("total") or 1))
    start = round(90 * index / total)
    end = round(90 * (index + 1) / total)
    if end <= start:
        return 100 if run_progress >= end else 0
    return max(0, min(100, round((run_progress - start) * 100 / (end - start))))


def observe_workflow_checkpoint(job: Any) -> None:
    """Translate the engine's existing progress checkpoints into structured events.

    The engine remains authoritative for execution. This adapter only observes
    ``job.step``/``job.progress`` and the pre-recorded DAG order.
    """

    obs = _obs(job)
    if obs is None:
        return
    step = str(getattr(job, "step", "") or "")
    run_progress = max(0, min(100, int(getattr(job, "progress", 0) or 0)))
    checkpoint = obs.setdefault("last_checkpoint", {"step": None, "progress": 0})
    if checkpoint.get("step") == step and checkpoint.get("progress") == run_progress:
        return

    nodes = obs.get("nodes")
    order = obs.get("order")
    if not isinstance(nodes, dict) or not isinstance(order, list):
        return

    matched = _NODE_INDEX_RE.search(step)
    if matched:
        index = int(matched.group(1)) - 1
        if 0 <= index < len(order):
            node_id = str(order[index])
            previous = obs.get("current_node_id")
            if isinstance(previous, str) and previous != node_id:
                _finish_node(obs, previous)

            node = nodes.get(node_id)
            if isinstance(node, dict):
                label = step[: matched.start()].strip()
                is_cached = label.startswith("Cached ")
                is_completed_source = label.startswith(("Text input", "Loading image", "Loading mesh"))
                if node.get("started_at") is None:
                    node["started_at"] = _now()
                    _event(
                        obs,
                        "node.started",
                        node_id=node_id,
                        class_type=node.get("class_type"),
                        index=node.get("index"),
                        total=node.get("total"),
                    )
                obs["current_node_id"] = node_id
                node["phase"] = label or node.get("phase")
                node["progress"] = _node_progress(node, run_progress)
                if is_cached:
                    node["cached"] = True
                    _finish_node(obs, node_id, status="cached")
                    obs["current_node_id"] = None
                elif is_completed_source:
                    _finish_node(obs, node_id)
                    obs["current_node_id"] = None
                else:
                    node["status"] = "running"
                    _event(
                        obs,
                        "node.phase",
                        node_id=node_id,
                        phase=node.get("phase"),
                        progress=node.get("progress"),
                    )
    else:
        current = obs.get("current_node_id")
        if isinstance(current, str):
            node = nodes.get(current)
            if isinstance(node, dict):
                previous_phase = node.get("phase")
                previous_progress = int(node.get("progress") or 0)
                node["progress"] = _node_progress(node, run_progress)
                if step:
                    node["phase"] = step
                # Phase callbacks can be frequent. Record only meaningful
                # changes while always keeping the latest node snapshot.
                if node.get("phase") != previous_phase or abs(int(node.get("progress") or 0) - previous_progress) >= 5:
                    _event(
                        obs,
                        "node.phase",
                        node_id=current,
                        phase=node.get("phase"),
                        progress=node.get("progress"),
                    )

    checkpoint["step"] = step
    checkpoint["progress"] = run_progress


def finalize_workflow_run(
    job: Any,
    *,
    status: str,
    output_url: str | None = None,
    error: str | None = None,
) -> None:
    obs = _obs(job)
    if obs is None:
        return
    current = obs.get("current_node_id")
    if isinstance(current, str):
        if status == "done":
            _finish_node(obs, current)
        elif status == "cancelled":
            _finish_node(obs, current, status="cancelled")
        else:
            _finish_node(obs, current, status="failed", error=error)
        obs["current_node_id"] = None

    if status == "done":
        # A successful engine return means every selected node executed. Mark
        # any source/sink snapshot that did not get a final persist as done.
        for node_id in obs.get("order", []):
            if isinstance(node_id, str):
                _finish_node(obs, node_id)

        if output_url:
            workspace_path = output_url.removeprefix("/workspace/")
            artifact_kind = str((_meta(job).get("artifact_kind") or "artifact"))
            artifact_ref = f"workflow-run://{getattr(job, 'job_id', '')}/output"
            artifact = {
                "kind": artifact_kind,
                "ref": artifact_ref,
                "workspace_path": workspace_path,
                "producer_node_id": (obs.get("order") or [None])[-1],
            }
            artifacts = obs.setdefault("artifacts", [])
            if isinstance(artifacts, list) and not any(
                isinstance(item, Mapping) and item.get("ref") == artifact_ref for item in artifacts
            ):
                artifacts.append(artifact)
            evidence = obs.setdefault("evidence", [])
            if isinstance(evidence, list) and not any(
                isinstance(item, Mapping) and item.get("ref") == artifact_ref for item in evidence
            ):
                evidence.append({"kind": "workflow-output", "ref": artifact_ref})
        _event(obs, "run.completed", output_url=output_url)
    elif status == "cancelled":
        _event(obs, "run.cancelled")
    else:
        _event(obs, "run.failed", error=error)


def inspect_workflow_run(job: Any) -> dict[str, Any]:
    """Return the stable observability projection consumed by Web/CLI/Agent."""

    obs = _obs(job) or {
        "version": OBSERVABILITY_VERSION,
        "workflow_id": (_meta(job).get("workflow_id")),
        "current_node_id": None,
        "nodes": {},
        "order": [],
        "artifacts": [],
        "evidence": [],
        "events": [],
    }
    return {
        "version": int(obs.get("version") or OBSERVABILITY_VERSION),
        "run_id": getattr(job, "job_id", ""),
        "workflow_id": obs.get("workflow_id") or _meta(job).get("workflow_id"),
        "status": getattr(job, "status", "unknown"),
        "progress": int(getattr(job, "progress", 0) or 0),
        "step": getattr(job, "step", None),
        "current_node_id": obs.get("current_node_id"),
        "order": list(obs.get("order") or []),
        "nodes": dict(obs.get("nodes") or {}),
        "artifacts": list(obs.get("artifacts") or []),
        "evidence": list(obs.get("evidence") or []),
        "events": list(obs.get("events") or []),
        "output_url": getattr(job, "output_url", None),
        "error": getattr(job, "error", None),
    }


__all__ = [
    "OBSERVABILITY_KEY",
    "OBSERVABILITY_VERSION",
    "finalize_workflow_run",
    "init_workflow_observability",
    "inspect_workflow_run",
    "mark_workflow_run_started",
    "observe_workflow_checkpoint",
]
