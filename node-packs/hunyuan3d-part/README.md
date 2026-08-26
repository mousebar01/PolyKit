# Hunyuan3D-Part

Official PolyKit node-pack wrapper for geometric mesh part segmentation with Hunyuan3D-Part P3-SAM.

## Managed lifecycle

PolyKit owns the integration lifecycle but does **not** vendor or relicense Tencent Hunyuan3D-Part source/model weights.

- **Setup / Repair** downloads the pinned MIT adapter revision `48b9ee3540bf7a85bcb7eb982f748d0fe14195a8`, creates the isolated `venv/`, prepares the upstream runtime, and installs CUDA/native dependencies.
- **Download** on `Segment Mesh Parts` downloads only `p3sam/p3sam.safetensors` from `tencent/Hunyuan3D-Part` into the node's model directory.
- The Sonata backbone is kept as a setup/runtime-managed Hugging Face cache because upstream P3-SAM consumes it through cache semantics rather than as a plain checkpoint path.
- **Run** never installs dependencies or silently downloads the P3-SAM checkpoint.

The generated workflow contract is `mesh -> mesh`: one input mesh becomes one multipart GLB whose child meshes represent geometric parts. The upstream runtime exports geometry only, so the PolyKit wrapper re-splits the original textured input by the segmentation face labels (`glb_split.py`), preserving UVs, materials and the texture bitmap byte-identically; `part_separation` spreads parts outward on top.

## Requirements

- NVIDIA CUDA GPU.
- The adapter currently declares a 24 GB target. Lower-VRAM hosts may work with its conservative Windows sampling defaults but are not advertised as guaranteed.
- Review the upstream Hunyuan3D-Part license before use; the model/runtime license is separate from PolyKit and from the MIT integration adapter.

## Troubleshooting

If the node-pack reports that its runtime is missing, open **Models → Hunyuan3D-Part → Repair**. If it reports missing weights, download **Segment Mesh Parts** from the same page. Runtime/native dependency errors should be repaired through the node-pack setup rather than by installing packages into PolyKit's main Python environment.
