# Rigging evidence

The built-in `rigging-evidence` pack keeps a small set of generic rigging checks and helpers that apply across asset types.

`rigging-evidence/attachment-anchor-audit` validates declarative relationships between attached components and component or bone anchors. It checks missing anchors, unresolved references, root parenting, cycles, and optional measured proximity without mutating geometry.

`rigging-evidence/rig-payload-audit` validates a portable pre-export skeleton payload: coordinate-system declarations, a rooted parent array, unique joint names, affine local matrices, and normalized four-slot skin weights.

`rigging-evidence/geodesic-bind` generates normalized four-slot skin weights from an explicit mesh and bone segments by propagating distance through the voxelized solid. Broken connectivity remains visible through unreachable vertex and bone findings.

`rigging-evidence/ik-solve` solves one explicitly ordered joint chain with CPU FABRIK, preserving segment lengths and reporting target error and unreachable targets. It does not create runtime armature constraints or animation clips.

Character-specific facial, lip-sync, Mixamo, chirality, and expression-clip checks are intentionally not part of the built-in surface. Those capabilities should return only when a complete user-facing character production workflow requires them.
