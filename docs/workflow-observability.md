# Workflow Run observability

PolyKit keeps workflow execution observable without introducing a second Agent-specific task runtime.

The design borrows the useful discipline from `img2threejs`: progress must be inspectable from persisted local state, completion must point to evidence, and deterministic code records/enforces facts while a model or user may make higher-level judgments. PolyKit maps that discipline onto its existing `WorkflowRun` lifecycle instead of creating a separate Agent workflow state machine.

## Source of truth

- `WorkflowRun` owns execution telemetry: run status, node states, progress, events, errors, and produced artifacts.
- `WorldDocument.runtime.quality` owns world-domain quality facts such as construction, visual, and gameplay validation.
- Chat/Agent context is neither execution state nor quality evidence.

The observability payload is persisted inside the existing `JobStatus.meta` record, so it follows the same SQLite `RunStore` lifecycle as the run itself.

## Contract

Each canonical DAG run records a versioned `observability` object containing:

```json
{
  "version": 1,
  "workflow_id": "building-construction",
  "current_node_id": "build",
  "order": ["brief", "build", "output"],
  "nodes": {
    "build": {
      "node_id": "build",
      "class_type": "blender-scene/build",
      "status": "running",
      "progress": 63,
      "phase": "blender-export",
      "cached": false,
      "started_at": "...",
      "finished_at": null,
      "error": null
    }
  },
  "artifacts": [],
  "evidence": [],
  "events": []
}
```

Node statuses are execution facts only: `pending`, `running`, `done`, `cached`, `failed`, or `cancelled`.

Events are append-only observations with monotonically increasing `seq` values. Typical event types are `run.queued`, `run.started`, `node.started`, `node.phase`, `node.completed`, `node.failed`, `run.completed`, `run.failed`, and `run.cancelled`.

Successful published output is recorded as both an artifact reference and workflow-output evidence. Domain validators may produce additional evidence separately; observability does not convert that evidence into an Agent transition.

## Process evidence

Process nodes may return a small JSON `metadata` object. The workflow engine
stores it under `JobStatus.meta.process_metadata`, keyed by node id, and the
read-only inspection projection exposes it as `process_metadata`. This is
backend evidence, not another progress or task state machine. For Blender
scene builds it can include the Blender version, BuildSpec, and the
`constructionValidation` report. A world construction validator accepts a
completed `building-construction` run only when that Blender report is present
and has `status: "pass"`; a mock executor without this evidence is not a
Blender E2E result.

## Read-only inspection

`GET /workflow-runs/{run_id}/inspect` returns the stable inspection projection:

- current run status/progress/error
- current node
- ordered node snapshots
- event timeline
- produced artifacts
- evidence references

The MCP tool `polykit_workflow_inspect` is a thin read-only proxy to the same endpoint.

Inspection never advances, retries, resumes, cancels, or otherwise mutates a run.

## Architectural invariant

Observability describes **what happened** and **what is happening**. It must not become another orchestration layer.

```text
Chat / Agent / UI / CLI
        |
        | read
        v
WorkflowRun observability
        ^
        | emitted by
        |
Workflow Engine ----> Node Packs ----> Artifacts
```

Higher-level callers may use inspection plus validator results to decide what to do next, but that decision remains outside the Workflow Run telemetry contract.
