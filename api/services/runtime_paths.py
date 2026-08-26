"""Single source of truth for PolyKit runtime filesystem locations.

Historically several services owned mutable copies of MODELS_DIR,
WORKSPACE_DIR, NODE_PACKS_DIR and WORKFLOWS_DIR. That made runtime path changes
order-dependent and forced unrelated services to import ModelRuntimeRegistry just
to locate files. RuntimePaths centralises those roots and keeps environment
variables in sync for child processes and legacy adapters.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_HOME = Path.home() / ".polykit"


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class RuntimePathSnapshot:
    models: Path
    workspace: Path
    workflows: Path
    node_packs: Path
    state_db: Path
    data: Path


class RuntimePaths:
    """Process-wide runtime roots with atomic updates and stable ownership."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._explicit_state_db = bool(os.environ.get("POLYKIT_STATE_DB"))
        models = _env_path("MODELS_DIR", _HOME / "models")
        workspace = _env_path("WORKSPACE_DIR", _HOME / "workspace")
        workflows = _env_path("WORKFLOWS_DIR", _HOME / "workflows")
        node_packs = _env_path("NODE_PACKS_DIR", _HOME / "node-packs")
        data = _env_path("POLYKIT_DATA_DIR", _HOME)
        state_db = _env_path("POLYKIT_STATE_DB", workspace / ".polykit-runs.sqlite3")
        self._snapshot = RuntimePathSnapshot(
            models=models,
            workspace=workspace,
            workflows=workflows,
            node_packs=node_packs,
            state_db=state_db,
            data=data,
        )
        self._sync_environment(self._snapshot)
        self.ensure_directories()

    def snapshot(self) -> RuntimePathSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def models(self) -> Path:
        return self.snapshot().models

    @property
    def workspace(self) -> Path:
        return self.snapshot().workspace

    @property
    def workflows(self) -> Path:
        return self.snapshot().workflows

    @property
    def node_packs(self) -> Path:
        return self.snapshot().node_packs

    @property
    def state_db(self) -> Path:
        return self.snapshot().state_db

    @property
    def data(self) -> Path:
        return self.snapshot().data

    def _sync_environment(self, snap: RuntimePathSnapshot) -> None:
        os.environ["MODELS_DIR"] = str(snap.models)
        os.environ["WORKSPACE_DIR"] = str(snap.workspace)
        os.environ["WORKFLOWS_DIR"] = str(snap.workflows)
        os.environ["NODE_PACKS_DIR"] = str(snap.node_packs)
        os.environ["POLYKIT_DATA_DIR"] = str(snap.data)
        if self._explicit_state_db:
            os.environ["POLYKIT_STATE_DB"] = str(snap.state_db)
        else:
            os.environ.pop("POLYKIT_STATE_DB", None)

    def ensure_directories(self) -> None:
        snap = self.snapshot()
        for path in (snap.models, snap.workspace, snap.workflows, snap.node_packs, snap.data):
            path.mkdir(parents=True, exist_ok=True)
        snap.state_db.parent.mkdir(parents=True, exist_ok=True)

    def update(
        self,
        *,
        models_dir: Optional[Path] = None,
        workspace_dir: Optional[Path] = None,
        workflows_dir: Optional[Path] = None,
        node_packs_dir: Optional[Path] = None,
        state_db: Optional[Path] = None,
    ) -> RuntimePathSnapshot:
        """Replace selected roots and return the new snapshot.

        When the run database is not explicitly configured, it follows the
        workspace root so changing workspace cannot leave run state attached to
        the previous directory.
        """
        with self._lock:
            current = self._snapshot
            models = models_dir.expanduser().resolve() if models_dir is not None else current.models
            workspace = workspace_dir.expanduser().resolve() if workspace_dir is not None else current.workspace
            workflows = workflows_dir.expanduser().resolve() if workflows_dir is not None else current.workflows
            node_packs = node_packs_dir.expanduser().resolve() if node_packs_dir is not None else current.node_packs

            if state_db is not None:
                resolved_state = state_db.expanduser().resolve()
                self._explicit_state_db = True
            elif workspace_dir is not None and not self._explicit_state_db:
                resolved_state = workspace / ".polykit-runs.sqlite3"
            else:
                resolved_state = current.state_db

            self._snapshot = RuntimePathSnapshot(
                models=models,
                workspace=workspace,
                workflows=workflows,
                node_packs=node_packs,
                state_db=resolved_state,
                data=current.data,
            )
            self._sync_environment(self._snapshot)

        self.ensure_directories()
        return self.snapshot()


runtime_paths = RuntimePaths()
