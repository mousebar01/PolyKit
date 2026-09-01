"""Application boundary for validating plans and creating durable Runs.

Routers, Agent adapters, and domain compilers should converge here instead of
reimplementing run metadata, graph validation, or durable checkpoint setup.
Scheduling is intentionally left to the transport/runtime adapter so this
module has no FastAPI dependency.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from schemas.execution import ExecutionInitiator, ExecutionPlan
from schemas.generation import JobStatus
from schemas.workflow import WorkflowExecutionRequest
from services.model_runtime_registry import model_runtime_registry
from services.node_catalog import is_known
from services.run_coordinator import run_coordinator
from services.run_observability import init_workflow_observability
from services.runtime_paths import runtime_paths
from services.workflow_execution import initialize_workflow_execution
from services.workflow_executor import (
    SINK_NODES,
    select_execution_prompt,
    topological_order,
    validate_prompt_links,
)
from services.workspace_paths import normalize_collection


@dataclass(frozen=True)
class PreparedExecution:
    """A validated Run that is ready for a runtime adapter to schedule."""

    run_id: str
    request: WorkflowExecutionRequest
    queued_nodes: int
    collection: str


def _legacy_request(plan: ExecutionPlan) -> WorkflowExecutionRequest:
    """Bridge the new execution protocol to the current durable engine format."""

    return WorkflowExecutionRequest.model_validate(plan.model_dump(mode="python"))


def validate_execution_plan(plan: ExecutionPlan) -> tuple[WorkflowExecutionRequest, dict, list[str], str]:
    """Validate one canonical plan before any Run is registered."""

    if plan.schema_version != 1:
        raise ValueError(f"Unsupported execution schema: {plan.schema_version}")

    request = _legacy_request(plan)
    collection = normalize_collection(plan.collection or "Workflows")
    execution_prompt = select_execution_prompt(request)
    order = topological_order(execution_prompt)

    for node in execution_prompt.values():
        if not is_known(node.class_type):
            raise ValueError(f"Unknown executable capability '{node.class_type}'")

    if request.output_node_id is not None:
        output_node = request.prompt.get(request.output_node_id)
        if output_node is None or output_node.class_type not in SINK_NODES:
            raise ValueError("output_node_id must point to an output or preview sink")

    if not any(execution_prompt[node_id].class_type in SINK_NODES for node_id in order):
        raise ValueError("Execution plan must include an output or preview sink")

    validate_prompt_links(request, execution_prompt)
    return request, execution_prompt, order, collection


def prepare_execution_run(
    plan: ExecutionPlan,
    *,
    initiator: ExecutionInitiator,
) -> PreparedExecution:
    """Create and persist the durable Run shell for an already compiled plan.

    This owns the shared behavior required by manual UI, Agent, CLI, Workflow,
    and World entry points. The caller only needs to schedule the returned
    request with the runtime runner.
    """

    request, execution_prompt, order, collection = validate_execution_plan(plan)

    run_coordinator.purge_old_jobs()
    run_id = str(uuid.uuid4())
    source = plan.source.model_dump(mode="json") if plan.source is not None else None
    initiator_payload = initiator.model_dump(mode="json", exclude_none=True)

    meta: dict = {
        "collection": collection,
        "execution_source": source,
        "initiator": initiator_payload,
    }
    if request.workflow_id:
        # Kept while old clients and World validators still read this field.
        meta["workflow_id"] = request.workflow_id
    if request.metadata:
        # execution_metadata is the canonical name. workflow_metadata remains a
        # temporary compatibility mirror for validators and existing clients.
        metadata = dict(request.metadata)
        meta["execution_metadata"] = metadata
        meta["workflow_metadata"] = metadata

    job = JobStatus(job_id=run_id, status="pending", progress=0, meta=meta)
    initialize_workflow_execution(job, request, order, workspace_root=runtime_paths.workspace)
    init_workflow_observability(job, request, execution_prompt, order)
    run_coordinator.register(job)
    model_runtime_registry.begin_generation(run_id)

    return PreparedExecution(
        run_id=run_id,
        request=request,
        queued_nodes=len(execution_prompt),
        collection=collection,
    )


__all__ = [
    "PreparedExecution",
    "prepare_execution_run",
    "validate_execution_plan",
]
