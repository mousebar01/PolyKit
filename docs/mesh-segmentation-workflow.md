# Mesh part segmentation workflow

PolyKit treats part segmentation as a semantic `mesh.segment` operation:

```text
Load 3D Mesh
    ↓ mesh
Mesh Part Segmentation
    ↓ mesh
Output
```

The v1 contract deliberately remains `mesh -> mesh`. A segmentation provider returns one 3D container (normally GLB) whose child meshes / objects represent the discovered parts. The workflow does **not** turn one mesh edge into a list of files yet. That keeps downstream preview, output, material, rigging, and asset-library behavior compatible with the normal mesh protocol.

## Texture preservation

The upstream P3-SAM runtime rebuilds its output from vertices/faces only, so the input's UVs, materials and texture are lost in the provider result. PolyKit restores them in a wrapper-owned post-process (`glb_split.py`, pure stdlib):

- The original textured input mesh is re-split by the segmentation face labels (`segmentation.json`).
- Each part keeps the original vertex attributes (position, normal, UV, colors) and shares the original materials and texture bitmap; the texture bytes are copied verbatim, so there is zero quality loss.
- `part_separation` still spreads parts outward for inspection on top of the textured split.
- When the input mesh or labels are unavailable, or the GLB layout is unsupported (multi-mesh / multi-primitive), the output falls back to the provider's geometry-only result.

- The splitter handles single-mesh, single-primitive textured GLBs — the common shape for generated assets. This path has been validated end to end with the real P3-SAM runtime: the upstream face labels map onto the original mesh's faces, and each part keeps UVs plus the original texture bitmap byte-identically.

## Official node pack

Hunyuan3D-Part is shipped as an official PolyKit node-pack wrapper:

- Node pack: `hunyuan3d-part`
- Workflow node: `hunyuan3d-part/decompose-mesh`
- Pipeline stage: `p3-sam`
- Upstream model/runtime: Tencent Hunyuan3D-Part / P3-SAM
- Integration adapter: `DrHepa/Hunyuan3D-Part-modly-extension`
- Adapter revision pinned by PolyKit: `48b9ee3540bf7a85bcb7eb982f748d0fe14195a8`

PolyKit ships only the reviewed manifest, wrapper and setup bootstrap. **Setup / Repair** fetches the pinned MIT adapter and creates its isolated runtime; Tencent source/runtime components and model weights are not committed to PolyKit. Review the upstream Hunyuan3D-Part license before use.

## Setup for validation

1. Open **Models → Hunyuan3D-Part**.
2. Run **Repair / Setup**. This creates the isolated `venv`, installs CUDA/native dependencies, prepares the upstream runtime, and manages the Sonata Hugging Face cache used by P3-SAM.
3. Download **Segment Mesh Parts**. PolyKit downloads only `p3sam/p3sam.safetensors` from `tencent/Hunyuan3D-Part` (the current ~451 MB safetensors checkpoint), rather than cloning the whole Hunyuan3D-Part model repository.
4. Return to **Workflows** and use **Mesh Part Segmentation (P3-SAM)**.

Runtime setup and model download are deliberately separate. Re-running a workflow never silently installs dependencies or downloads the P3-SAM checkpoint.

The provider requires an NVIDIA CUDA environment. Its adapter currently declares a 24 GB target; P3-SAM is the validation target for this workflow. X-Part/full decomposition remains outside the official workflow until those paths are validated to the same standard.

## Default workflow

The bundled template uses conservative, explicit parameters:

```json
{
  "pipeline_stage": "p3-sam",
  "max_parts": 32,
  "output_mode": "primary",
  "semantic_resolver": "off",
  "seed": 42,
  "export_format": "glb",
  "quality_preset": "balanced"
}
```

`max_parts` is a ceiling, not a promise that exactly 32 parts will be returned. P3-SAM performs geometric part segmentation; the initial output should not be interpreted as semantic labels such as "wheel" or "arm" unless a later semantic resolver explicitly provides them.

## Execution contract

Mesh-primary model nodes are different from the original image-generation contract:

- `mesh` may be the only primary input; an image is no longer required.
- The server passes a filesystem `Path` to direct generators and to isolated node-pack subprocesses through the generic `primary_input` IPC envelope.
- The legacy `image_b64` subprocess field remains unchanged for image models.
- `params.mesh_path` is also supplied for compatibility with older mesh-aware adapters.
- Mesh-only model nodes do not implicitly enable texture generation.
- A generator may return a `Path`, a path string, or a mapping containing `primary_mesh`, `mesh`, `filePath`, `path`, or `output_path`.
- The returned mesh remains an intermediate `MeshArtifact` until `polykit.output` publishes it.
- A mesh-primary transform inherits the input artifact's coordinate-space metadata. Segmentation must not introduce an implicit basis rotation.

## Managed node-pack state

The official pack source and runtime state have different owners:

```text
Bundled / synced code
node-packs/hunyuan3d-part/
  manifest.json
  generator.py
  setup.py

Runtime state (not committed)
NODE_PACKS_DIR/hunyuan3d-part/
  provider/       pinned MIT adapter
  venv/           isolated Python environment
  .upstream/      runtime source prepared by provider setup
  .cache/         provider/Hugging Face runtime cache

Model state
MODELS_DIR/hunyuan3d-part/decompose-mesh/
  p3sam/p3sam.safetensors
```

Official-pack sync refreshes reviewed code files while preserving the runtime directories. This means application updates do not intentionally delete an already prepared Hunyuan environment.

## What to validate

Use a GLB with clearly separable parts first (for example a chair, vehicle, or simple character accessory set), then test a harder organic mesh.

Check the following:

1. Confirm **Hunyuan3D-Part** appears in Models without manually installing a GitHub node pack.
2. Run **Repair / Setup**, restart/reload if prompted, and confirm the node no longer reports a missing isolated venv.
3. Download **Segment Mesh Parts** and confirm only the P3-SAM checkpoint is fetched to its model directory.
4. Select **Mesh Part Segmentation (P3-SAM)**.
5. In **Load 3D Mesh**, choose a workspace or local mesh and run without connecting an image node.
6. Confirm the model loads in its isolated environment and progress reaches the workflow HUD.
7. Confirm the output is published as a GLB in `Workflows`.
8. Open the GLB and inspect its scene hierarchy: it should contain multiple mesh objects/components when P3-SAM finds multiple parts.
9. Confirm the whole result keeps the source orientation and scale.
10. Cancel one run during model execution and confirm the run exits without publishing a partial final asset, then run again to confirm recovery.

## Failure messages that should be actionable

- **Unknown executable node `hunyuan3d-part/decompose-mesh`**: run **Repair / Setup** and reload the node-pack registry; this normally means the isolated runtime has not been prepared yet.
- **Model not downloaded / checkpoint missing**: download **Segment Mesh Parts** in Models.
- **Isolated env requires a venv**: run **Repair / Setup** for Hunyuan3D-Part.
- **Provider runtime is not set up**: the pinned adapter bootstrap is missing or incomplete; run **Repair / Setup** again.
- CUDA / native-extension errors: use the provider setup diagnostics; these are environment/runtime failures rather than workflow-link errors.
- A returned path that does not exist is rejected before publication rather than producing a broken asset-library entry.

## Future extension: explode parts

If downstream workflows need each part as an independent asset, add a separate operation after segmentation (for example `mesh.explode_parts`) rather than changing `mesh.segment` to emit a batch. That keeps segmentation, part extraction, naming, and asset publication as independently testable steps.
