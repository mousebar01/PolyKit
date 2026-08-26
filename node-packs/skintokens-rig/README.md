# SkinTokens Rigging Node Pack

This pack adapts [SkinTokens](https://github.com/VAST-AI-Research/SkinTokens)
to PolyKit's server-owned workflow runtime. It accepts a `.glb` mesh and
returns a rigged `.glb` containing a generated skeleton and
`JOINTS_0`/`WEIGHTS_0` skin attributes.

The initial integration deliberately keeps the scope small:

- `auto-rig` is a `mesh -> mesh` node and can preserve the source materials and
  textures through SkinTokens' transfer path.
- The generated skeleton is mesh-specific, so it is not limited to a human
  template and is suitable for non-human/monster-shaped assets when the model
  can infer a coherent articulation.
- This node creates the rig and weights; it does not generate motion clips.
  Animation can be added as a separate node once the rig output is validated.

## Setup

Use the Models page's Setup/Repair action. `setup.py` creates the isolated
Python 3.11+ environment, installs PyTorch and provider dependencies, fetches
the pinned SkinTokens source revision, and applies the PyTorch SDPA fallback
for systems where the optional `flash-attn` binary is unavailable.

This is a bundled, reviewed official pack (`trusted`/`builtin`); it is not a
user-installed remote node pack. The adapter tracks a pinned MIT SkinTokens
revision, while model weights remain an explicit Hugging Face download.

Download the `TokenRig` resource from the Models page before running the node.
The resource contains the two SkinTokens checkpoints (about 1.6 GB). The
adapter fetches only the small Qwen3-0.6B configuration/tokenizer metadata on
first load; it deliberately does not download the 1.5 GB Qwen weight file.
All runtime files are stored under PolyKit's normal
`MODELS_DIR/skintokens-rig/auto-rig` path.

## Validation

The adapter should first be tested with a textured GLB. Keep **Preserve Source
Mesh** enabled to verify that the output retains the original material/texture
while adding the generated skin. A downstream viewer or Blender can then
rotate individual joints to inspect deformation continuity.

The provider requires an NVIDIA CUDA runtime and about 14 GB of free VRAM. The
adapter fails before model load when CUDA is unavailable or the available VRAM
is below that contract; it never falls back to returning an unrigged mesh.
It uses a local Blender-based transfer service during inference; the service
is started and stopped by the node-pack subprocess and never becomes part of
the FastAPI process.
