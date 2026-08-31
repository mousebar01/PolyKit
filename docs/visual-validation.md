# Visual validation

PolyKit visual validation is an evidence-producing domain validator. It does not own task progression, retries, rollback, or Agent state. `WorkflowRun` remains the only execution lifecycle, while `WorldDocument.runtime.quality.visual` remains a compact domain-quality summary.

## Boundary

```text
Reference / visual target
        ↓
   WorkflowRun
        ↓
 render evidence
        ↓
VisualValidationReport
        ↓
world.visual.validate
        ↓
world.final.validate
```

The full report is an immutable evidence artifact. A later attempt creates another report rather than overwriting the previous one.

## Three judges

A visual report combines independent checks from three judge classes:

- `metric`: deterministic image comparison such as aspect ratio, grayscale MAE, edge MAE, grid luminance, and P0 regional metrics.
- `semantic`: material identity, visual hierarchy, lighting direction, reference identity, wet/dry state, and other visual facts that pixel metrics cannot prove.
- `spatial`: World/scene/geometry facts such as connectivity, visibility, support, clearance, camera revision, and directional structures.

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

Missing evidence never becomes `pass`.

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
    "camera_revision": 3
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

## P0 observations

Reference-guided tasks should mark the visual facts that define target identity as P0. P0 is an importance level, not a request to create geometry. A P0 observation may represent geometry, a doorway or other negative space, material, lighting, atmosphere, or another visual region.

Example:

```json
{
  "id": "main-doorway",
  "priority": "P0",
  "production_domain": "spatial_region",
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
world.visual.validate
world.gameplay.validate
world.final.validate
```

`world.visual.validate` accepts either:

- a report reference stored at `runtime.quality.visual.report_ref`; or
- `workflow_metadata.visual_validation_report` from a completed WorkflowRun for the same world.

A report reference may be an embedded report during tests/automation or a workspace-relative JSON artifact reference in normal production use.

`world.final.validate` requires visual validation to pass alongside the other required world quality domains. Validators report facts and evidence only; they do not create a second task state machine.
