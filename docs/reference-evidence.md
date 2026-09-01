# Reference evidence

PolyKit now includes the `reference-evidence` process pack. Its two nodes are
the first modeling-quality gates inspired by the local `img2threejs` reference:
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
