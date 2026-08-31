# PolyKit — FastAPI Product Runtime

FastAPI is PolyKit's single execution boundary. The browser Web UI, CLI and MCP
clients all use this service for generation, workflow execution, job state,
cancellation, workspace artifacts, and canonical workflow-definition
persistence.

## Setup

```bash
cd ..
uv sync --python 3.11
cd api
# Windows
..\.venv\Scripts\activate
# macOS/Linux
source ../.venv/bin/activate
```

`api/requirements.txt` 保留给兼容旧环境的手动安装；新环境使用根目录的 `pyproject.toml` 和 `uv.lock`。

## Run headless

```bash
cd ..
python api/serve.py --host 127.0.0.1 --port 8765
```

For a CPU-only control-plane smoke test (the output is synthetic and must not
be used as an inference or performance benchmark):

```bash
python api/serve.py --executor fake
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/doctor
```

The same server can be started through the stdlib-only CLI:

```bash
python tools/polykit-cli/polykit.py health
python tools/polykit-cli/polykit.py doctor
```

Default binding is loopback (`127.0.0.1`). Only bind to a public interface
behind authentication and TLS termination.

## Canonical run and workflow-definition endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Compatibility liveness check |
| GET | `/health/live` | Process liveness check |
| GET | `/health/ready` | Control-plane readiness and inference capability summary |
| GET | `/doctor` | Runtime, CUDA, model, path, and executor diagnostics |
| GET | `/model/status` | Model download / load status |
| GET | `/model/hf-download` | SSE stream of download progress |
| GET | `/model/hf-download/active` | Active download progress for Web reconnects |
| GET | `/model/downloaded` | Check weights on the server, including packs whose venv is not ready |
| GET/POST | `/settings/sources` | Read or update optional Hugging Face, PyPI, and PyTorch artifact mirrors |
| POST | `/settings/sources/test` | Probe one configured artifact source from the server |
| POST | `/node-packs/setup/{id}` | Repair an isolated node-pack environment (official packs in Web/headless mode) |
| POST | `/workflow-runs/from-image` | Start a canonical image-to-3D run |
| POST | `/workflow-runs/execute` | Submit a compiled workflow DAG |
| GET | `/workflow-runs` | List recent persisted runs |
| GET | `/workflow-runs/{run_id}` | Poll one run |
| POST | `/workflow-runs/{run_id}/cancel` | Cancel one run |
| GET | `/workflow-definitions` | List saved editable workflow graphs |
| PUT | `/workflow-definitions/{workflow_id}` | Create or replace one editable workflow graph |
| DELETE | `/workflow-definitions/{workflow_id}` | Delete one editable workflow graph |
| GET | `/workspace-library/worlds/{world_id}` | Read one server-owned world plan/manifest |
| PUT | `/workspace-library/worlds/{world_id}` | Create or replace one world plan/manifest |
| POST | `/workspace-library/worlds` | Allocate a fresh server-owned World document |

`/generate/*` remains mounted only as an explicit compatibility surface for
older CLI callers. New product code must use `/workflow-runs/*`.

## Workflow definitions and execution

Editable workflow graphs are stored server-side under `WORKFLOWS_DIR` (default
`~/.polykit/workflows`). The Web client uses `/workflow-definitions/*` for
list/save/delete and performs a one-time migration from the old
`polykit-web-workflows` localStorage key.

Files are named from a SHA-256 digest of the workflow id and replaced atomically,
so workflow ids never become filesystem paths and interrupted writes do not leave
partial JSON files. On first read, the store also detects legacy `${id}.json`
files, deduplicates by embedded workflow id / `updatedAt`, writes the newest
copy to the canonical hash filename, and removes obsolete duplicates.
Legacy pre-graph `blocks` workflow JSON remains accepted as migration input; the
shared workflow store converts it to modern nodes/edges when loaded.

The Web workflow editor compiles an editable graph into a server execution
prompt. `POST /workflow-runs/execute` validates an acyclic graph of known node
types and `[node_id, output_name]` references, then executes the DAG through
`services.workflow_engine.WorkflowEngine`.

The external MCP adapter in `tools/polykit-mcp/server.py` exposes
`polykit_world_*` tools plus local image helpers. External callers can author a
World document, submit canonical WorkflowRuns, inspect execution evidence, and
attach completed workspace artifacts without introducing a second runtime or a
hosted generation dependency.

Server-owned model/process nodes share the same `RunCoordinator`, single-GPU slot,
cancellation signals, persistence, and workspace lifecycle. If a graph cannot
compile to a server-executable prompt, the missing capability belongs in
FastAPI.

## Job services

- `services/run_coordinator.py` owns shared run state, cancellation, persistence,
  TTL cleanup, and the active single-GPU slot.
- `services/image_generation.py` owns model generation, optional texture
  refinement, output naming, and the generation worker lifecycle.
- `services/workflow_store.py` owns editable workflow persistence,
  legacy filename migration, and the runtime workflow-definition directory.
- `routers/workflow_runs.py` and the legacy `routers/legacy_generation.py` are HTTP
  surfaces over runtime services rather than owners of execution state.

## Models and node packs

Model adapters are loaded dynamically from `NODE_PACKS_DIR` (default
`~/.polykit/node-packs`). Each model node pack ships a `manifest.json` plus a
`generator.py` that subclasses `services/generators.base.BaseGenerator`.
Process node packs are discovered by the server node catalog and executed by
the workflow engine. There is no hardcoded default model.

The standalone server accepts `--workflows-dir` (or `WORKFLOWS_DIR`) alongside
`--models-dir`, `--workspace-dir`, and `--node-packs-dir` so all server-owned
persistent paths can be configured explicitly. The Web client may update its
workflow directory through `/settings/paths`; servers can also keep paths
startup-configured and reject runtime path mutation.
