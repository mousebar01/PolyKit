"""Coordinate server-owned workflow and image-generation runs.

This module owns the in-memory run registry, cancellation signals, persistence,
and the serialized accelerator execution slot.  ``Coordinator`` is intentional:
it orchestrates run lifecycle across storage and model runtime boundaries; it is
not itself an execution runtime.
"""
from __future__ import annotations

import threading
import time
from typing import Dict

from schemas.generation import JobStatus
from services.run_store import RunStore
from services.runtime_paths import runtime_paths


class RunCoordinator:
    def __init__(self) -> None:
        self.run_store = RunStore()
        self.jobs: Dict[str, JobStatus] = self.run_store.load()
        self.cancelled: set[str] = set()
        self.cancel_events: Dict[str, threading.Event] = {}
        self.completed_at: Dict[str, float] = self.run_store.completed_times()
        self.generation_lock = threading.Lock()
        self.active_job_id: str | None = None
        self.job_ttl_seconds = 1800

    def reconfigure_store(self) -> None:
        """Rebind run persistence after a runtime workspace/state-path change."""
        if self.active_job_id is not None:
            raise RuntimeError("Cannot change run storage while a generation is active")
        self.run_store = RunStore(runtime_paths.state_db)
        self.jobs = self.run_store.load()
        self.completed_at = self.run_store.completed_times()
        self.cancelled.clear()
        self.cancel_events.clear()

    def purge_old_jobs(self) -> None:
        cutoff = time.time() - self.job_ttl_seconds
        stale = [job_id for job_id, completed in self.completed_at.items() if completed < cutoff]
        for job_id in stale:
            self.jobs.pop(job_id, None)
            self.cancelled.discard(job_id)
            self.cancel_events.pop(job_id, None)
            self.completed_at.pop(job_id, None)
            self.run_store.delete(job_id)
            try:
                from services.mesh_artifacts import cleanup_artifact_root

                cleanup_artifact_root(runtime_paths.workspace / ".artifacts" / job_id)
            except Exception:
                pass
            try:
                from services.workflow_execution import delete_run_checkpoints

                delete_run_checkpoints(job_id)
            except Exception:
                pass

    def register(self, job: JobStatus) -> threading.Event:
        self.jobs[job.job_id] = job
        event = threading.Event()
        self.cancel_events[job.job_id] = event
        self.persist(job)
        return event

    def ensure_cancel_event(self, job_id: str) -> threading.Event:
        event = self.cancel_events.get(job_id)
        if event is None or event.is_set():
            event = threading.Event()
            self.cancel_events[job_id] = event
        self.cancelled.discard(job_id)
        return event

    def persist(self, job: JobStatus) -> None:
        self.run_store.save(job, completed_at=self.completed_at.get(job.job_id))

    def mark_completed(self, job: JobStatus) -> None:
        self.completed_at[job.job_id] = time.time()
        self.persist(job)

    def clear_completed(self, job_id: str) -> None:
        self.completed_at.pop(job_id, None)
        self.run_store.clear_completed(job_id)

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self.cancelled

    def cancel(self, job_id: str) -> JobStatus | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None

        self.cancelled.add(job_id)
        event = self.cancel_events.get(job_id)
        if event is not None:
            event.set()
        if job.status in ("pending", "running", "waiting", "interrupted"):
            job.status = "cancelled"
            self.completed_at[job_id] = time.time()
        self.persist(job)
        self.stop_active_model_runtime(job_id)
        return job

    def set_active(self, job_id: str) -> None:
        self.active_job_id = job_id

    def clear_active(self, job_id: str) -> None:
        if self.active_job_id == job_id:
            self.active_job_id = None

    def stop_active_model_runtime(self, job_id: str) -> None:
        """Best-effort hard stop through the public model-runtime boundary."""
        if self.active_job_id != job_id:
            return
        try:
            from services.model_runtime_registry import model_runtime_registry

            model_runtime_registry.cancel_generation(job_id)
        except Exception:
            # Cooperative cancellation remains authoritative; a hard stop is
            # only a best-effort acceleration for isolated subprocess models.
            pass

run_coordinator = RunCoordinator()
