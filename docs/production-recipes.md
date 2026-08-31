# Production recipes

PolyKit ProductionRecipe v1 turns validator repair evidence into a reviewable workflow draft without starting execution.

```text
World validator
    ↓
polykit.repair-scope v1
    ↓
ProductionRecipe compiler
    ↓
polykit.production-recipe v1
    ↓
optional WorkflowDefinition + WorkflowExecutionRequest
    ↓
caller decision
    ↓
/workflow-runs/*
```

The compiler is not an Agent runtime and owns no retries, rollback, stage state, or durable task lifecycle. `WorkflowRun` remains the only execution lifecycle.

## Compile API

`POST /workspace-library/worlds/{world_id}/production-recipes/compile`

The request selects one repair scope from a freshly recomputed validator result:

```json
{
  "capability": "world.spatial.validate",
  "repair_scope_id": "repair:world.spatial.validate:spatial.attachment.cabin.wall-floor",
  "run_id": "run-123",
  "collection": "Scenes",
  "render_preview": true,
  "allow_scope_expansion": false
}
```

The server does not trust a caller-authored repair scope. It reruns the named World validator, finds the requested scope in the authoritative result, and compiles from that scope.

## Status

A recipe has one of three v1 statuses:

- `ready`: the installed server backend can honor the compiled scope. The response contains both an editable workflow definition draft and a canonical `WorkflowExecutionRequest`. Nothing is executed automatically.
- `blocked`: the desired repair is more precise than the installed backend, the target building is ambiguous, or a required repair capability is unavailable.
- `no_workflow`: the failure is an evidence problem, so generating geometry would be the wrong response.

## Scope fidelity

The compiler keeps both:

```text
desired_scope
compiled_scope
scope_expanded
```

A workflow must never pretend to be narrower than the backend can actually execute.

For example, the current `blender-scene/build` node rebuilds one full BuildSpec building. If validation localizes a failure to `floor + left-wall + wall-floor`, the default compile result is therefore `blocked` and names the missing capability `blender-scene/repair-parts`.

A caller may explicitly set `allow_scope_expansion: true`. In that case the compiler may produce the existing whole-building workflow, but the expansion is preserved in the recipe and WorkflowRun metadata:

```json
{
  "desired_repair_scope": { "locality": "relationship" },
  "compiled_repair_scope": { "locality": "building", "building_id": "cabin" },
  "scope_expanded": true
}
```

This is an explicit fallback, not silent local repair.

## Evidence-only failures

Missing reports, missing final meshes, incomplete runs, and similar evidence failures return `no_workflow` when represented as an evidence repair scope. The recipe carries the advisory `next_action`, such as regenerating or attaching evidence, but does not fabricate a geometry workflow.

## Missing repair capabilities

V1 reports concrete capability gaps instead of creating fake workflows. Examples include:

```text
blender-scene/repair-parts
blender-scene/repair-camera
blender-scene/repair-visibility
blender-scene/repair-visual-subject
blender-scene/repair-semantic-subject
world.scene.repair
world.gameplay.repair
world.spec.repair
```

These names are compiler requirements, not claims that such node packs already exist.

## Workflow outputs

A `ready` recipe returns two representations of the same planned execution:

1. `workflow_definition` is an editable graph draft using the normal Web workflow node/edge format.
2. `execution_request` is the canonical server execution payload accepted by `/workflow-runs/execute`.

The caller may inspect, persist, edit, or execute them through the existing workflow APIs. The compiler itself never persists the draft and never starts a run.
