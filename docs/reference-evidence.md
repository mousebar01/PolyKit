# Reference evidence

The built-in `reference-evidence` pack keeps a small set of reusable image-analysis helpers for generation and review workflows.

| Node | Purpose |
| --- | --- |
| `reference-quality` | Measures resolution, contrast, dynamic range, edge energy, and alpha coverage before expensive generation or matching work. |
| `material-palette` | Extracts dominant reference colors and writes a swatch board plus machine-readable palette evidence. |
| `material-region` | Measures color, luminance, saturation, edge energy, and cautious material evidence inside an explicit normalized crop. |
| `pbr-evidence` | Produces reviewable albedo, roughness, height, image-gradient normal, and AO sidecars as low-confidence starting evidence. |
| `camera-fit` | Fits FOV, Euler orientation, and camera position from explicit 3D-to-2D correspondences. |
| `delight-albedo` | Suppresses broad illumination with deterministic low-frequency division before projection texturing. |
| `reference-compare` | Resizes a candidate to the reference frame and emits a deterministic difference heatmap and pixel metrics. |
| `multi-view-evidence` | Normalizes two or more connected images into a labeled contact sheet and view manifest. |

These nodes deliberately separate measurable image processing from semantic claims. A palette is not calibrated PBR truth, an image-derived normal is not a high-to-low bake, and a difference heatmap does not prove geometry or identity correctness.

`camera-fit` is the only built-in camera helper because it works from explicit correspondences and reports reprojection error. Heuristic camera guesses and standalone projection-plan descriptors are intentionally excluded; actual projection texturing belongs to `mesh-production/projection-bake`.

The pack also avoids role-specific landmark, hair, pose, and composite scoring gates. Those capabilities should only return when a complete user-facing workflow requires them rather than as isolated evidence experiments.

All outputs remain server-owned WorkflowRun artifacts. Image outputs and sidecars are published through the normal workflow sink into the selected workspace collection.
