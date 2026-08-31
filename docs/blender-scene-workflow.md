# Blender reference scene workflow

The `blender-cabin-reference` template is the first end-to-end Blender-backed
scene example. It follows the staged shape of the reference production skills
project without introducing a second runtime:

```text
Text brief → blender-scene/build → polykit.output → workspace GLB
                                      ├─ .blend sidecar
                                      └─ multi-angle PNG preview sidecars
```

`blender-scene/build` sends a fixed, reviewed cabin recipe to the Blender
backend used by the process node. The text input is recorded as scene metadata
(`polyKitBrief`) for provenance; the first preset is deterministic so layout
and Three.js loading can be verified before adding more presets. Arbitrary
Blender Python is not accepted as a node parameter.

## Running it

1. Make the configured Blender backend available for the `blender-scene` process
   node in the target environment.
2. Build the bundled process packs:

   ```bash
   node scripts/build-builtins.mjs
   ```

3. Restart/reload the FastAPI server so the bundled pack is synchronised into
   `~/.polykit/node-packs`.
4. In Workflows, choose **Blender Winter Cabin**, or submit its compiled graph
   to `POST /workflow-runs/execute`.

The final mesh is served at `/workspace/Workflows/<name>.glb`, so the existing
Three.js `Viewer3D` can load it with the normal workspace URL. The `.blend` and
PNG files are published beside it for Blender inspection and visual checks;
they are auxiliary outputs, not additional graph types. When preview rendering
is enabled, the node publishes the presentation view plus three inspection
views: `<name>_view_entry.png`, `<name>_view_hearth.png`, and
`<name>_view_exterior.png`. The inspection cameras are also stored in the
`.blend`, so the same scene can be checked from inside the entry, from the
hearth side, and from outside the cabin instead of trusting one carefully
framed screenshot.

## Spatial contract

The recipe uses Blender's native right-handed, Z-up world in meters. The glTF
exporter writes the standard right-handed, Y-up representation consumed by
Three.js `GLTFLoader`; the viewer does not need a second hand-written axis
conversion. Every generated mesh is tagged with `polyKitZone`
(`architecture`, `interior`, or `exterior`) and `polyKitRole`.

Before export, the Blender script checks the world-space bounding boxes of the
semantic zones. Interior props must stay inside the cabin floor plan and above
the floor; exterior snow and trees must stay outside the cabin boundary; and a
presentation camera must be present and aimed at the set. A failed check stops
the node instead of publishing a misleading GLB. The result metadata contains
the validation report for downstream checks.

The saved `.blend` also switches any available 3D Viewport to the presentation
camera. This is only an inspection convenience—the active render camera is
still stored on the scene and Three.js receives the regular GLB. The cabin
recipe keeps the left-wall door as a native Boolean opening, uses Array-owned
floor seams and roof rafters, adds a physically connected porch threshold, and
uses separate procedural wood/fabric responses. It clears old hidden objects
before rebuilding and validates those relationships before publishing.
Existing `.blend` files are snapshots; rerun the template to regenerate them
with the current recipe.

The presentation camera is not the only acceptance check. A scene is
considered visually plausible only after the entry, hearth, and exterior
renders all show the expected support and containment relationships; a
good-looking single camera angle cannot hide a wall, roof, or prop that is
misplaced on the other side of the set.

## Material source policy

The scene brief should describe material intent and art direction, not embed a
texture website URL. A caller or future material workflow may resolve that
intent through [Poly Haven Textures](https://polyhaven.com/textures), then
record the selected asset id, map set, resolution, and real-world UV scale in
scene provenance. Blender applies the result through shader nodes; the source
is therefore replaceable without changing object placement or the Three.js
viewer contract.

Poly Haven's assets are CC0. If PolyKit uses the live Poly Haven API rather than
a workspace-cached asset, the UI or asset metadata must credit Poly Haven and
requests must use a unique User-Agent. This keeps online lookup explicit and
avoids coupling a generated scene to an unavailable remote URL.

## What this proves

- FastAPI remains the owner of execution, cancellation, output naming and
  workspace persistence.
- Blender is an external authoring/render backend, not a browser-side runtime.
- The exported GLB contains named semantic objects and material assignments;
  Three.js parses it as a regular glTF 2.0 scene.
- More advanced scene composition can add reviewed presets or explicit asset
  inputs without changing the viewer contract.
