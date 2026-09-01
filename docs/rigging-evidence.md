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

`rigging-evidence/facial-rig-audit` checks blendshape names, duplicate channels,
normalized ranges, and optional minimal or ARKit-lite required channels.
`rigging-evidence/lip-sync-audit` checks the portable viseme map and optional
time-sampled mouth curves. These are compatibility gates only: they do not
generate expressions, recognize speech, or prove facial quality in a render.

`rigging-evidence/ik-solve` solves one explicitly ordered joint chain with
CPU FABRIK. It preserves the source segment lengths and reports target error,
reach limits, and the solved joint positions. It does not create armature
rotations or constraints; unreachable targets are reported as `needs_review`.

`rigging-evidence/mixamo-audit` normalizes common `mixamorig:` and L/R aliases,
checks the Mixamo core body hierarchy, detects duplicate names and cycles, and
can require first-phalanx finger roots. It is a retargeting preflight only; the
target runtime still owns bind-pose orientation and animation import.
