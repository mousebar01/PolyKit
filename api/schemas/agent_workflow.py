"""Protocol models for durable Agent workflows.

Agent workflows are task state, not chat state. Definitions describe allowed
steps/transitions; sessions persist one execution so the host Agent can answer
unrelated questions and later resume without reconstructing progress from the
conversation transcript.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AGENT_WORKFLOW_KIND = "polykit.agent-workflow"
AGENT_WORKFLOW_SESSION_KIND = "polykit.agent-workflow-session"
AGENT_WORKFLOW_VERSION = 1
SPECIAL_TRANSITION_TARGETS = {"$complete", "$stop"}

AgentWorkflowStepType = Literal["agent", "workflow", "validator"]
AgentWorkflowSessionStatus = Literal[
    "running",
    "paused",
    "waiting_for_user",
    "waiting_for_run",
    "completed",
    "failed",
    "cancelled",
]
AgentWorkflowStepStatus = Literal["pending", "ready", "running", "completed", "failed", "skipped"]
AgentWorkflowWaitKind = Literal["user", "run"]


class AgentWorkflowCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: list[str] = Field(default_factory=list)


class AgentWorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=120)
    type: AgentWorkflowStepType
    capability: str | None = Field(default=None, max_length=240)
    workflow: str | None = Field(default=None, max_length=240)
    inputs: dict[str, Any] = Field(default_factory=dict)
    completion: AgentWorkflowCompletion = Field(default_factory=AgentWorkflowCompletion)
    transitions: dict[str, str]

    @model_validator(mode="after")
    def validate_executor(self):
        if self.type == "workflow" and not self.workflow:
            raise ValueError(f"Workflow step '{self.id}' requires workflow")
        if self.type in {"agent", "validator"} and not self.capability:
            raise ValueError(f"{self.type.title()} step '{self.id}' requires capability")
        if not self.transitions:
            raise ValueError(f"Step '{self.id}' requires at least one transition")
        return self


class AgentWorkflowLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts_per_step: int = Field(default=3, ge=1, le=100)
    max_corrections: int = Field(default=8, ge=0, le=1000)
    max_transitions: int = Field(default=128, ge=1, le=10000)


class AgentWorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["polykit.agent-workflow"] = AGENT_WORKFLOW_KIND
    version: Literal[1] = AGENT_WORKFLOW_VERSION
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    subject_kind: str | None = Field(default=None, max_length=120)
    start_step: str = Field(min_length=1, max_length=120)
    limits: AgentWorkflowLimits = Field(default_factory=AgentWorkflowLimits)
    steps: list[AgentWorkflowStep] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_graph(self):
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Agent workflow step ids must be unique")
        known = set(ids)
        if self.start_step not in known:
            raise ValueError(f"start_step '{self.start_step}' does not exist")
        for step in self.steps:
            for outcome, target in step.transitions.items():
                if not outcome.strip():
                    raise ValueError(f"Step '{step.id}' contains an empty transition outcome")
                if target not in known and target not in SPECIAL_TRANSITION_TARGETS:
                    raise ValueError(f"Step '{step.id}' transition '{outcome}' targets unknown step '{target}'")
        return self


class AgentWorkflowSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=120)
    id: str = Field(min_length=1, max_length=240)


class AgentWorkflowEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=120)
    ref: str = Field(min_length=1, max_length=2000)
    summary: str | None = Field(default=None, max_length=4000)


class AgentWorkflowStepState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentWorkflowStepStatus = "pending"
    attempts: int = Field(default=0, ge=0)
    evidence: list[AgentWorkflowEvidence] = Field(default_factory=list)
    last_outcome: str | None = None
    diagnostic: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class AgentWorkflowWait(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AgentWorkflowWaitKind
    ref: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=4000)


class AgentWorkflowTransitionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_step: str
    outcome: str
    to_step: str
    at: str


class AgentWorkflowSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["polykit.agent-workflow-session"] = AGENT_WORKFLOW_SESSION_KIND
    version: Literal[1] = AGENT_WORKFLOW_VERSION
    id: str
    workflow_id: str
    workflow_version: int = AGENT_WORKFLOW_VERSION
    subject: AgentWorkflowSubject
    status: AgentWorkflowSessionStatus = "running"
    current_step: str | None
    steps: dict[str, AgentWorkflowStepState]
    wait: AgentWorkflowWait | None = None
    corrections: int = Field(default=0, ge=0)
    transition_count: int = Field(default=0, ge=0)
    history: list[AgentWorkflowTransitionRecord] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
