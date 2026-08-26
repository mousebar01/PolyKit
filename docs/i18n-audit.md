# i18n audit status

PolyKit follows one localization boundary: **translate presentation concepts, not machine identifiers**.

## Covered in the current i18n pass

- Models / Node Packs page shell, cards, detail drawer, install/download states, repair/sync controls and uninstall dialogs.
- Node-pack `i18n` metadata is preserved in the Models data path, so pack descriptions and node names can switch language without changing pack/node ids.
- Settings: Application, Storage, Integrations, About and MCP-facing explanatory UI.
- Technical names and values such as `GLB`, `mesh`, `CUDA`, `venv`, repository names, `MCP Server`, `Hugging Face Hub`, `Claude Desktop`, `Codex CLI`, and `OpenCode` remain stable.

## Remaining legacy localization debt

These areas still contain source-English presentation strings and should be migrated in a dedicated follow-up rather than mixed into the Models/Settings repair:

1. `src/areas/workflows/WorkflowsPage.tsx`
   - built-in node display labels such as Image, Text, Load 3D Mesh, Output, Preview Views and Note;
   - Node Library headings/help text and additional workflow-management copy.
2. Built-in process node-pack manifests under `src/areas/workflows/nodes/*/manifest.json`
   - several packs currently have English-only pack/node names, descriptions, parameter labels and tooltips.
3. `src/areas/assets/AssetsPage.tsx`
   - mostly localized already, but a small number of legacy literals remain (for example the Decimate popover Cancel button).

Backend/runtime diagnostics may remain English when they are raw technical errors. Controlled UI status labels should use the application dictionaries.
