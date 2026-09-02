# Game exporter

The `game-exporter` built-in pack prepares a server-owned mesh for handoff to a game editor. It has two process nodes:

- `game-exporter/unity-import-bundle` writes an `Assets/PolyKit` bundle, a stable Unity `.meta` file, and an import manifest.
- `game-exporter/unreal-import-bundle` writes a `Content/PolyKit` bundle and records Unreal import settings such as morph targets and optional auto collision.

Each node keeps a copy of the mesh as its primary `mesh` output and publishes the zip archive plus a JSON manifest as sidecars. This makes the node safe to place in a workflow before a final Output node.

The archives are interchange bundles, not native editor databases: Unity creates the final imported asset and `Library/` data, while Unreal creates `.uasset` files and cooked data. FBX/OBJ/DAE/3DS use Unity's built-in model importer. GLB/glTF needs a third-party glTF importer in Unity; Unreal still performs the editor import for every source format. PolyKit does not claim to generate `.unitypackage` or `.uasset` binaries without those editors installed.

Both manifests include source format, byte count, SHA-256, target path, and capability limitations so a handoff can be inspected or reproduced before opening the target editor.
