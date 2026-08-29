# PolyKit domain context

## Assets and scenes

- **Asset**: Any server-owned, addressable item shown in the asset library.
- **Leaf asset**: A single image, mesh, rig, animation, or sidecar file.
- **Generated scene**: A compound asset persisted as `<scene-id>.world.json`. It
  contains the terrain specification, regions, placements, agent plan, and
  references to leaf assets. The UI calls this a scene; `polykit.world` and
  `generated-world` remain the stable storage/API identifiers.
- **Workflow**: An editable graph and its execution record. It creates or
  transforms assets but is not itself the generated scene.
- **Scene manifest**: Technical metadata/index data; it is not the editable
  generated scene document.

The asset library is the single user-facing entry point for generated scenes.
The Three.js renderer, scene inspector, server-owned world store, and Agent
preview remain shared capabilities rather than a second top-level page.
