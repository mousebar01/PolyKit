"""Small SQLite-backed run store for the single-process headless service."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from services.runtime_paths import runtime_paths

try:
    from schemas.generation import JobStatus
except ImportError:  # Keep the store unit-testable without API dependencies.
    @dataclass
    class JobStatus:  # type: ignore[no-redef]
        job_id: str
        status: str
        progress: int = 0
        step: Optional[str] = None
        output_url: Optional[str] = None
        error: Optional[str] = None
        meta: Optional[Dict[str, Any]] = None


def _default_db_path() -> Path:
    """Compatibility helper; RuntimePaths owns the actual state-db location."""
    return runtime_paths.state_db


class RunStore:
    """Persist queryable run state without requiring a second service."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = (db_path or _default_db_path()).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    step TEXT,
                    output_url TEXT,
                    error TEXT,
                    meta_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL
                )
                """
            )
            now = time.time()
            conn.execute(
                """
                UPDATE runs
                   SET status = 'interrupted',
                       error = COALESCE(error, 'Service restarted before the run completed'),
                       updated_at = ?,
                       completed_at = NULL
                 WHERE status IN ('pending', 'running')
                """,
                (now,),
            )

    def load(self) -> Dict[str, JobStatus]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at").fetchall()
        return {row["run_id"]: self._to_job(row) for row in rows}

    def completed_times(self) -> Dict[str, float]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id, completed_at FROM runs WHERE completed_at IS NOT NULL"
            ).fetchall()
        return {row["run_id"]: float(row["completed_at"]) for row in rows}

    def save(self, job: JobStatus, *, completed_at: Optional[float] = None) -> None:
        now = time.time()
        meta_json = json.dumps(job.meta, separators=(",", ":")) if job.meta is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, status, progress, step, output_url, error,
                    meta_json, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    progress = excluded.progress,
                    step = excluded.step,
                    output_url = excluded.output_url,
                    error = excluded.error,
                    meta_json = excluded.meta_json,
                    updated_at = excluded.updated_at,
                    completed_at = COALESCE(excluded.completed_at, runs.completed_at)
                """,
                (
                    job.job_id,
                    job.status,
                    job.progress,
                    job.step,
                    job.output_url,
                    job.error,
                    meta_json,
                    now,
                    now,
                    completed_at,
                ),
            )

    def clear_completed(self, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE runs SET completed_at = NULL WHERE run_id = ?", (run_id,))

    def delete(self, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    @staticmethod
    def _to_job(row: sqlite3.Row) -> JobStatus:
        meta: Optional[Dict[str, Any]] = None
        if row["meta_json"]:
            try:
                parsed = json.loads(row["meta_json"])
                if isinstance(parsed, dict):
                    meta = parsed
            except json.JSONDecodeError:
                meta = {"store_warning": "Invalid persisted metadata"}
        return JobStatus(
            job_id=row["run_id"],
            status=row["status"],
            progress=row["progress"],
            step=row["step"],
            output_url=row["output_url"],
            error=row["error"],
            meta=meta,
        )
