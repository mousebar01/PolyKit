# Spatial validation

PolyKit's Spatial Judge is a deterministic evidence layer for World validation. It reads the final mesh artifact published by a `WorkflowRun` and cross-checks it against server-owned `WorldDocument` facts. It does not own retries, repair execution, workflow stages, or Agent state.

## Evidence boundary

```text
WorldDocument / BuildSpec / ScenePlan
              +
Visual target camera facts (optional)
              +
WorkflowRun final GLB
              ↓
      SpatialValidationBundle
              ↓
       world.spatial.validate
              ↓
       world.visual.validate
```

The final GLB is authoritative geometry evidence. Caller-authored spatial scores do not replace this inspection.

## Camera contract

Camera visibility is evaluated only when the visual target supplies a deterministic camera contract, or explicitly requires visibility. Camera facts belong to the immutable visual target; they are not workflow lifecycle state.

```json
{
  "camera_id": "camera-main",
  "camera_revision": 3,
  "require_visibility": true,
  "camera": {
    "id": "camera-main",
    "revision": 3,
    "position": [0.0, 1.6, -5.0],
    "target": [0.0, 1.2, 0.0],
    "up": [0.0, 1.0, 0.0],
    "vertical_fov_deg": 50.0,
    "aspect_ratio": 1.7777778,
    "near": 0.05,
    "far": 1000.0
  }
}
```

If `camera.id` / `revision` are present they must agree with `camera_id` / `camera_revision` on the target.

For P0 observations that reference a `world_object_id`, the judge can check:

- ScenePlan object + compiled instance presence;
- intersection of the object's compiled bounds with the target camera frustum;
- deterministic line-of-sight rays from the camera to matching final-GLB geometry.

An object proven outside the frustum fails. Sampled occlusion is conservative: if no ray reaches the target first, the result remains `needs_review` unless stronger evidence proves invisibility. Missing camera or geometry evidence never becomes PASS.

## Volume relations

BuildSpec `inside` and `passes-through` relations are evaluated from final mesh volume evidence when the geometry is suitable.

### inside

PASS requires:

- one resolvable source mesh;
- one target container mesh;
- a watertight, convex target container;
- complete source-vertex evidence within the bounded validation budget;
- every source vertex inside or on the target boundary within tolerance.

A sampled source point outside the container is a FAIL. If the target is non-watertight/concave or the evidence budget is incomplete, the judge does not invent a PASS.

### passes-through

PASS requires:

- one watertight source mesh;
- one watertight, convex target volume;
- a BuildSpec anchor position and directional normal;
- source evidence inside the target volume;
- source evidence outside on both positive and negative sides of the anchor plane;
- complete bounded evidence.

This distinguishes a true crossing from a source that merely enters the target from one side.

## Bounded evidence

The judge deliberately caps expensive deterministic work. Current guards include a finite source-sample budget for volume checks and a finite triangle budget for line-of-sight. Exceeding a budget produces `needs_review` rather than silently promoting a partial sample to PASS.

## Snapshot

The final-GLB inspection emits `polykit.spatial-snapshot`. Per geometry it records world-space bounds, vertex/face counts, and whether the mesh is watertight and convex. The snapshot is evidence; it is not a new durable task-state machine.

## Status rules

Required spatial checks use the same fail-closed vocabulary as visual validation:

```text
pass
needs_review
fail
not_evaluated
```

A required FAIL vetoes the Spatial Judge. Missing or incomplete required evidence keeps the result at `needs_review`. Only complete required evidence can produce PASS.
