# Repair scopes

PolyKit validators can emit advisory `polykit.repair-scope` v1 records. A repair scope describes the smallest trustworthy area that a caller may choose to review or rebuild after validation.

Repair scopes are evidence, not execution state. They never schedule retries, mutate a World document, advance a WorkflowRun, or act as an Agent task runtime.

## Contract

A scope contains:

```json
{
  "schema_version": 1,
  "kind": "polykit.repair-scope",
  "id": "repair:world.spatial.validate:spatial.attachment.cabin.wall-floor",
  "source": {
    "capability": "world.spatial.validate",
    "check_id": "spatial.attachment.cabin.wall-floor"
  },
  "source_status": "fail",
  "locality": "relationship",
  "causal_system": "construction_geometry",
  "affected_object_ids": ["floor", "left-wall"],
  "affected_relationship_ids": ["wall-floor"],
  "affected_subject_ids": ["cabin", "wall-floor"],
  "action_hint": "repair_relationship_geometry",
  "safe_to_localize": true,
  "reason": "Final GLB violates the BuildSpec attachment tolerance."
}
```

`action_hint` is advisory only. No validator executes it.

## Locality

V1 locality values are descriptive rather than orchestration states:

```text
evidence
object
relationship
scene
```

An evidence failure means the right next step is to regenerate, attach, or review evidence rather than rebuild geometry.

## Causal system

The causal system tells a caller which production system is implicated. Examples include:

```text
construction_geometry
camera_composition
occlusion_geometry
scene_layout
silhouette
material
lighting
semantic_match
evidence_pipeline
game_spec
world_spec
```

This prevents an external Agent from guessing repair strategy from English error text alone.

## Safe localization

`safe_to_localize: true` means the validator carries enough bounded subject/part/relationship evidence to consider a local repair. It does not guarantee an installed backend can actually execute that scope.

For example, the Spatial Judge may prove that one `wall-floor` relationship is invalid and identify the two BuildSpec parts involved. The current Blender building backend may still only support rebuilding the whole building. The ProductionRecipe compiler must report that capability mismatch rather than silently widen the repair.

## World validator output

Every `world.*.validate` response may include:

```json
{
  "status": "fail",
  "issues": [],
  "repair_scopes": []
}
```

`world.visual.validate` also attaches the matching repair scope to `details.earliest_failure` when the earliest unresolved check has one.

`world.final.validate` inherits concrete repair scopes from its construction, visual, and gameplay child validators. It does not collapse them into a generic whole-scene rebuild recommendation.

## ProductionRecipe compiler

Repair scopes can be compiled through:

```text
POST /workspace-library/worlds/{world_id}/production-recipes/compile
```

The server reruns the requested validator and selects the scope from that authoritative result; it does not trust a caller-authored replacement scope.

The resulting `polykit.production-recipe` v1 either:

- returns `ready` with a reviewable WorkflowDefinition draft and canonical WorkflowExecutionRequest,
- returns `blocked` with the missing backend capability or required scope-expansion decision, or
- returns `no_workflow` when the failure is evidence-only.

See `docs/production-recipes.md`.
