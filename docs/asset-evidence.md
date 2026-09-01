# Asset evidence

The built-in `asset-evidence` pack keeps four generic scene-level mesh helpers.

| Node | Purpose |
| --- | --- |
| `component-audit` | Reports scene components, world-space footprints, and overlap/near relationships without modifying geometry. |
| `pairwise-penetration` | Samples component surfaces to flag likely penetration, with explicit allowed-contact pairs. |
| `material-audit` | Reports declared PBR material channels and flags missing base-color or roughness evidence. |
| `normalize-mesh` | Applies an explicit scale/center/ground transform and records the normalization report. |

These checks complement `mesh-production/geometry-integrity`: mesh-production focuses on topology and triangle-level validity, while asset-evidence focuses on scene composition, component relationships, materials, and normalization.

Turntable and component-ID rendering are intentionally not separate built-in evidence nodes. Multi-view reference organization belongs to `reference-evidence`, while inspection rendering should be owned by the Blender/render workflow that actually needs it.

Read-only audits return the original mesh with JSON sidecars. `normalize-mesh` is the only node in this pack that intentionally writes transformed geometry.
