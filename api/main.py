"""PolyKit's standalone API and Web UI host.

The FastAPI process is the product core. A browser or CLI can use the API
directly. When ``dist-web`` (or ``POLYKIT_WEB_DIR``) exists, this process also
serves the built Web frontend.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routers import agent_skills, export, legacy_generation, model, node_packs, node_types, optimize, production_recipes, settings, status, workflow_runs, workflow_store, workspace_library, workspace_worlds, world_artifacts
from services.runtime_paths import runtime_paths


@asynccontextmanager
async def lifespan(app: FastAPI):
    from services.model_runtime_registry import model_runtime_registry
    from services.runtime_settings import apply_persisted_download_sources, apply_persisted_proxy

    apply_persisted_proxy()
    apply_persisted_download_sources()
    model_runtime_registry.initialize()
    yield
    model_runtime_registry.unload_all(allow_during_generation=True)


class _StatusFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        return (
            "/workflow-runs/" not in message
            and "/generate/status/" not in message
            and "/system/resources" not in message
        )


logging.getLogger("uvicorn.access").addFilter(_StatusFilter())


app = FastAPI(
    title="PolyKit API",
    description="Headless PolyKit control plane for image-to-3D generation and workspace artifacts.",
    lifespan=lifespan,
)

_headless = os.environ.get("POLYKIT_HEADLESS", "0") == "1"
_cors_origins = [
    origin.strip()
    for origin in os.environ.get("POLYKIT_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if not _cors_origins:
    _cors_origins = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ] if _headless else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length"],
)

app.include_router(status.router)
app.include_router(settings.router)
app.include_router(model.router, prefix="/model")
app.include_router(legacy_generation.router, prefix="/generate")
app.include_router(optimize.router, prefix="/optimize")
app.include_router(node_packs.router, prefix="/node-packs")
app.include_router(export.router, prefix="/export")
app.include_router(workflow_runs.router, prefix="/workflow-runs")
app.include_router(workflow_store.router)
app.include_router(workspace_library.router)
app.include_router(workspace_worlds.router)
app.include_router(production_recipes.router)
app.include_router(agent_skills.router)
app.include_router(world_artifacts.router)
app.include_router(node_types.router)


@app.api_route("/workspace/{full_path:path}", methods=["GET", "HEAD"])
async def serve_workspace_file(full_path: str):
    from services.workspace_paths import resolve_workspace_path

    try:
        file_path = resolve_workspace_path(runtime_paths.workspace, full_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workspace path")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path))


_web_dist = Path(
    os.environ.get("POLYKIT_WEB_DIR")
    or Path(__file__).resolve().parents[1] / "dist-web"
).resolve()
if _web_dist.is_dir() and (_web_dist / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")
