# Blender production

The built-in `blender-production` pack exposes a focused set of bounded Blender operations through the official server-side bridge.

| Node | Purpose |
| --- | --- |
| `opening` | Creates an architectural door/window opening with a Boolean cutter and semantic frame. |
| `array-stairs` | Builds a parametric stair flight with explicit run, rise, width, and rail parts. |
| `curve-profile` | Turns an explicit point path into a beveled curve/mesh for cables, trim, and railings. |
| `assembly` | Creates independent semantic parts with connector metadata and explicit gaps/tolerances. |
| `surface` | Applies a bounded production material preset to imported mesh objects. |
| `lighting` | Adds an accountable inspection/studio lighting setup around an asset. |
| `deform` | Applies a bounded Simple Deform operation with explicit axis and angle. |

The pack intentionally avoids exposing arbitrary Python. Each node maps to a bounded server-owned operation and publishes GLB output plus optional Blender/preview sidecars.

Geometry Nodes authoring, simulation setup, NPR renderer construction, and a second Blender-specific geometry-report surface are intentionally excluded from the built-in node catalog. They are either specialist workflows or overlap with generic mesh validation. They should return only when a complete user-facing workflow needs them.

Blender remains valuable as the execution backend for operations that genuinely require Blender semantics; generic topology validation and evidence stay in the engine-independent mesh packs.
