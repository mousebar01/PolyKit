# Reference evidence

PolyKit now includes the `reference-evidence` process pack. Its evidence nodes
are modeling-quality gates inspired by the local `img2threejs` reference:
before geometry is judged, the reference needs an explicit, reviewable detail
inventory and enough image quality to support that review.

`reference-evidence/reference-quality` measures resolution, luminance
contrast, dynamic range, edge energy, and alpha coverage. It emits a status
border and a `*-reference-quality.json` sidecar, flagging insufficient or
low-information references before an expensive model run.

`reference-evidence/material-palette` extracts dominant colors from the image,
adds a swatch board, and records RGB/HSV/luminance shares in a
`*-material-palette.json` sidecar. The palette is a prioritization aid, not a
calibrated PBR measurement.

`reference-evidence/material-region` narrows that evidence to a normalized
crop, recording local luminance, saturation, edge energy, and a small palette
alongside explicit low-confidence PBR channel placeholders. It keeps region
assignment reviewable instead of treating a whole-image color as a material
truth.

`reference-evidence/gradient-stops` samples median RGB/HSV values along a
horizontal or vertical material crop axis, emits a swatch strip, and flags
blue-collapse risk for saturated violet/magenta/blue stops. The stops are
reference evidence for a texture recipe, not a calibrated shader.

`reference-evidence/pbr-evidence` emits de-lit albedo, roughness, height,
image-gradient normal, and low-frequency AO PNG sidecars plus a four-panel
contact sheet. Every channel is explicitly low-confidence image evidence; it
is useful for a starting material recipe, not an inverse-rendered PBR truth.

`reference-evidence/landmark-guide` adds a 10-percent anatomy grid and
head/face/shoulder/hip guide points, with an unreviewed normalized landmark
skeleton in JSON. It packages the measurement surface for character work but
does not claim to detect a face automatically.

`reference-evidence/delight-albedo` makes a deterministic low-frequency
illumination correction and emits a PNG plus method report. It is a projection
texturing aid only: highlights, cast shadows, and color drift still require
visual review before the image is treated as an albedo reference.

`reference-evidence/camera-guide` emits a `referenceCamera` descriptor with an
aspect-ratio-aware FOV starting guess, pose/distance hints, and a framing
overlay. It is deliberately a review scaffold rather than camera calibration.

`reference-evidence/camera-fit` accepts the reference image plus a text-node
JSON payload containing at least six `{world: [x, y, z], observed: [px, py]}`
correspondences. It numerically fits FOV, Euler orientation, and position,
then emits an observed/projected landmark overlay and per-point residuals.

`reference-evidence/projection-plan` validates a target mesh id, projection
mode, texture size, and unseen-region strategy, then emits a framed overlay and
a renderer-facing bake descriptor. It records the real runtime steps while
leaving pixel sampling to a downstream Three.js projection pass.

`reference-evidence/reference-compare` accepts two connected images (reference
first, candidate second), resizes the candidate to the reference frame, and
emits a three-panel contact sheet with a difference heatmap and pixel metrics.
The metrics localize discrepancies but do not replace geometric or semantic
review.

`reference-evidence/multi-view-evidence` accepts two or more connected images
through a batch input, normalizes their heights, and emits a labeled contact
sheet plus a view manifest. It preserves view evidence and ordering without
pretending to solve camera pose or reconstruct geometry.

`reference-evidence/interior-difference` compares two renders on a normalized
foreground lattice. Only cells classified as figure in both images contribute,
so a matching outline cannot hide missing eyes, seams, or other interior
appearance changes. Optional normalized height bands make head/torso-specific
checks possible; the output includes the measured cell count and heatmap.

`reference-evidence/detail-inventory` accepts an image and produces:

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

The mesh-side companions live in the `asset-evidence` pack. They provide
component footprints, material-channel gates, explicit normalization, and a
multi-view turntable contact sheet; see [asset evidence](asset-evidence.md).
