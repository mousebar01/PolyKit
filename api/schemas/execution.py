from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ExecutionSource(BaseModel):
    """Describe where an execution plan came from, independent of who ran it.

    ``kind`` identifies the product object or compiler that produced the plan.
    Web, Agent and CLI are initiators and belong on the Run metadata instead.
    """

    kind: Literal["direct", "workflow", "world"]
    id: Optional[str] = None
    revision: Optional[str] = None


class ExecutionInitiator(BaseModel):
    """Identify who requested a Run without changing execution semantics."""

    type: Literal["user", "agent", "cli", "system"]
    surface: Optional[str] = None
    id: Optional[str] = None


class ExecutionNode(BaseModel):
    """One executable capability invocation in an execution plan."""

    class_type: str
    inputs: Dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    """Canonical immutable input consumed by the execution layer.

    The field names intentionally remain wire-compatible with the existing
    workflow execution payload while PolyKit migrates callers away from
    workflow-specific terminology. ``prompt`` is the executable DAG; a future
    schema version may rename it to ``nodes`` once all clients have migrated.
    """

    schema_version: int = 1
    source: Optional[ExecutionSource] = None
    # Legacy workflow provenance. Kept during the compatibility migration so
    # existing clients and persisted checkpoints continue to validate.
    workflow_id: Optional[str] = None
    prompt: Dict[str, ExecutionNode]
    output_node_id: Optional[str] = None
    target_node_ids: Optional[List[str]] = None
    collection: str = "Workflows"
    metadata: Dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ExecutionInitiator",
    "ExecutionNode",
    "ExecutionPlan",
    "ExecutionSource",
]
