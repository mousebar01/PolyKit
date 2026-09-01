# Node Packs & Workflow Templates

This page explains how PolyKit packages model/process runtimes ("node packs") and how validated workflows become reusable templates.

## Node pack structure

A **node pack** is an installable unit that provides executable nodes to the workflow DAG. It is a directory installed from GitHub or a local folder with files such as:

```text
<pack-id>/
├── manifest.json     # pack + node declarations
├── generator.py      # model pack entry
├── processor.py/.ts  # process pack entry
├── setup.py          # optional environment setup
└── venv/             # optional isolated Python environment
```

`manifest.json` declares one or more nodes with typed inputs/outputs and a parameter schema. The node catalog (`api/services/node_catalog.py`) merges built-in, model, and process nodes into `GET /node_types`, which drives both the editor and executor.

```text
pack id: trellis2
nodes:
  trellis2/generate   image -> mesh
  trellis2/refine     image+mesh -> mesh
```

## Environment model

Node packs use the **shared API environment** by default. A pack can opt into an **isolated venv** by declaring `"env": "isolated"` in `manifest.json`.

| Value | Interpreter | When to use |
|-------|-------------|-------------|
| `"shared"` (default) | API environment, or a thin pack `venv/` that points back to it | Dependencies are compatible with the shared runtime |
| `"isolated"` | Pack-owned `venv/` built by `setup.py` | The pack needs conflicting Python/CUDA dependencies |

The shared API environment (root `pyproject.toml`) includes FastAPI, uvicorn, httpx, python-multipart, trimesh, pymeshlab, `huggingface_hub`, and certifi. It deliberately excludes the PyTorch/CUDA stack; model packs that need torch or GPU-native wheels opt into an isolated venv.

### Installing or repairing an environment

1. **Shared pack** — place the pack under `NODE_PACKS_DIR` and reload the registry.
2. **Isolated pack** — the Models page runs `setup.py` during install; **Repair** reruns it when the environment is missing or broken. In Web mode this runs on the connected server, not in the browser.

Headless servers allow Repair for bundled official packs. Third-party setup remains
blocked unless the operator explicitly enables it with
`POLYKIT_ALLOW_NODE_PACK_SETUP=1` after reviewing the code. Build an isolated
environment before starting a locked-down server when Web Repair is unavailable:

Download sources are server-owned and can be configured from Settings → Network
or through `/settings/sources`. The Hugging Face endpoint affects model listing
and weight downloads; the PyPI index affects `uv` dependency installs; the
PyTorch index is separate because CUDA wheels are not normally mirrored by a
standard Python package index. SkinTokens' pinned GitHub provider archive
continues to use the configured proxy for now. Leave a field blank to use the
official source.

```bash
cd <NODE_PACKS_DIR>/trellis2
python setup.py '{"python_exe":"<venv-python>","ext_dir":"<abs-path>","gpu_sm":89,"cuda_version":126}'
```

`gpu_sm` is the GPU compute capability, for example `89` for an RTX 4090. Use `0` when GPU-specific wheels should be disabled. If the API process runs under an older Python, set `POLYKIT_SETUP_PYTHON` to a Python interpreter that satisfies the pack's `python_min` requirement.

## Data directories

Node packs live in `NODE_PACKS_DIR`, defaulting to `~/.polykit/node-packs`.

Large model weights stay outside the pack folder under `MODELS_DIR/<pack>/`. Path resolution is centralized in `api/services/folder_paths.py`. Packs declare a relative weight location through `download.location`, and generators resolve it with `folder_paths.get_weights_dir()` instead of hardcoding filesystem paths.

## Model-weight downloads

Weights are not bundled with node-pack code. When a model node runs without its required weights, the generator can download them through `huggingface_hub` before generation.

- Target: `MODELS_DIR/<pack>/`
- Only manifest allow-patterns are fetched.
- Gated/private repositories can use `HUGGING_FACE_HUB_TOKEN` or `HF_TOKEN`.
- The manifest download check file marks the pack as downloaded.

A workflow can therefore download missing weights first and then continue generation without a separate manual step.

## Bundled packs

PolyKit supports both bundled nodes and user-installed extensions:

- **Official model packs** live under `node-packs/<pack>/` in the repository.
- **Built-in process packs** live under `src/areas/workflows/nodes/*` and compile to `out/builtin-node-packs/`.
- **Runtime packs** live under `NODE_PACKS_DIR` and include synchronized official packs plus user-installed third-party packs.

At server start, `api/services/official_packs.py` performs a one-way sync into `NODE_PACKS_DIR`:

- bundled code is refreshed when needed;
- runtime state such as `venv/`, `__pycache__/`, `node_modules/`, and weights is preserved;
- a bundled pack may carry its own runtime state (`venv/`, `provider/`, `.upstream/`, `.cache/`) to be self-contained: those directories are **seeded** into the runtime dir on first sync when absent, and never refreshed afterwards, so a machine-prepared environment always wins;
- synchronized packs carry a `.polykit-official` marker and use `trusted: true, builtin: true` so they cannot be uninstalled from the UI.

## Prestartup scripts

A model pack may include `prestartup_script.py`. It runs inside the pack's interpreter before `generator.py` is imported, allowing the pack to prepare environment variables, vendored libraries, or lightweight runtime setup.

Failures are logged but do not block server startup. `setup.py` is for install-time environment construction; `prestartup_script.py` is for per-process startup work.

## Declarative manifest

A model pack can declare its runtime contract in `manifest.json`:

```jsonc
{
  "id": "trellis2",
  "env": "shared",
  "trusted": true,
  "builtin": true,
  "requirements": ["Pillow", "numpy", "trimesh", "gguf", "huggingface_hub"],
  "download": {
    "kind": "huggingface",
    "repo": "Aero-Ex/Trellis2-GGUF",
    "location": "trellis2",
    "check": "pipeline.json"
  }
}
```

The registry exposes this metadata through `GET /node-packs/list`, allowing the Models page to show environment, dependency, and download information without hardcoding pack details.

## Runtime dependency policy

Dependencies are installed declaratively by setup/install flows, not silently during a workflow run. If a required Python package is missing, the generator should fail with an actionable setup/Repair error.

PolyKit's setup flows use `uv pip` for both shared and isolated environments. Install
uv before installing a pack; a missing uv executable is reported as a setup error.

Legacy/debugging environments can opt into on-the-fly installation with:

```text
POLYKIT_AUTO_INSTALL_MISSING_PACKAGES=1
```

## Execution engine

`api/services/workflow_executor.py` executes the DAG while the HTTP router manages request handling, job records, and the single-GPU slot.

The engine provides:

- **Topological execution** — resolves upstream references and runs source, model/process, and output nodes in dependency order.
- **Input-signature cache** — caches model/process outputs from class, literal parameters, and upstream signatures. Changing one input recomputes only the affected subtree. Nodes using `seed == -1` are not cached. Disable caching with `POLYKIT_DISABLE_NODE_CACHE=1`.
- **Preflight validation** — rejects invalid or type-mismatched links before execution.
- **List mapping** — inputs resolving to lists can execute per item and return lists of outputs.

Cached mesh outputs point at workspace files, so reusable results can survive a server-process restart.

## Server-workspace sources

A local Web editor can drive a remote backend because workflow source nodes reference server-owned workspace data rather than browser-machine absolute paths.

- **Image / Load 3D Mesh** upload local files through `POST /workspace-library/upload` and store the returned workspace path.
- **Load 3D Mesh** can select an existing backend mesh through `GET /workspace-library/list`.
- The server resolves mesh sources with payloads such as `{"kind":"workspace_path","path":"Workflows/x.glb"}`.

The backend never needs direct access to the user's local filesystem.

## Workflow templates

A **template** is a validated workflow snapshot stored as JSON under `src/areas/workflows/templates/*.json`. It uses the same execution path as any other workflow; there is no separate template executor.

Built-in templates currently include:

| Template | Nodes | Notes |
|----------|-------|-------|
| `trellis2-geometry-mesh` | image → generate → output | Fastest path, no texture |
| `trellis2-textured-mesh` | image → generate → refine → output | Adds PBR texture |
| `anima-trellis2-text-to-3d` | text → Anima → background removal → Trellis.2 → texture mesh → output | Stylized text-to-3D pipeline that publishes a textured GLB |

Each template declares `requires: ["<pack-id>"]`. The editor lists templates separately and shows an install hint when a required pack is missing. Built-in process packs, such as `image-background-remover`, the `reference-evidence/*` image gates (including deterministic Divine Eye, interior-difference, multi-view, hair evidence, pose-sweep, and the soft-signal hair gate), the `character-evidence/*` provenance-aware humanoid proportion, hair-profile validation/compilation, and scalp-exposure specs, the `geometry-evidence/*` swept-arc shape audit, the `asset-evidence/*` component/material/penetration checks, the `rigging-evidence/*` attachment, rig-payload, chirality, geodesic-bind, facial-rig, lip-sync, expression-clip, IK, and Mixamo checks, the `mesh-production/*` projection, visual-hull, UV, map, LOD, collision, BVH, animation, morph, joint-loop, and clothing blockout derivatives, the `environment-production/*` seeded terrain mesh, city blockout, and vegetation scatter generators, and the `game-exporter/*` Unity/Unreal import bundles, are included in the same server-owned inventory as model packs. Evidence and derivative nodes publish measured reports (and, for LOD generation, additional GLB levels) as sidecars, making reference, mesh, game-runtime, and engine-handoff artifacts available to later modeling and validation steps without a browser-local path. Game bundles remain honest interchange packages: the target editor still creates native `.unitypackage`, `.uasset`, and cooked data.

To add a template:

1. Build and run the workflow successfully in the editor.
2. Save its `nodes`, `edges`, `templateId`, `name`, `description`, and `requires` as JSON under `src/areas/workflows/templates/`.
3. The editor discovers the file through `import.meta.glob` without an additional code registration step.

Only promote workflows that have run successfully end to end.
