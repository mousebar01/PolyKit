"""Poly Haven asset discovery and explicit server-side imports.

Poly Haven is an external source, not a second workspace library.  Discovery
is read-only and uses the public API; importing a selected model downloads the
declared glTF bundle, verifies it, normalizes it to one workspace GLB, and
records attribution/provenance in a sidecar.  No World or WorkflowRun state is
owned here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request

from services.asset_names import output_name
from services.runtime_paths import runtime_paths
from services.runtime_settings import url_opener


POLYHAVEN_API_BASE = "https://api.polyhaven.com"
POLYHAVEN_USER_AGENT = "PolyKit/asset-library (+https://github.com/XliuXjianX/PolyKit)"
POLYHAVEN_LICENSE_URL = "https://polyhaven.com/license"
_API_HOSTS = {"api.polyhaven.com"}
_DOWNLOAD_HOSTS = {"dl.polyhaven.org", "cdn.polyhaven.com"}
_RESOLUTIONS = {"1k", "2k", "4k", "8k"}
_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")
_MAX_FILES = 64
_MAX_IMPORT_BYTES = int(os.environ.get("POLYKIT_POLYHAVEN_MAX_BYTES", str(512 * 1024 * 1024)))
_CACHE_TTL_SECONDS = 6 * 60 * 60
_MAX_JSON_BYTES = 32 * 1024 * 1024


class PolyHavenError(ValueError):
    """A user-facing provider, validation, or import error."""


@dataclass(frozen=True)
class _FileSpec:
    relative_path: str
    url: str
    size: int
    md5: str


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(str(value).replace("_", " ").replace("-", " "))}


def _safe_asset_id(asset_id: str) -> str:
    value = str(asset_id or "").strip()
    if not _ASSET_ID_RE.fullmatch(value):
        raise PolyHavenError("Poly Haven asset_id contains unsupported characters.")
    return value


def _safe_relative_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    path = Path(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or ":" in raw:
        raise PolyHavenError("Poly Haven file manifest contains an unsafe relative path.")
    return path.as_posix()


def _validate_url(value: str, hosts: set[str]) -> str:
    parsed = urlparse(str(value or ""))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in hosts or parsed.username or parsed.password:
        raise PolyHavenError("Poly Haven returned a URL outside the provider allowlist.")
    return value


def _request_json(path: str) -> Any:
    url = f"{POLYHAVEN_API_BASE}/{path.lstrip('/')}"
    _validate_url(url, _API_HOSTS)
    request = Request(url, headers={"User-Agent": POLYHAVEN_USER_AGENT, "Accept": "application/json"})
    try:
        with url_opener().open(request, timeout=60) as response:
            final_url = response.geturl()
            _validate_url(final_url, _API_HOSTS)
            body = response.read(_MAX_JSON_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise PolyHavenError(f"Poly Haven API request failed: {exc}") from exc
    if len(body) > _MAX_JSON_BYTES:
        raise PolyHavenError("Poly Haven API response is too large.")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolyHavenError("Poly Haven API returned invalid JSON.") from exc
    if not isinstance(value, (dict, list)):
        raise PolyHavenError("Poly Haven API returned an unexpected response.")
    return value


def _cache_path() -> Path:
    return runtime_paths.data / "asset-sources" / "polyhaven-models.json"


def _load_model_index(*, refresh: bool = False) -> dict[str, Mapping[str, Any]]:
    cache = _cache_path()
    if not refresh:
        try:
            value = json.loads(cache.read_text(encoding="utf-8"))
            if (
                isinstance(value, Mapping)
                and isinstance(value.get("assets"), Mapping)
                and float(value.get("fetched_at", 0)) > datetime.now(timezone.utc).timestamp() - _CACHE_TTL_SECONDS
            ):
                return {str(key): item for key, item in value["assets"].items() if isinstance(item, Mapping)}
        except (OSError, ValueError, TypeError):
            pass

    value = _request_json("/assets?type=models")
    if not isinstance(value, Mapping):
        raise PolyHavenError("Poly Haven model index has an unexpected shape.")
    assets: dict[str, Mapping[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(item, Mapping):
            continue
        try:
            is_model = int(item.get("type", 2)) == 2
        except (TypeError, ValueError):
            is_model = False
        if is_model:
            assets[str(key)] = item
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_name(cache.name + ".tmp")
    temporary.write_text(
        json.dumps({"fetched_at": datetime.now(timezone.utc).timestamp(), "assets": assets}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(cache)
    return assets


def _result(asset_id: str, metadata: Mapping[str, Any], score: float) -> dict[str, Any]:
    name = str(metadata.get("name") or asset_id)
    tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
    category = metadata.get("category")
    return {
        "asset_id": asset_id,
        "display_name": name,
        "description": str(metadata.get("description") or ""),
        "category": category,
        "tags": [str(tag) for tag in tags],
        "attributes": metadata.get("attributes") if isinstance(metadata.get("attributes"), Mapping) else {},
        "thumbnail_url": metadata.get("thumbnail_url"),
        "source_url": f"https://polyhaven.com/a/{quote(asset_id, safe='')}",
        "provider": "polyhaven",
        "license": "CC0",
        "license_url": POLYHAVEN_LICENSE_URL,
        "score": round(score, 3),
        "download_count": metadata.get("download_count"),
        "polycount": metadata.get("polycount"),
        "max_resolution": metadata.get("max_resolution"),
    }


def search_models(query: str, *, category: str | None = None, limit: int = 5, refresh: bool = False) -> list[dict[str, Any]]:
    """Search Poly Haven's model metadata without downloading or mutating files."""

    text = str(query or "").strip()
    if not text:
        raise PolyHavenError("query is required")
    query_tokens = _tokens(text)
    category_tokens = _tokens(category or "")
    results: list[dict[str, Any]] = []
    for asset_id, metadata in _load_model_index(refresh=refresh).items():
        name = str(metadata.get("name") or asset_id)
        tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
        categories = metadata.get("categories") if isinstance(metadata.get("categories"), list) else []
        category_value = str(metadata.get("category") or "")
        if category_tokens and not (category_tokens & (_tokens(category_value) | _tokens(" ".join(map(str, categories))))):
            continue
        score = 0.0
        score += len(query_tokens & _tokens(name)) * 5.0
        score += len(query_tokens & _tokens(asset_id)) * 4.0
        score += len(query_tokens & _tokens(" ".join(map(str, tags)))) * 4.0
        score += len(query_tokens & _tokens(category_value)) * 2.0
        score += len(query_tokens & _tokens(str(metadata.get("description") or "")))
        if text.casefold() in {asset_id.casefold(), name.casefold()}:
            score += 8.0
        if score > 0:
            results.append(_result(asset_id, metadata, score))
    results.sort(key=lambda item: (-float(item["score"]), str(item["asset_id"])))
    return results[: max(1, min(int(limit), 50))]


def _collect_file_specs(node: Mapping[str, Any], *, prefix: str = "", result: list[_FileSpec] | None = None) -> list[_FileSpec]:
    result = result if result is not None else []
    for key, value in node.items():
        if not isinstance(value, Mapping):
            continue
        path = _safe_relative_path(f"{prefix}/{key}" if prefix else str(key))
        if value.get("url"):
            url = _validate_url(str(value["url"]), _DOWNLOAD_HOSTS)
            try:
                size = int(value.get("size", 0))
            except (TypeError, ValueError) as exc:
                raise PolyHavenError(f"Poly Haven file has an invalid size: {path}") from exc
            if size < 0:
                raise PolyHavenError(f"Poly Haven file has an invalid size: {path}")
            result.append(_FileSpec(path, url, size, str(value.get("md5") or "").lower()))
        include = value.get("include")
        if isinstance(include, Mapping):
            _collect_file_specs(include, prefix=prefix, result=result)
    return result


def _model_file_specs(files: Mapping[str, Any], resolution: str) -> list[_FileSpec]:
    gltf = files.get("gltf")
    if not isinstance(gltf, Mapping) or not isinstance(gltf.get(resolution), Mapping):
        raise PolyHavenError(f"Poly Haven model has no glTF download at {resolution}.")
    variant = gltf[resolution]
    root = variant.get("gltf") if isinstance(variant, Mapping) else None
    if not isinstance(root, Mapping) or not root.get("url"):
        raise PolyHavenError(f"Poly Haven model has no glTF download at {resolution}.")
    root_url = _validate_url(str(root["url"]), _DOWNLOAD_HOSTS)
    root_name = Path(urlparse(root_url).path).name
    if not root_name:
        raise PolyHavenError("Poly Haven glTF root has no filename.")
    try:
        root_size = int(root.get("size", 0))
    except (TypeError, ValueError) as exc:
        raise PolyHavenError("Poly Haven glTF root has an invalid size.") from exc
    specs = [_FileSpec(_safe_relative_path(root_name), root_url, root_size, str(root.get("md5") or "").lower())]
    include = root.get("include")
    if isinstance(include, Mapping):
        specs.extend(_collect_file_specs(include))
    unique: dict[str, _FileSpec] = {}
    for spec in specs:
        if spec.relative_path in unique and unique[spec.relative_path] != spec:
            raise PolyHavenError(f"Poly Haven manifest contains duplicate file: {spec.relative_path}")
        unique[spec.relative_path] = spec
    if len(unique) > _MAX_FILES:
        raise PolyHavenError("Poly Haven model contains too many files to import.")
    total = sum(spec.size for spec in unique.values())
    if total > _MAX_IMPORT_BYTES:
        raise PolyHavenError("Poly Haven model exceeds the configured import size limit.")
    return list(unique.values())


def _download_file(spec: _FileSpec, destination: Path) -> None:
    request = Request(spec.url, headers={"User-Agent": POLYHAVEN_USER_AGENT, "Accept": "*/*"})
    digest = hashlib.md5()
    size = 0
    temporary = destination.with_name(destination.name + ".part")
    try:
        with url_opener().open(request, timeout=120) as response:
            _validate_url(response.geturl(), _DOWNLOAD_HOSTS)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > _MAX_IMPORT_BYTES:
                        raise PolyHavenError("Poly Haven download exceeded the import size limit.")
                    digest.update(chunk)
                    output.write(chunk)
    except (HTTPError, URLError, TimeoutError, OSError, PolyHavenError) as exc:
        temporary.unlink(missing_ok=True)
        raise PolyHavenError(f"Poly Haven download failed for {spec.relative_path}: {exc}") from exc
    if spec.size and size != spec.size:
        temporary.unlink(missing_ok=True)
        raise PolyHavenError(f"Poly Haven size verification failed for {spec.relative_path}.")
    if spec.md5 and digest.hexdigest().lower() != spec.md5:
        temporary.unlink(missing_ok=True)
        raise PolyHavenError(f"Poly Haven checksum verification failed for {spec.relative_path}.")
    temporary.replace(destination)


def _validate_gltf_bundle(root: Path) -> None:
    try:
        document = json.loads(root.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolyHavenError("Downloaded Poly Haven glTF is invalid JSON.") from exc
    if not isinstance(document, Mapping):
        raise PolyHavenError("Downloaded Poly Haven glTF has an invalid document.")
    references: list[str] = []
    for key in ("buffers", "images"):
        values = document.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, Mapping) and isinstance(item.get("uri"), str) and not item["uri"].startswith("data:"):
                references.append(item["uri"])
    for reference in references:
        if reference.startswith(("http:", "https:", "//")):
            raise PolyHavenError("Poly Haven glTF references a remote URL; only bundled files are allowed.")
        relative = _safe_relative_path(reference.split("#", 1)[0])
        candidate = (root.parent / relative).resolve()
        if root.parent.resolve() not in candidate.parents and candidate != root.parent.resolve():
            raise PolyHavenError("Poly Haven glTF references a file outside its bundle.")
        if not candidate.is_file():
            raise PolyHavenError(f"Poly Haven glTF is missing bundled file: {relative}")


def _sidecar_path(output: Path) -> Path:
    # Match the workspace library's established ``<stem>.asset.json``
    # sidecar convention so delete/rename operations move it with the mesh.
    return output.with_name(f"{output.stem}.asset.json")


def _load_existing_sidecar(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _find_existing_import(
    output_dir: Path,
    *,
    asset_id: str,
    resolution: str,
    files_hash: Any,
) -> tuple[Path, Mapping[str, Any]] | None:
    """Find a previously imported provider asset without relying on its name.

    Output names are intentionally unique, so re-imports must use provenance
    sidecars for idempotency instead of reconstructing a deterministic path.
    """

    if not output_dir.is_dir():
        return None
    for sidecar in sorted(output_dir.glob("*.asset.json")):
        metadata = _load_existing_sidecar(sidecar)
        if not metadata:
            continue
        if metadata.get("asset_id") != asset_id or metadata.get("resolution") != resolution:
            continue
        if files_hash is not None and metadata.get("files_hash") != files_hash:
            continue
        stem = sidecar.name.removesuffix(".asset.json")
        candidate = output_dir / f"{stem}.glb"
        if candidate.is_file():
            return candidate, metadata
    return None


def import_model(asset_id: str, *, resolution: str = "2k", workspace: Path | None = None) -> dict[str, Any]:
    """Download and publish one selected Poly Haven model as a workspace GLB."""

    asset_id = _safe_asset_id(asset_id)
    resolution = str(resolution or "2k").lower()
    if resolution not in _RESOLUTIONS:
        raise PolyHavenError("resolution must be one of 1k, 2k, 4k, or 8k")
    info = _request_json(f"/info/{quote(asset_id, safe='')}")
    try:
        info_type = int(info.get("type", 2)) if isinstance(info, Mapping) else -1
    except (TypeError, ValueError):
        info_type = -1
    if not isinstance(info, Mapping) or info_type != 2:
        raise PolyHavenError(f"Poly Haven asset is not a model: {asset_id}")
    files_value = _request_json(f"/files/{quote(asset_id, safe='')}")
    if not isinstance(files_value, Mapping):
        raise PolyHavenError("Poly Haven file manifest has an unexpected shape.")
    specs = _model_file_specs(files_value, resolution)

    root_workspace = workspace or runtime_paths.workspace
    staging = root_workspace / "Workflows" / ".external" / "polyhaven" / asset_id / resolution
    for spec in specs:
        destination = staging / spec.relative_path
        if destination.is_file() and (not spec.size or destination.stat().st_size == spec.size):
            if spec.md5:
                digest = hashlib.md5(destination.read_bytes()).hexdigest().lower()
                if digest != spec.md5:
                    _download_file(spec, destination)
            continue
        _download_file(spec, destination)

    root_spec = specs[0]
    root = staging / root_spec.relative_path
    _validate_gltf_bundle(root)

    output_dir = root_workspace / "Workflows" / "PolyHaven"
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_import = _find_existing_import(
        output_dir,
        asset_id=asset_id,
        resolution=resolution,
        files_hash=info.get("files_hash"),
    )
    if existing_import is not None:
        output, existing = existing_import
        return {
            "provider": "polyhaven",
            "asset_id": asset_id,
            "display_name": str(info.get("name") or asset_id),
            "workspace_path": output.relative_to(root_workspace).as_posix(),
            "resolution": resolution,
            "format": "glb",
            "source_format": "gltf",
            "source_url": f"https://polyhaven.com/a/{quote(asset_id, safe='')}",
            "license": "CC0",
            "files_hash": info.get("files_hash"),
            "reused": True,
        }

    output = output_dir / output_name(asset_id, tag=resolution, ext=".glb")
    sidecar = _sidecar_path(output)

    try:
        import trimesh
        scene = trimesh.load(str(root), force="scene")
        with tempfile.NamedTemporaryFile(suffix=".glb", dir=output_dir, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            scene.export(str(temporary_path), file_type="glb")
            temporary_path.replace(output)
        finally:
            temporary_path.unlink(missing_ok=True)
    except Exception as exc:
        raise PolyHavenError(f"Poly Haven glTF normalization failed: {exc}") from exc

    metadata = {
        "asset_id": asset_id,
        "name": str(info.get("name") or asset_id),
        "description": str(info.get("description") or ""),
        "aliases": [asset_id, str(info.get("name") or asset_id), *(str(tag) for tag in info.get("tags", []) if isinstance(tag, str))],
        "category": info.get("category"),
        "tags": [str(tag) for tag in info.get("tags", []) if isinstance(tag, str)],
        "source": "polyhaven",
        "provider": "polyhaven",
        "source_url": f"https://polyhaven.com/a/{quote(asset_id, safe='')}",
        "license": "CC0",
        "license_url": POLYHAVEN_LICENSE_URL,
        "resolution": resolution,
        "format": "glb",
        "source_format": "gltf",
        "files_hash": info.get("files_hash"),
        "source_files": [{"path": spec.relative_path, "size": spec.size, "md5": spec.md5} for spec in specs],
        "thumbnail_url": info.get("thumbnail_url"),
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary_sidecar = sidecar.with_name(sidecar.name + ".tmp")
    temporary_sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_sidecar.replace(sidecar)
    return {
        "provider": "polyhaven",
        "asset_id": asset_id,
        "display_name": metadata["name"],
        "workspace_path": output.relative_to(root_workspace).as_posix(),
        "resolution": resolution,
        "format": "glb",
        "source_format": "gltf",
        "source_url": metadata["source_url"],
        "license": "CC0",
        "files_hash": metadata["files_hash"],
        "reused": False,
    }


__all__ = ["PolyHavenError", "search_models", "import_model"]
