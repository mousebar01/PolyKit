# Rigging evidence

`rigging-evidence/attachment-anchor-audit` validates the declarative
relationship between worn/held/hung components and their anchors. Feed it a
text JSON descriptor containing `componentTree`, optional `rig.bones`, and an
optional `measured` map of world positions.

The audit checks that attachments declare an anchor, resolve to a component or
bone, do not parent directly to the model root, and do not form cycles. When
measured positions are present it also checks the distance against an explicit
`attachment.maxOffset`, a fraction of the anchor extent, or the documented
fallback. Missing measurements remain `needs_review` rather than passing.

It emits a JSON text report and does not mutate geometry or create a second
runtime hierarchy.

`rigging-evidence/rig-payload-audit` validates the portable pre-export payload:
Y-up/right-handed coordinates, one rooted parent array, unique joint names,
affine local matrices, and four-slot finite non-negative skin weights that sum
to one. It reports unweighted joints as warnings so attachment-only bones are
not mistaken for failures.

`rigging-evidence/geodesic-bind` generates four-slot normalized skin weights
from a JSON mesh plus bone segments. It voxelizes the closed mesh, propagates
distance through the solid with 26-neighbour Dijkstra, and reports unreachable
vertices/bones instead of silently hiding broken connectivity. Optional
`components` and per-vertex `vertexComponents` pin rigid roles such as hair,
decals, and panels to an ancestor joint. Resolution and falloff power are
explicit parameters, and the output remains reviewable JSON rather than an
opaque black-box bind.
