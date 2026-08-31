"""Server-owned runtime settings — a durable JSON store next to the data dir.

PolyKit is Web-first: FastAPI owns durable product state. Settings that must
survive server restarts and be visible to every outbound download path live
here rather than only in browser localStorage.

The proxy is materialised into this process's environment as the standard
``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``ALL_PROXY`` / ``NO_PROXY`` variables —
the single mechanism already honoured by every stack PolyKit downloads
through (urllib and huggingface_hub for model weights, uv and git inside node
pack ``setup.py``, and every child process that inherits ``os.environ``).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote, urlparse, urlunsplit

_HOME = Path.home() / ".polykit"
DATA_DIR = Path(os.environ.get("POLYKIT_DATA_DIR", str(_HOME)))
SETTINGS_FILE = DATA_DIR / "settings.json"

_LOCK = threading.Lock()

# Hosts that must never be routed through the proxy (the app's own server).
_BYPASS_DEFAULTS = ("localhost", "127.0.0.1", "::1")

_PROXY_ENV_KEYS = (
    "http_proxy", "https_proxy", "all_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
)
_NO_PROXY_ENV_KEYS = ("no_proxy", "NO_PROXY")

# Optional artifact mirrors. Empty values keep the upstream defaults. These
# are deliberately separate from proxy settings: a mirror changes *which*
# service endpoint is used, while a proxy changes the network route.
_SOURCE_ENV_KEYS = (
    "HF_ENDPOINT",
    "UV_INDEX_URL",
    "PIP_INDEX_URL",
    "POLYKIT_PYTORCH_INDEX_URL",
)


class ProxyConfig:
    """A proxy configuration as edited in Settings."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        url: str = "",
        username: str = "",
        password: str = "",
        bypass: str = "",
    ) -> None:
        self.enabled = bool(enabled)
        self.url = (url or "").strip()
        self.username = username or ""
        self.password = password or ""
        self.bypass = (bypass or "").strip()

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "ProxyConfig":
        data = data or {}
        return cls(
            enabled=bool(data.get("enabled", False)),
            url=str(data.get("url", "") or ""),
            username=str(data.get("username", "") or ""),
            password=str(data.get("password", "") or ""),
            bypass=str(data.get("bypass", "") or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled,
            "url": self.url,
            "username": self.username,
            "password": self.password,
            "bypass": self.bypass,
        }

    @property
    def effective_url(self) -> str:
        """Proxy URL with credentials embedded (``http://user:pass@host:port``)."""
        if not self.url:
            return ""
        if not self.username and not self.password:
            return self.url
        parsed = urlparse(self.url)
        host = parsed.hostname or ""
        if not host:
            return self.url
        netloc = host
        if parsed.port is not None:
            netloc += f":{parsed.port}"
        if self.username:
            user = quote(self.username, safe="")
            if self.password:
                netloc = f"{user}:{quote(self.password, safe='')}@{netloc}"
            else:
                netloc = f"{user}@{netloc}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


class DownloadSourceConfig:
    """Optional mirrors for the artifact ecosystems PolyKit downloads from."""

    def __init__(
        self,
        *,
        huggingface_endpoint: str = "",
        pypi_index_url: str = "",
        pytorch_index_url: str = "",
    ) -> None:
        self.huggingface_endpoint = (huggingface_endpoint or "").strip().rstrip("/")
        self.pypi_index_url = (pypi_index_url or "").strip().rstrip("/")
        self.pytorch_index_url = (pytorch_index_url or "").strip().rstrip("/")

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "DownloadSourceConfig":
        data = data or {}
        return cls(
            huggingface_endpoint=str(data.get("huggingface_endpoint", "") or ""),
            pypi_index_url=str(data.get("pypi_index_url", "") or ""),
            pytorch_index_url=str(data.get("pytorch_index_url", "") or ""),
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "huggingface_endpoint": self.huggingface_endpoint,
            "pypi_index_url": self.pypi_index_url,
            "pytorch_index_url": self.pytorch_index_url,
        }


def _read() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # The settings file may contain HF tokens or proxy credentials. Keep both
    # the temporary and final files private.
    os.chmod(tmp, 0o600)
    tmp.replace(SETTINGS_FILE)
    os.chmod(SETTINGS_FILE, 0o600)


def load_settings() -> dict:
    """Read the whole settings file ({} when missing or corrupt)."""
    with _LOCK:
        return _read()


def save_settings(patch: dict) -> dict:
    """Merge ``patch`` into the settings file atomically and return the result."""
    with _LOCK:
        next_data = {**_read(), **patch}
        # Silently drop the retired embedded-agent configuration when any
        # setting is saved so old installations converge to the current schema.
        next_data.pop("agent", None)
        _write(next_data)
        return next_data


def _bypass_value(bypass: str) -> str:
    parts = [
        part.strip()
        for part in (*_BYPASS_DEFAULTS, *bypass.replace("\n", ",").split(","))
        if part.strip()
    ]
    return ",".join(dict.fromkeys(parts))


def apply_proxy_env(proxy: ProxyConfig) -> None:
    """Materialise the proxy into this process (and every child process)."""
    for key in (*_PROXY_ENV_KEYS, *_NO_PROXY_ENV_KEYS):
        os.environ.pop(key, None)
    if not (proxy.enabled and proxy.effective_url):
        return

    scheme = urlparse(proxy.effective_url).scheme.lower()
    if scheme in {"http", "https"}:
        os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = proxy.effective_url
        os.environ["https_proxy"] = os.environ["HTTPS_PROXY"] = proxy.effective_url
    else:
        os.environ["all_proxy"] = os.environ["ALL_PROXY"] = proxy.effective_url

    bypass = _bypass_value(proxy.bypass)
    if bypass:
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = bypass


def get_proxy() -> ProxyConfig:
    return ProxyConfig.from_dict(load_settings().get("proxy"))


def set_proxy(proxy: ProxyConfig) -> ProxyConfig:
    """Persist the proxy and apply it to the running process immediately."""
    save_settings({"proxy": proxy.to_dict()})
    apply_proxy_env(proxy)
    return proxy


def get_download_sources() -> DownloadSourceConfig:
    return DownloadSourceConfig.from_dict(load_settings().get("sources"))


def apply_download_sources(sources: DownloadSourceConfig) -> None:
    """Apply mirror endpoints to this process and inherited child processes."""
    for key in _SOURCE_ENV_KEYS:
        os.environ.pop(key, None)
    if sources.huggingface_endpoint:
        os.environ["HF_ENDPOINT"] = sources.huggingface_endpoint
    if sources.pypi_index_url:
        os.environ["UV_INDEX_URL"] = sources.pypi_index_url
        os.environ["PIP_INDEX_URL"] = sources.pypi_index_url
    if sources.pytorch_index_url:
        os.environ["POLYKIT_PYTORCH_INDEX_URL"] = sources.pytorch_index_url


def set_download_sources(sources: DownloadSourceConfig) -> DownloadSourceConfig:
    """Persist mirror endpoints and apply them without restarting the server."""
    save_settings({"sources": sources.to_dict()})
    apply_download_sources(sources)
    return sources


def apply_persisted_proxy() -> None:
    """Apply the saved proxy at server start.

    Explicit proxy env vars visible at boot (set by the operator or the
    launcher script) take precedence over the saved setting.
    """
    already_configured = any(
        os.environ.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    )
    if already_configured:
        for upper, lower in (
            ("HTTP_PROXY", "http_proxy"),
            ("HTTPS_PROXY", "https_proxy"),
            ("ALL_PROXY", "all_proxy"),
        ):
            value = os.environ.get(upper) or os.environ.get(lower)
            if value:
                os.environ[upper] = value
                os.environ[lower] = value
        return
    proxy = get_proxy()
    if proxy.enabled and proxy.effective_url:
        apply_proxy_env(proxy)


def apply_persisted_download_sources() -> None:
    """Apply saved mirrors unless the operator supplied that source via env."""
    sources = get_download_sources()
    if sources.huggingface_endpoint and not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = sources.huggingface_endpoint
    if sources.pypi_index_url and not (
        os.environ.get("UV_INDEX_URL") or os.environ.get("PIP_INDEX_URL")
    ):
        os.environ["UV_INDEX_URL"] = sources.pypi_index_url
        os.environ["PIP_INDEX_URL"] = sources.pypi_index_url
    if sources.pytorch_index_url and not os.environ.get("POLYKIT_PYTORCH_INDEX_URL"):
        os.environ["POLYKIT_PYTORCH_INDEX_URL"] = sources.pytorch_index_url


def url_opener():
    """Build a fresh urllib opener honouring the *current* proxy env."""
    from urllib.request import ProxyHandler, build_opener

    return build_opener(ProxyHandler())
