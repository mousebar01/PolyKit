import os
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.model_runtime_registry import model_runtime_registry
from services.run_coordinator import run_coordinator
from services.runtime_paths import runtime_paths
from services.runtime_settings import (
    DownloadSourceConfig,
    AgentSettings,
    AgentThinkingLevel,
    AgentToolProfile,
    ProxyConfig,
    get_download_sources,
    get_proxy as get_proxy_config,
    set_download_sources,
    set_proxy as set_proxy_config,
    url_opener,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class PathsUpdate(BaseModel):
    models_dir: Optional[str] = None
    workspace_dir: Optional[str] = None
    workflows_dir: Optional[str] = None
    node_packs_dir: Optional[str] = None


class TokenUpdate(BaseModel):
    token: str


class AgentSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    thinking_level: Optional[AgentThinkingLevel] = None
    tool_profile: Optional[AgentToolProfile] = None


@router.get("/agent")
async def get_agent_settings_route():
    from services.runtime_settings import get_agent_settings

    settings = get_agent_settings()
    return {
        **settings.to_dict(),
        "session_dir": str(runtime_paths.data / "agent" / "sessions"),
    }


@router.post("/agent")
async def update_agent_settings(body: AgentSettingsUpdate):
    from services.runtime_settings import get_agent_settings, set_agent_settings

    current = get_agent_settings()
    settings = set_agent_settings(AgentSettings(
        enabled=current.enabled if body.enabled is None else body.enabled,
        default_provider=current.default_provider if body.default_provider is None else body.default_provider,
        default_model=current.default_model if body.default_model is None else body.default_model,
        thinking_level=current.thinking_level if body.thinking_level is None else body.thinking_level,
        tool_profile=current.tool_profile if body.tool_profile is None else body.tool_profile,
    ))
    return {
        **settings.to_dict(),
        "session_dir": str(runtime_paths.data / "agent" / "sessions"),
    }


@router.get("/paths")
async def get_paths():
    paths = runtime_paths.snapshot()
    return {
        "models_dir": str(paths.models),
        "workspace_dir": str(paths.workspace),
        "workflows_dir": str(paths.workflows),
        "node_packs_dir": str(paths.node_packs),
    }


@router.post("/paths")
async def update_paths(body: PathsUpdate):
    if os.environ.get("POLYKIT_HEADLESS") == "1":
        raise HTTPException(409, "Headless paths are configured at startup and cannot be changed at runtime")
    try:
        workspace_changed = bool(body.workspace_dir)
        if body.models_dir or body.workspace_dir or body.node_packs_dir:
            model_runtime_registry.update_paths(
                models_dir=Path(body.models_dir) if body.models_dir else None,
                workspace_dir=Path(body.workspace_dir) if body.workspace_dir else None,
                node_packs_dir=Path(body.node_packs_dir) if body.node_packs_dir else None,
            )
        if body.workflows_dir:
            runtime_paths.update(workflows_dir=Path(body.workflows_dir))
        if workspace_changed:
            # The default run database is workspace-scoped. Rebind persistence
            # after the workspace root changes so state never remains attached
            # to the previous workspace.
            run_coordinator.reconfigure_store()
            # The Agent sidecar captures its workspace boundary at startup;
            # restart it lazily so the next conversation uses the new root.
            from services.agent_runtime import agent_runtime

            await agent_runtime.stop()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(400, f"Could not configure storage path: {exc}") from exc

    paths = runtime_paths.snapshot()
    return {
        "models_dir": str(paths.models),
        "workspace_dir": str(paths.workspace),
        "workflows_dir": str(paths.workflows),
        "node_packs_dir": str(paths.node_packs),
    }


@router.post("/hf-token")
async def update_hf_token(body: TokenUpdate):
    """Update the HuggingFace token inherited by future child processes."""
    if os.environ.get("POLYKIT_HEADLESS") == "1":
        raise HTTPException(403, "Set HF_TOKEN before starting the headless server")
    if body.token:
        os.environ["HUGGING_FACE_HUB_TOKEN"] = body.token
        os.environ["HF_TOKEN"] = body.token
    else:
        os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
        os.environ.pop("HF_TOKEN", None)
    return {"ok": True}


class ProxyUpdate(BaseModel):
    enabled: bool = False
    url: str = ""
    username: str = ""
    password: str = ""
    bypass: str = ""


_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}


def _validate_proxy_url(raw: str) -> None:
    parsed = urlparse(raw.strip())
    if parsed.scheme.lower() not in _PROXY_SCHEMES or not parsed.hostname:
        raise HTTPException(
            400,
            "Proxy address must look like http://host:port, https://host:port or socks5://host:port",
        )
    if parsed.port is None:
        raise HTTPException(400, "Proxy address must include a port (e.g. http://127.0.0.1:7890)")


@router.get("/proxy")
async def get_proxy():
    return get_proxy_config().to_dict()


@router.post("/proxy")
async def update_proxy(body: ProxyUpdate):
    if body.enabled:
        _validate_proxy_url(body.url)
    proxy = ProxyConfig(
        enabled=body.enabled,
        url=body.url,
        username=body.username,
        password=body.password,
        bypass=body.bypass,
    )
    set_proxy_config(proxy)
    # The embedded Agent captures proxy environment variables at process
    # start. Restart it lazily after a proxy edit so the next OAuth/API call
    # uses the setting just saved instead of a stale dispatcher snapshot.
    from services.agent_runtime import agent_runtime

    await agent_runtime.stop()
    return proxy.to_dict()


class DownloadSourcesUpdate(BaseModel):
    huggingface_endpoint: str = ""
    pypi_index_url: str = ""
    pytorch_index_url: str = ""


_SOURCE_FIELDS = (
    ("huggingface_endpoint", "Hugging Face endpoint"),
    ("pypi_index_url", "Python package index"),
    ("pytorch_index_url", "PyTorch index"),
)


def _validate_source_url(raw: str, label: str) -> None:
    value = raw.strip()
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, f"{label} must be an http(s) URL")


@router.get("/sources")
async def get_sources():
    return get_download_sources().to_dict()


@router.post("/sources")
async def update_sources(body: DownloadSourcesUpdate):
    values = {
        "huggingface_endpoint": body.huggingface_endpoint.strip(),
        "pypi_index_url": body.pypi_index_url.strip(),
        "pytorch_index_url": body.pytorch_index_url.strip(),
    }
    for field, label in _SOURCE_FIELDS:
        _validate_source_url(values[field], label)
    sources = DownloadSourceConfig(**values)
    set_download_sources(sources)
    return sources.to_dict()


def _source_probe_url(kind: str, sources: DownloadSourceConfig) -> str:
    if kind == "huggingface":
        return f"{sources.huggingface_endpoint or 'https://huggingface.co'}/api/models?limit=1"
    if kind == "pypi":
        base = sources.pypi_index_url or "https://pypi.org/simple/"
        return urljoin(base.rstrip("/") + "/", "fastapi/")
    if kind == "pytorch":
        base = (sources.pytorch_index_url or "https://download.pytorch.org/whl/{tag}/").replace("{tag}", "cu126")
        return urljoin(base.rstrip("/") + "/", "torch/")
    raise HTTPException(400, f"Unknown source kind: {kind}")


class SourceTestRequest(BaseModel):
    kind: Literal["huggingface", "pypi", "pytorch"] = "huggingface"


@router.post("/sources/test")
async def test_source(body: SourceTestRequest):
    sources = get_download_sources()
    url = _source_probe_url(body.kind, sources)

    import asyncio
    from urllib.request import Request

    def _probe() -> str:
        try:
            with url_opener().open(
                Request(url, headers={"User-Agent": "polykit"}),
                timeout=10,
            ) as response:
                response.read(1024)
            return ""
        except Exception as exc:
            return str(exc)

    loop = asyncio.get_running_loop()
    error = await loop.run_in_executor(None, _probe)
    return {"kind": body.kind, "url": url, "ok": not error, "error": error}


@router.post("/proxy/test")
async def test_proxy():
    import asyncio
    from urllib.request import Request

    def _probe() -> str:
        try:
            with url_opener().open(
                Request(
                    "https://huggingface.co/api/models",
                    headers={"User-Agent": "polykit"},
                ),
                timeout=10,
            ) as response:
                response.read(1024)
            return ""
        except Exception as exc:
            return str(exc)

    loop = asyncio.get_running_loop()
    error = await loop.run_in_executor(None, _probe)
    return {"ok": not error, "error": error}
