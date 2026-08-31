# Agent Workflow Protocol v1

PolyKit Agent Workflow is a **durable task protocol**, not a conversational mode.
The host Agent remains a normal chat/coding/research Agent while zero or more
workflow sessions are attached to the conversation or workspace.

## Core invariant

A normal chat turn MUST NOT mutate workflow progress.

A user can start a long-running task, ask an unrelated question, receive a normal
answer, and later resume the task from its durable workflow session. Conversation
history is useful context, but it is not the source of truth for task progress.

```text
ChatSession
  ├─ ordinary messages / research / coding
  └─ references zero or more AgentWorkflowSessions

AgentWorkflowSession
  ├─ workflow definition id/version
  ├─ subject
  ├─ current step
  ├─ attempts / corrections / history
  ├─ evidence refs
  └─ optional wait state
```

## Definition vs session

`AgentWorkflowDefinition` describes the allowed process. It is immutable runtime
policy: steps, executors, required evidence, transitions and limits.

`AgentWorkflowSession` is one durable execution of a definition. It stores only
execution state and evidence references; domain data remains in domain documents
such as `WorldDocument`.

Definitions use three executor types in v1:

- `agent`: semantic work that requires model judgment.
- `workflow`: an existing PolyKit typed DAG / workflow run.
- `validator`: deterministic or model-assisted review that emits evidence.

## Lifecycle

```text
start / resume
     │
     ▼
    next        (read-only)
     │
     ▼
   begin        (explicit mutation)
     │
     ├── wait_for_user / wait_for_run
     │          │
     │          └── resume
     │
     ▼
  complete
     │
     ├── forward transition
     ├── correction / retry transition
     ├── $complete
     └── $stop
```

`next` MUST be side-effect free. Merely asking what comes next cannot advance the
workflow. Only explicit `begin`, `complete`, `wait`, `pause`, `resume`, or `cancel`
operations mutate the session.

## Evidence

A step can declare required evidence kinds. Completion is rejected until every
required kind is present. The session stores compact evidence references rather
than copying large reports or artifacts into workflow state.

Examples:

```text
world-intent
build-spec
scene-plan
workflow-run
construction-report
gameplay-report
```

Deterministic checks should be produced by code whenever possible. The Agent may
judge style, intent alignment and other semantic questions, but it must not invent
mechanical validation results.

## Waiting and ordinary chat

A workflow can enter `waiting_for_user` or `waiting_for_run` while keeping its
current step. This does not block the host chat session.

Example:

```text
world-builder / structure
  → begin
  → launch Blender workflow
  → waiting_for_run(run-123)

user: "顺便帮我查一下北欧木屋屋顶的资料"
agent: normal research answer

user: "继续刚才那个"
agent:
  → resume workflow session
  → check run-123
  → complete structure with evidence
  → next
```

The research turn does not call a workflow mutation API, therefore workflow state
remains unchanged.

## Limits and corrections

Definitions declare bounded attempts, corrections and transitions. Backward
transitions count as corrections. Limits prevent an Agent from silently looping
forever and consuming unbounded model/GPU work.

## Built-in World Builder

`api/resources/agent_workflows/world-builder.json` is the first built-in workflow.
It is intentionally separate from `WorldDocument`: the world is the product data;
the workflow session is one process used to create or refine it.

The built-in process currently follows:

```text
intent
→ spec
→ validate-spec
→ blockout
→ blockout-review
→ structure
→ construction-review
→ environment
→ assets
→ materials
→ lighting
→ gameplay
→ gameplay-review
→ optimization
→ final-review
```

Review steps can route backward to retry execution or revise the spec. This is a
workflow policy, not a property of the world document itself.

## HTTP surface

The FastAPI control plane exposes:

```text
GET  /agent-workflows/definitions
GET  /agent-workflows/definitions/{workflow_id}
POST /agent-workflows/sessions
GET  /agent-workflows/sessions/{session_id}
GET  /agent-workflows/sessions/{session_id}/next
POST /agent-workflows/sessions/{session_id}/begin
POST /agent-workflows/sessions/{session_id}/complete
POST /agent-workflows/sessions/{session_id}/wait
POST /agent-workflows/sessions/{session_id}/pause
POST /agent-workflows/sessions/{session_id}/resume
POST /agent-workflows/sessions/{session_id}/cancel
```

Agent/Skill integration should be layered on top of this protocol rather than
reimplementing workflow state inside prompts or chat history.
