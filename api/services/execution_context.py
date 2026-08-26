"""Request-scoped execution context for workflow runs."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from services.runtime_paths import runtime_paths
from services.workspace_paths import normalize_collection


@dataclass(frozen=True)
class ExecutionPaths:
    workspace: Path
    collection_dir: Path
    artifact_root: Path
    model_outputs: Path
    process_workspace: Path
    temp: Path


@dataclass(frozen=True)
class ExecutionContext:
    """Everything filesystem/cancellation-specific to one workflow run."""

    run_id: str
    collection: str
    paths: ExecutionPaths
    cancel_event: Optional[threading.Event]
    is_cancelled: Callable[[], bool]

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        collection: str,
        cancel_event: Optional[threading.Event],
        is_cancelled: Callable[[], bool],
    ) -> "ExecutionContext":
        normalized = normalize_collection(collection or "Workflows")
        workspace = runtime_paths.workspace
        artifact_root = workspace / ".artifacts" / run_id
        paths = ExecutionPaths(
            workspace=workspace,
            collection_dir=workspace / normalized,
            artifact_root=artifact_root,
            model_outputs=artifact_root / "models",
            process_workspace=artifact_root / "process-workspace",
            temp=artifact_root / "tmp",
        )
        return cls(
            run_id=run_id,
            collection=normalized,
            paths=paths,
            cancel_event=cancel_event,
            is_cancelled=is_cancelled,
        )

    def prepare(self) -> None:
        self.paths.model_outputs.mkdir(parents=True, exist_ok=True)
        self.paths.process_workspace.mkdir(parents=True, exist_ok=True)
        self.paths.temp.mkdir(parents=True, exist_ok=True)

    def cancelled(self) -> bool:
        return self.is_cancelled()
