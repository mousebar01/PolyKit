"""Generic execution-layer boundary.

The current implementation is still provided by the proven workflow engine.
New callers should import this module so direct asset generation, World builds,
Agent commands, and saved Workflows converge on one execution abstraction.
"""
from __future__ import annotations

from services.workflow_engine import (
    ArtifactNodeOutputCache,
    WorkflowEngine,
    WorkflowWait,
    clear_workflow_cache,
)


class ExecutionEngine(WorkflowEngine):
    """Canonical execution engine name for new application code."""


ExecutionWait = WorkflowWait
clear_execution_cache = clear_workflow_cache

__all__ = [
    "ArtifactNodeOutputCache",
    "ExecutionEngine",
    "ExecutionWait",
    "clear_execution_cache",
]
