# PolyKit agent architecture rules

PolyKit is **Web-first**. Treat these rules as repository invariants when changing the product.

## Runtime ownership

- **FastAPI owns product execution and durable product state.** Generation, workflow execution, editable workflow definitions, model/process-node execution, job state, cancellation, persistence, and workspace artifacts live on the server.
- **The React Web client is the primary UI.** Browser and packaged desktop builds use the same React application and HTTP API contracts.
- **Electron owns packaging and local OS integration.** Keep it focused on windows, local server/bootstrap, native dialogs/filesystem access, updates, and packaged resources.
- When a product capability is missing from the Web path, add the server capability first and let both Web and Electron use it.

## Canonical API contracts

- Use `/workflow-runs/*` for generation/workflow submission, status, reconnect, and cancellation.
- Use `/workflow-definitions/*` for editable workflow graph list/save/delete. Browser and Electron share this server-owned store.
- Treat `/generate/*` as compatibility-only. New code targets the canonical workflow-run API; retire the legacy router together with its CLI command and documentation.
- Treat workspace artifacts as server-owned. Prefer workspace-relative paths and `/workspace/...` URLs.

## Frontend boundaries

- Shared React code uses FastAPI for product behavior and the browser-compatible `window.electron` bridge for genuine OS integration.
- Keep Web code browser-native. Node.js, Electron IPC, subprocesses, and local absolute-path behavior belong in Electron/server-specific code.
- `src/web/web-electron.ts` adapts the desktop-shaped interface for browsers. Desktop-only no-op methods there are interface shims, not product logic.
- Workflow import/export may use native file UX, while editable workflow persistence stays in the FastAPI definition store.

## UI and design system

- **Read and follow `DESIGN.md` before changing product UI.** It is the design + code contract for PolyKit.
- Use Tailwind for styling and semantic theme tokens (`background`, `card`, `muted`, `primary`, `border`, etc.) for ordinary feature UI.
- Use shadcn primitives under `src/shared/components/ui/` for standard controls such as buttons, dialogs, switches, selects, inputs, badges, tabs, tooltips, dropdowns, and toasts.
- Put feature-specific composition next to the feature that owns it; put universally reusable primitives in `src/shared/components/ui/`.
- Use Lucide for generic product icons.
- Canvas/data visuals may stay bespoke; controls layered over 3D/workflow canvases still use shared UI primitives.

### UI copy

- Keep UI text concise. Prefer the shortest wording that still makes the action, state, or consequence clear.
- UI is not documentation. Labels, buttons, menus, tooltips, empty states, dialogs, and helper text should contain only information needed at that moment.
- Let surrounding headings, controls, icons, and context carry information instead of repeating it in copy.
- Buttons and menu items normally use short action labels such as `Save`, `Delete`, `Retry`, `Import`, `Open`, or `Reload`.
- Prefer one short sentence over multiple explanatory sentences. Remove filler such as `Please`, `You can`, `This will`, and `In order to` when meaning stays clear.
- Dialog descriptions focus on consequences or information needed before a decision.
- Error messages state what failed and, when useful, the next action.
- Tooltips add information rather than restating the visible label.
- Keep implementation and architecture details out of normal product copy unless the user needs them to complete the task.
- Preserve important warnings while shortening existing copy.
- Before adding UI copy, check whether fewer words express the same meaning.

## Node packs and workflows

- Editable workflow graphs persist through `/workflow-definitions/*`, compile to a server execution payload, and execute through `/workflow-runs/*`.
- Model and process node packs run through the server runtime so Web and desktop share execution behavior.
- Centralize output naming, path validation, cancellation, workflow persistence, and job lifecycle logic on the server.
- Third-party node-pack installation executes external code. Add remote install/uninstall capabilities only with an explicit permission and trust model.

## Change review checklist

Before merging a change, confirm:

1. The feature works through the browser/FastAPI path.
2. FastAPI owns execution and durable product state.
3. New generation/workflow callers use canonical `/workflow-runs/*` and `/workflow-definitions/*` APIs.
4. Existing server logic is reused for paths, persistence, jobs, output naming, and node execution.
5. Electron remains a thin shell over the same Web app and API.
6. Touched UI follows `DESIGN.md` and shared shadcn primitives.
7. Migration-only wrappers are removed after their callers move to the canonical path.
8. Touched UI copy is concise and avoids repeating visible context.

A change is ready when it reinforces one runtime, one durable store, one UI system, and clear product copy.
