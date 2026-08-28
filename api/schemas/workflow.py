from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkflowExecutionNode(BaseModel):
    """One executable node in the server-side workflow prompt.

    ``inputs`` intentionally stays open-ended.  Node manifests own the
    meaning and validation of their parameters, while the control plane only
    needs to understand references between nodes.
    """

    class_type: str
    inputs: Dict[str, Any] = Field(default_factory=dict)


class WorkflowExecutionRequest(BaseModel):
    """Compiled execution JSON sent by the Web workflow editor."""

    schema_version: int = 1
    workflow_id: Optional[str] = None
    prompt: Dict[str, WorkflowExecutionNode]
    output_node_id: Optional[str] = None
    target_node_ids: Optional[List[str]] = None
    collection: str = "Workflows"
