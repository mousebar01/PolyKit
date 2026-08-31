# Visual validation

PolyKit visual validation is an evidence-producing domain validator. It does not own task progression, retries, rollback, or Agent state. `WorkflowRun` remains the only execution lifecycle, while `WorldDocument.runtime.quality.visual` remains a compact domain-quality summary.

## Boundary

```text
Reference / visual target
        ↓
   WorkflowRun
        ↓
 render evidence + final GLB
        ↓
VisualValidationReport
        ↓
world.visual.validate
        ↓
authoritative spatial re-check
        ↓
world.final.validate
```

The full report is an immutable evidence artifact. A later attempt creates another report rather than overwriting the previous one.

## Three judges

A visual report combines independent checks from three judge classes:

- `metric`: deterministic image comparison such as aspect ratio, grayscale MAE, edge MAE, grid luminance, and P0 regional metrics.
- `semantic`: material identity, visual hierarchy, lighting direction, reference identity, wet/dry state, and other visual facts that pixel metrics cannot prove.
- `spatial`: World/scene/geometry facts such as connectivity, support, clearance, camera revision, and directional structures.

A semantic or spatial failure can veto otherwise strong image metrics. Aggregate similarity never compensates for a critical P0, material, camera, or geometry failure.

## Status

World-facing status remains:

```text
pass
needs_review
fail
```

Individual checks may additionally use `not_evaluated` and `not_applicable`.

Required checks are fail-closed:

- any required `fail` -> report `fail`;
- no failure but any required `needs_review` or `not_evaluated` -> report `needs_review`;
- only required `pass` / `not_applicable` -> report `pass`.

Missing evidence never becomes `pass`. If no visual report exists yet, `world.visual.validate` returns `needs_review`; an explicit report that is malformed, belongs to another world/run, references invalid evidence, or contains a required failed check returns `fail`.

## VisualValidationReport v1

```json
{
  "schema_version": 1,
  "kind": "polykit.visual-validation-report",
  "world_id": "world_xxx",
  "run_id": "run_xxx",
  "validator": "world.visual.validate",
  "status": "needs_review",
  "target": {
    "kind": "reference-image",
    "reference_id": "ref_main",
    "camera_id": "camera_main",
    "camera_revision": 3,
    "require_spatial": true
  },
  "candidate": {
    "camera_id": "camera_main",
    "camera_revision": 3
  },
  "summary": {
    "metric_status": "pass",
    "semantic_status": "needs_review",
    "spatial_status": "pass"
  },
  "checks": [],
  "earliest_failure": null,
  "evidence": [],
  "provenance": {
    "validator_version": "visual-v1"
  }
}
```

Each required passing check must cite evidence by stable evidence id. The evidence entry must point to a real file when file-backed evidence is used.

## Evidence integrity

Server validation treats evidence as part of the proof, not as decoration:

- reference-locked reports require deterministic metric checks and a required semantic review;
- reference-locked reports require the P0 completeness check rather than allowing a caller to omit P0 validation;
- when the target records a camera id or camera revision, the candidate must match it;
- targets that declare `require_spatial` must contain required spatial/geometry checks;
- file-backed evidence must exist and resolve inside the server-owned PolyKit workspace;
- check ids and evidence ids must be unique, and every required evaluated check must reference existing evidence;
- the validator recomputes report status, summary, and earliest unresolved check instead of trusting caller-authored summary fields.

These rules prevent a numerically good image, a stale render, or an arbitrary local file from being used to manufacture a passing report.

## Authoritative spatial judge

`api/services/spatial_validation.py` independently re-checks geometry when a target sets `require_spatial: true`. This second pass does **not** trust the spatial status written into the VisualValidationReport.

The judge reads the final mesh artifact recorded by WorkflowRun observability and loads the delivered GLB with `trimesh`. It produces a `polykit.spatial-snapshot` containing final geometry nodes and world-space bounds, then crosses that geometry with server-owned World facts.

Only explicit mesh/scene artifacts or known mesh file extensions are accepted as spatial mesh evidence. A PNG or other generic WorkflowRun artifact cannot satisfy the geometry gate.

Current deterministic checks include:

- final WorkflowRun GLB exists, is readable, and contains geometry;
- compiled `ScenePlan.metadata.layoutQuality` remains passing;
- P0 observations that name a `world_object_id` have a compiled ScenePlan object and instance;
- BuildSpec `support` / `flush` attachment anchors can be mapped to final GLB nodes;
- mapped attachment anchors are measured against the **actual final mesh surface** using trimesh proximity, not only the authored anchor coordinates;
- attachment distance must remain within its declared BuildSpec tolerance.

`inside` and `passes-through` stay `not_evaluated` until richer volume evidence is available. They are never silently converted to PASS.

This creates two different trust levels:

```text
report.spatial_status
    = caller/producer evidence

world.spatial.validate
    = server recomputation from World + final GLB
```

When `require_spatial` is enabled, `world.visual.validate` requires both layers to agree. A caller-authored spatial PASS cannot override a failing final-GLB measurement.

## P0 observations

Reference-guided tasks should mark the visual facts that define target identity as P0. P0 is an importance level, not a request to create geometry. A P0 observation may represent geometry, a doorway or other negative space, material, lighting, atmosphere, or another visual region.

Example:

```json
{
  "id": "main-doorway",
  "priority": "P0",
  "production_domain": "spatial_region",
  "world_object_id": "doorway_main",
  "bbox_normalized": [0.34, 0.18, 0.28, 0.69],
  "required_cues": [
    "tall central opening",
    "dark connected region beyond",
    "visible floor continuation"
  ],
  "confidence": 0.98
}
```

The deterministic comparator supports supplied `candidate_bbox_normalized` values. When candidate bounds are unavailable, P0 center/size validation remains `not_evaluated` rather than being guessed.

## Comparison order

Visual checks should be interpreted from large structure to small detail:

```text
frame / crop
    ↓
camera / projection
    ↓
negative space / occlusion
    ↓
spatial connectivity
    ↓
P0 silhouette / bounds
    ↓
contacts / construction
    ↓
grayscale hierarchy
    ↓
material identity
    ↓
lighting direction
    ↓
color / wet-dry
    ↓
surface aging / atmosphere / presentation
```

The report records the earliest unresolved required check so callers can repair the smallest causal scope. The validator itself does not execute repairs or mutate WorkflowRun state.

## Deterministic image comparison

`api/services/visual_validation.py` provides the initial deterministic comparator. It emits overlay, amplified difference, and edge-comparison artifacts and measures:

- aspect-ratio error;
- global grayscale MAE;
- global edge MAE;
- 4 x 3 luminance-grid maximum error;
- P0 regional grayscale and edge error;
- P0 center and size error when candidate bounds are supplied.

A reference-locked report without P0 observations cannot obtain a clean metric pass. A report without semantic review cannot obtain a final visual pass.

## World integration

The validation capability family is:

```text
world.spec.validate
world.blockout.validate
world.construction.validate
world.spatial.validate
world.visual.validate
world.gameplay.validate
world.final.validate
```

`world.spatial.validate` is the standalone deterministic World + final-GLB geometry gate.

`world.visual.validate` accepts either:

- a report reference stored at `runtime.quality.visual.report_ref`; or
- `workflow_metadata.visual_validation_report` from a completed WorkflowRun for the same world.

A report reference may be an embedded report during tests/automation or a workspace-relative JSON artifact reference in normal production use. If that report declares `require_spatial`, the same WorkflowRun must expose a final mesh artifact in observability so the server can reproduce the spatial result.

`world.final.validate` requires visual validation to pass alongside the other required world quality domains. An unreviewed visual or spatial result keeps Final at `needs_review`; an explicit visual/spatial failure makes Final fail. Validators report facts and evidence only; they do not create a second task state machine.
