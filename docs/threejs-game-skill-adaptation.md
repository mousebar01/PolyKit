# Three.js game-skill adaptation

`majidmanzarpour/threejs-game-skills` is useful as an engineering checklist,
not as a runtime to copy into PolyKit. Its strongest ideas for this product are
the short game-design brief, deterministic input/update behaviour, primitive
collision proxies, and browser QA with reproducible screenshots and playtests.

## What PolyKit adopts

| Reference idea | PolyKit implementation |
| --- | --- |
| Define the player promise, verb, pressure, reward, and retry path | Keep the scene brief and relation graph in the Agent-authored `ScenePlan` |
| Build a playable loop before polish | Add a desktop walkthrough to `ScenePlanCanvas` before adding interaction systems |
| Use simple collision geometry | Derive 2D semantic AABB proxies from object dimensions and clamp to scene bounds |
| Prefer deterministic tests | Preserve `seed`, deterministic layout, deterministic walkthrough spawn, and server diagnostics |
| Validate in a real browser | Use the normal Web/FastAPI path, then run build, interaction, and visual checks |

## What stays out of scope

The external skill's Vite scaffold, game-specific runtime, Tripo/Gemini/
ElevenLabs integrations, combat/physics systems, and mobile controls are not
copied. FastAPI remains the only execution and persistence boundary; Blender
MCP remains an optional authoring/offline-render backend; Three.js remains the
browser viewer.

## Current demo

Open any valid `polykit.scene-plan` in the Agent scene preview or world asset
viewer. Click the gamepad button, click the viewport to lock the pointer, and
use WASD or arrow keys to walk. Press Esc to release the pointer; click the
button again to leave walkthrough mode. This is a spatial validation demo, not
a shipping game loop: it proves that the generated room, object placement, and
collision proxies agree from a first-person view instead of only from one
camera screenshot.

The server-side `metadata.layoutQuality` audit remains authoritative. A scene
that only looks correct from the editor camera is not considered valid.

Visual validation follows the same camera-independent principle. Inspect the
entry, opposite interior, and exterior angles in addition to the presentation
shot. This catches spatial errors that a single hero camera can conceal while
keeping Three.js as the display layer.
