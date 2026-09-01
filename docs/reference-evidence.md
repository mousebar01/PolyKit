# Reference evidence

PolyKit now includes the `reference-evidence/detail-inventory` process node.
It is the first modeling-quality gate inspired by the local `img2threejs`
reference: before geometry is judged, the reference needs an explicit,
reviewable detail inventory.

The node accepts an image and produces:

- a normal PNG image artifact with a 2×2 through 5×5 review grid;
- a `*-detail-inventory.json` sidecar with source dimensions, SHA-256, region
  bounding boxes, and a 15-item detail checklist (gloss, bevel, fastener,
  seam, scratch, decal, groove, and related identity cues);
- optional PNG crops for each region.

The checklist is intentionally marked `needs_visual_review`. The node does
not claim that a detail exists merely because a grid cell was created. A human
or vision model must confirm each item, attach evidence regions, and then map
confirmed details to a concrete mesh component, material, or procedural
operation.

The result follows the existing server-owned WorkflowRun contract. Connect the
node to `Image Output` to publish the overlay and sidecars into the selected
workspace collection. The process workspace is run-private until that sink
publishes it, and sidecars are copied beside the output by the FastAPI runtime.

This first slice deliberately stays deterministic and dependency-light. The
next compatible additions are object-ID/per-component capture, per-channel
material evidence, and bounded localized correction against the inventory.
