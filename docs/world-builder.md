# World Builder

PolyKit World Builder is a server-owned domain layer for scene intent, construction facts, gameplay contracts, workflow execution, and evidence-backed validation.

```text
Web / CLI / automation
        ↓
World API
        ↓
World domain compiler + validators
        ↓
Workflow Engine / Workflow Runs
        ↓
Node Packs
        ↓
Blender / local models / processors
        ↓
Artifacts / GLB
```

## Domain boundary

A `WorldDocument` describes what the world is. A `WorkflowRun` describes what computation is or was running. Do not store workflow stages, retry counters, task progress, rollback targets, or Agent conversation state in the World document.

The server remains the single execution control plane. Browser, CLI, MCP, and external automation all use the same World and workflow APIs.

## Runtime model

Schema-v2 World documents keep stable domain facts under `runtime`:

- `intent`: the authored goal and prompt.
- `build`: deterministic BuildSpec facts such as buildings, anchors, attachments, tolerances, and construction rules.
- `scene`: compiled ScenePlan objects, relations, instances, and layout diagnostics.
- `game`: player, interaction, objective, and gameplay facts.
- `quality`: compact construction, visual, and gameplay quality summaries.

Large validation reports, renders, meshes, and workflow timelines remain artifacts/evidence rather than being copied into the World document.

## Scene planning

A typical semantic scene flow is:

```text
create/save World
      ↓
compile ScenePlan
      ↓
resolve or generate object assets
      ↓
attach artifacts to stable object ids
      ↓
compose final scene
```

ScenePlan is renderer-neutral. Object identity is stable and must not depend on filenames.

## Building construction

`runtime.build` stores the authored construction facts. `POST /workspace-library/worlds/{world_id}/build-structure` compiles one supported building into the canonical Workflow Engine.

The current Blender bridge uses the official `blender-scene/build` process node and publishes a GLB plus optional production/gray inspection renders. The node also records a render-evidence sidecar with camera, light-role, color-management, and non-blank metrics; this is render evidence for a later `VisualValidationReport`, not a replacement for that domain report. It does not own WorkflowRun state. FastAPI owns the WorkflowRun, output naming, artifact paths, cancellation, and persistence.

Construction validators inspect facts and evidence; they never advance a task.

## Visual and spatial validation

World validation capabilities include:

```text
world.spec.validate
world.blockout.validate
world.construction.validate
world.spatial.validate
world.visual.validate
world.gameplay.validate
world.final.validate
```

`world.visual.validate` consumes an evidence-backed VisualValidationReport and, when required, reruns authoritative spatial checks against the current World plus the final WorkflowRun GLB.

The Spatial Judge can prove bounded facts such as BuildSpec contact tolerances, camera-frustum membership, sampled line of sight, and suitable watertight/convex `inside` or `passes-through` relations. Missing or insufficient evidence remains `needs_review` / `not_evaluated`; it is never promoted to PASS by assumption.

See `docs/visual-validation.md` and `docs/spatial-validation.md`.

Reusable Blender construction and finishing operations (openings, stairs,
curves, assemblies, surfacing, lighting, deformation, simulation setup, NPR,
and factual geometry reports) are available as official process nodes. See
`docs/blender-production.md`.

## Repair scopes and production recipes

Validators also derive advisory `polykit.repair-scope` v1 records. These identify the smallest trustworthy causal area—such as an attachment relationship, P0 object, camera composition issue, or missing evidence—without performing a repair.

`POST /workspace-library/worlds/{world_id}/production-recipes/compile` recompiles one authoritative repair scope into `polykit.production-recipe` v1. A recipe may return an editable WorkflowDefinition draft and canonical WorkflowExecutionRequest, but it never starts a run automatically.

The compiler preserves both desired and executable scope. If a validator localizes a defect to one relationship but the installed backend can only rebuild an entire building, the default result is blocked. Scope expansion is allowed only through an explicit caller opt-in and is recorded in workflow metadata.

See `docs/repair-scopes.md` and `docs/production-recipes.md`.

## Final validation

Final validation aggregates domain validators rather than inventing new facts.

Construction, visual/spatial, and gameplay domains must have the required passing evidence before final output can pass. Missing visual evidence remains `needs_review`; explicit failed evidence is `fail`.

## Important rules

- Do not introduce a second durable World task state machine.
- Do not put workflow stage state in `WorldDocument`.
- Do not duplicate World mutation rules in CLI, MCP, or browser clients.
- Do not execute model/process nodes in the browser.
- Construction contacts and tolerances must be deterministic.
- `inside` and `passes-through` require volume evidence before PASS.
- Missing visual or semantic evidence must not be invented as PASS.
- Validation and repair-scope outputs are facts/advice; callers decide whether to compile or execute another WorkflowRun.
