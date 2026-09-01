from typing import Dict

from schemas.execution import ExecutionNode, ExecutionPlan


class WorkflowExecutionNode(ExecutionNode):
    """Compatibility name for legacy workflow execution payloads."""


class WorkflowExecutionRequest(ExecutionPlan):
    """Compatibility wrapper around the canonical :class:`ExecutionPlan`.

    Existing Web, Agent, World, and persisted run payloads can keep using the
    old name while new application code targets the generic execution layer.
    """

    prompt: Dict[str, WorkflowExecutionNode]
