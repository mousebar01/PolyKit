# Repair scopes

PolyKit validators may return advisory `polykit.repair-scope` records alongside ordinary issues and evidence. A repair scope describes the smallest trustworthy area associated with an unresolved validation fact. It is not a retry, task, rollback command, workflow stage, or Agent state.

## Boundary

```text
WorldDocument + WorkflowRun evidence
              ↓
          validator
              ↓
 issue/check + repair_scope
              ↓
     external caller decides
              ↓
   ordinary WorkflowDefinition / WorkflowRun
```

The validator never executes the `action_hint`. It only reports a deterministic hint that a caller may use when authoring a later workflow.

## Contract

A v1 scope has this shape:

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

`affected_object_ids` contains stable World/BuildSpec part ids only when the validator has explicit evidence for them. `affected_subject_ids` preserves check subjects that may instead be observation, interaction, objective, camera, or other semantic ids.

`safe_to_localize` means the evidence identifies a bounded object or relationship that can be considered independently. It does not mean a local repair is guaranteed to succeed or that the validator is authorized to launch one.

## Locality

- `object`: one or more explicit objects/parts are implicated.
- `relationship`: an attachment or other explicit relation is implicated.
- `scene`: the failing fact is scene-wide or cannot safely be reduced to a part.
- `evidence`: generation/attachment of trustworthy evidence is the missing step; rebuilding scene geometry would be premature.

## Causal systems

v1 uses compact systems such as `scene_layout`, `camera_composition`, `occlusion_geometry`, `construction_geometry`, visual categories such as `silhouette` or `lighting`, `semantic_match`, `game_spec`, `world_spec`, and `evidence_pipeline`.

The causal system is an explanation boundary, not a workflow node type. A future recipe/compiler may map it to available Node Packs, but validators do not select or execute those nodes.

## Visual and spatial behavior

`world.spatial.validate` derives scopes from authoritative spatial checks. Attachment checks can use `source_part` and `target_part` metrics, so a failed wall/floor contact can identify the two relevant parts and the attachment id instead of recommending a whole-world rebuild.

`world.visual.validate` combines unresolved deterministic metric, semantic, and authoritative spatial checks. Its `details.earliest_failure` receives the matching repair scope when one exists, so callers can stop at the earliest causal category and keep accepted later systems untouched.

Missing or unreadable reports, missing final meshes, and similar integrity gaps produce `locality: evidence` rather than a geometry repair scope.

## Final validation

`world.final.validate` inherits repair scopes from construction, visual, and gameplay validation. This keeps final validation from collapsing a precise child failure into an unhelpful `rebuild_scene` recommendation.

## Ownership invariants

`WorkflowRun` remains the only durable execution lifecycle. `WorldDocument` continues to store domain facts and compact quality state. Repair scopes are validation evidence only: they own no retry budget, rollback target, stage state, queue, conversation, or task progression.
