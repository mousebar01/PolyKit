import asyncio
import json
import time
import os
import socket
import threading
import re
import shutil
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import StreamingResponse
from services.model_runtime_registry import model_runtime_registry
from services.runtime_paths import runtime_paths
from services.runtime_settings import get_download_sources, url_opener

router = APIRouter(tags=["model"])


class DownloadPaused(Exception):
    pass


class DownloadCancelled(Exception):
    pass


_download_controls: dict[str, dict[str, threading.Event]] = {}
_download_progress: dict[str, dict[str, object]] = {}
_download_lock = threading.Lock()


def _safe_model_path(model_id: str) -> Path:
    """Resolve a model id without allowing deletion outside MODELS_DIR."""
    value = str(model_id or "").strip()
    parts = Path(value).parts
    if (
        not value
        or not parts
        or any(part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
    ):
        raise HTTPException(400, "Invalid model id")
    root = runtime_paths.models.resolve()
    candidate = root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise HTTPException(400, "Model directory must not contain symlinks")
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Invalid model id")
    return candidate


def _download_model_id(model_id: str) -> str:
    """Map a node execution id to its shared weight resource id.

    A manifest may expose several executable nodes backed by one
    ``download.location`` (for example ``trellis2/generate`` and
    ``trellis2/refine`` both use ``trellis2``). Keep workflow/runtime ids
    distinct while making all weight-management endpoints address the shared
    directory and download lock.
    """
    try:
        manifest = model_runtime_registry.get_manifest(model_id)
    except (KeyError, ValueError):
        return model_id
    location = (manifest.get("download") or {}).get("location")
    if isinstance(location, str) and location.strip():
        return location.strip()
    return model_id


def _download_control(model_id: str) -> Optional[dict[str, threading.Event]]:
    """Return the current control for pause/cancel endpoints, if active."""
    with _download_lock:
        return _download_controls.get(model_id)


def _new_download_control(model_id: str) -> dict[str, threading.Event]:
    """Create a fresh control for a new download session."""
    with _download_lock:
        if model_id in _download_controls:
            raise HTTPException(409, "Download already in progress")
        control: dict[str, threading.Event] = {
            "pause": threading.Event(),
            "cancel": threading.Event(),
        }
        _download_controls[model_id] = control
        _download_progress[model_id] = {
            "modelId": model_id,
            "percent": 0,
            "status": "Starting download...",
        }
        return control


def _check_download_control(control: dict[str, threading.Event]) -> None:
    if control["cancel"].is_set():
        raise DownloadCancelled()
    if control["pause"].is_set():
        raise DownloadPaused()


def _huggingface_endpoint() -> str | None:
    """Return the configured Hub endpoint for this request.

    ``huggingface_hub`` snapshots ``HF_ENDPOINT`` when its module is first
    imported.  The Web settings can change the mirror while the API process
    is already running, so relying on that module-level value can silently
    send a download back to the unreachable public endpoint.  Resolve the
    persisted setting at request time and pass it explicitly to the Hub API
    and URL builder instead.
    """
    return get_download_sources().huggingface_endpoint or os.environ.get("HF_ENDPOINT") or None


@router.get("/status")
async def model_status():
    """Status of the active model."""
    return model_runtime_registry.active_status()


@router.get("/all")
async def all_models_status():
    """Status of all known models (downloaded, loaded, required VRAM)."""
    return model_runtime_registry.all_status()


@router.get("/params")
async def model_params(model_id: Optional[str] = None):
    """Parameter schema of the active model (or a specified model)."""
    try:
        return model_runtime_registry.params_schema(model_id)
    except KeyError:
        raise HTTPException(404, f"Unknown model ID: {model_id}")


@router.post("/switch")
async def switch_model(model_id: str):
    """Switch the active model."""
    try:
        model_runtime_registry.switch_model(model_id)
        return {"active": model_id}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@router.post("/unload-all")
async def unload_all_models():
    """Unloads all models from memory to free VRAM/RAM."""
    try:
        model_runtime_registry.unload_all()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    # Force Python to release memory back to the OS
    import gc
    gc.collect()
    try:
        import ctypes, sys
        if sys.platform == "win32":
            k32 = ctypes.windll.kernel32
            k32.SetProcessWorkingSetSizeEx(k32.GetCurrentProcess(), -1, -1, 0)
    except Exception:
        pass
    return {"unloaded": True}


@router.post("/unload/{model_id}")
async def unload_model(model_id: str):
    """Unloads a model from memory so its files can be safely deleted."""
    if model_runtime_registry.has_active_generation():
        raise HTTPException(409, "Cannot unload models while a generation is running")
    try:
        gen = model_runtime_registry.get_generator(model_id)
        gen.unload()
        return {"unloaded": True}
    except ValueError:
        return {"unloaded": True}  # already not loaded, that's fine


@router.post("/delete")
async def delete_model(model_id: str = Form(...)):
    """Unload and remove one downloaded model's exact directory."""
    if model_runtime_registry.has_active_generation():
        raise HTTPException(409, "Cannot delete model weights while a generation is running")

    download_id = _download_model_id(model_id)
    model_path = _safe_model_path(download_id)
    # Weight management must still work while an isolated node pack is being
    # repaired. The registry intentionally omits generators whose venv is
    # missing, but their model directory is still safe to remove.
    generator_ids = [
        candidate
        for candidate in model_runtime_registry.model_ids()
        if candidate == model_id or _download_model_id(candidate) == download_id
    ]

    try:
        for generator_id in generator_ids:
            model_runtime_registry.get_generator(generator_id).unload()
        if model_path.exists():
            if not model_path.is_dir():
                raise HTTPException(400, "Model path is not a directory")
            shutil.rmtree(model_path)
        return {"deleted": True, "model_id": model_id}
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(500, f"Could not delete model weights: {exc}")


@router.post("/hf-download/pause")
async def pause_hf_download(model_id: Optional[str] = None, form_model_id: Optional[str] = Form(None, alias="model_id")):
    resolved_model_id = form_model_id or model_id
    if not resolved_model_id:
        raise HTTPException(422, "model_id is required")
    control = _download_control(_download_model_id(resolved_model_id))
    if control is not None:
        control["pause"].set()
    return {"paused": control is not None}


@router.post("/hf-download/cancel")
async def cancel_hf_download(model_id: Optional[str] = None, form_model_id: Optional[str] = Form(None, alias="model_id")):
    resolved_model_id = form_model_id or model_id
    if not resolved_model_id:
        raise HTTPException(422, "model_id is required")
    control = _download_control(_download_model_id(resolved_model_id))
    if control is not None:
        control["cancel"].set()
    return {"cancelled": control is not None}


@router.get("/hf-download/active")
async def active_hf_downloads():
    """Return active model downloads so a refreshed Web client can reconnect."""
    with _download_lock:
        return [dict(value) for value in _download_progress.values()]


@router.get("/downloaded")
async def model_downloaded(model_id: str, download_check: Optional[str] = None):
    """Check downloaded weights directly on the server filesystem.

    This endpoint intentionally does not depend on a successfully instantiated
    generator. An isolated node pack can have valid weights while its venv is
    still being repaired, and the Web UI must still be able to show that state.
    """
    download_id = _download_model_id(model_id)
    model_path = _safe_model_path(download_id)
    check = download_check
    if not check:
        try:
            check = model_runtime_registry.get_manifest(model_id).get("download_check")
        except KeyError:
            check = None

    if check:
        relative = Path(str(check))
        if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
            raise HTTPException(400, "Invalid download check path")
        candidate = (model_path / relative).resolve()
        try:
            candidate.relative_to(model_path.resolve())
        except ValueError as exc:
            raise HTTPException(400, "Invalid download check path") from exc
        downloaded = candidate.is_file()
    else:
        downloaded = model_path.is_dir() and any(model_path.iterdir())

    return {"model_id": model_id, "downloaded": downloaded}


@router.get("/hf-download")
async def hf_download(
    repo_id: str,
    model_id: str,
    skip_prefixes: Optional[str] = None,
    include_prefixes: Optional[str] = None,
    token: Optional[str] = None,
):
    """
    Streams a HuggingFace Hub model download via SSE.
    Downloads into the manifest's shared weight location (or
    MODELS_DIR / model_id when no location is declared), applying the
    filtering declared in the node pack manifest.

    skip_prefixes:    JSON-encoded list of path prefixes to exclude.
    include_prefixes: JSON-encoded list of path prefixes to include (whitelist).
    token:            HuggingFace access token for gated repos (from server settings).
    All three fall back to the node pack's manifest / environment when not supplied.

    SSE format: data: {"percent": 0-100, "file": "...", "status": "..."}
    """
    import json as _json
    import os
    download_id = _download_model_id(model_id)
    dest_dir = str(_safe_model_path(download_id))
    # Prefer skip_prefixes passed directly from the client (authoritative, no registry dep)
    if skip_prefixes:
        try:
            skip_list = _json.loads(skip_prefixes)
        except Exception:
            skip_list = []
    else:
        try:
            skip_list = model_runtime_registry.get_manifest(model_id).get("hf_skip_prefixes", [])
        except KeyError:
            skip_list = []

    if include_prefixes:
        try:
            include_list = _json.loads(include_prefixes)
        except Exception:
            include_list = []
    else:
        try:
            include_list = model_runtime_registry.get_manifest(model_id).get("hf_include_prefixes", [])
        except KeyError:
            include_list = []

    # Token: explicit query param > env var > None
    hf_token = token or os.environ.get("HUGGING_FACE_HUB_TOKEN") or os.environ.get("HF_TOKEN") or None
    control = _new_download_control(download_id)

    async def stream():
        loop = asyncio.get_running_loop()

        def _fmt(data: dict) -> str:
            terminal = bool(
                data.get("error")
                or data.get("paused")
                or data.get("cancelled")
                or data.get("status") == "done"
            )
            with _download_lock:
                if terminal:
                    _download_progress.pop(download_id, None)
                else:
                    _download_progress[download_id] = {
                        "modelId": download_id,
                        **data,
                    }
            return f"data: {json.dumps(data)}\n\n"

        try:
            yield _fmt({"percent": 0, "status": "Listing repository files..."})
            _check_download_control(control)

            hf_endpoint = _huggingface_endpoint()

            def _list_files():
                from huggingface_hub import HfApi
                api = HfApi(endpoint=hf_endpoint, token=hf_token)
                return [
                    f for f in api.list_repo_files(repo_id)
                    if (not include_list or any(f.startswith(p) for p in include_list))
                    if not any(f.startswith(p) for p in skip_list)
                ]

            files = await loop.run_in_executor(None, _list_files)
            total = len(files)

            if total == 0:
                yield _fmt({"error": f"No files found in HuggingFace repo: {repo_id}"})
                return

            yield _fmt({"percent": 1, "status": f"Downloading {total} files..."})

            from huggingface_hub import hf_hub_url

            for i, filename in enumerate(files):
                _check_download_control(control)
                yield _fmt({
                    "percent": 1 + round(i / total * 94),
                    "file": filename,
                    "fileIndex": i + 1,
                    "totalFiles": total,
                    "status": f"Starting {filename}",
                    "bytesDownloaded": 0,
                    "stalledSeconds": 0,
                })

                base_pct = 1 + round(i / total * 94)
                queue: asyncio.Queue[dict] = asyncio.Queue()

                def _progress(msg: dict) -> None:
                    loop.call_soon_threadsafe(queue.put_nowait, msg)

                url = hf_hub_url(repo_id=repo_id, filename=filename, endpoint=hf_endpoint)
                dl_future = loop.run_in_executor(
                    None,
                    lambda: _download_file_streamed(
                        url=url,
                        filename=filename,
                        dest_dir=dest_dir,
                        file_index=i + 1,
                        total_files=total,
                        base_percent=base_pct,
                        progress_cb=_progress,
                        control=control,
                        token=hf_token,
                    ),
                )

                while not dl_future.done():
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    else:
                        yield _fmt(msg)

                final_size = await dl_future
                _check_download_control(control)

                # Reserve 1-95 for file downloads, leave 95-100 for finalisation
                pct = 1 + round((i + 1) / total * 94)
                yield _fmt({
                    "percent": pct,
                    "file": filename,
                    "fileIndex": i + 1,
                    "totalFiles": total,
                    "status": "Downloaded",
                    "bytesDownloaded": final_size,
                    "stalledSeconds": 0,
                })

            yield _fmt({"percent": 100, "status": "done"})

        except DownloadPaused:
            yield _fmt({"paused": True, "status": "paused"})
        except DownloadCancelled:
            # Remove only partial files; completed files are preserved so the
            # next download can resume from where it left off.
            for part in Path(dest_dir).rglob("*.part"):
                part.unlink(missing_ok=True)
            yield _fmt({"cancelled": True, "status": "cancelled"})
        except Exception as exc:
            yield _fmt({"error": str(exc)})
        finally:
            # Only remove the control if it still belongs to this session.
            with _download_lock:
                if _download_controls.get(download_id) is control:
                    _download_controls.pop(download_id, None)
                    _download_progress.pop(download_id, None)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _download_file_streamed(
    *,
    url: str,
    filename: str,
    dest_dir: str,
    file_index: int,
    total_files: int,
    base_percent: int,
    progress_cb,
    control: dict[str, threading.Event],
    token: Optional[str] = None,
) -> int:
    root_path = Path(dest_dir).resolve()
    final_path = (root_path / filename).resolve()
    try:
        final_path.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"Invalid model file path: {filename}") from exc
    temp_path = final_path.with_suffix(final_path.suffix + ".part")
    final_path.parent.mkdir(parents=True, exist_ok=True)

    if final_path.exists():
        return final_path.stat().st_size

    # Explicit token (from caller) > env vars > none
    hf_token = (
        token
        or os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_HUB_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    )
    headers = {"User-Agent": "polykit/0.3.1"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    retries = 3
    backoff = 2.0
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            _check_download_control(control)
            existing_bytes = temp_path.stat().st_size if temp_path.exists() else 0
            request_headers = dict(headers)
            request_url = url
            if existing_bytes > 0:
                request_url = _resolve_direct_download_url(url, headers)
                request_headers["Range"] = f"bytes={existing_bytes}-"

            request = Request(request_url, headers=request_headers)
            with url_opener().open(request, timeout=30) as response:
                resumed = existing_bytes > 0 and getattr(response, "status", None) == 206
                if existing_bytes > 0 and not resumed:
                    temp_path.unlink(missing_ok=True)
                    existing_bytes = 0

                total_bytes = _response_total_bytes(response.headers, existing_bytes if resumed else 0)
                bytes_downloaded = existing_bytes
                last_emit = 0.0
                chunk_size = 1024 * 1024
                mode = "ab" if resumed else "wb"

                progress_cb({
                    "percent": base_percent,
                    "file": filename,
                    "fileIndex": file_index,
                    "totalFiles": total_files,
                    "status": _download_status(bytes_downloaded, total_bytes, attempt, retries, resumed=resumed),
                    "bytesDownloaded": bytes_downloaded,
                    "totalBytes": total_bytes,
                    "stalledSeconds": 0,
                })

                with open(temp_path, mode) as out:
                    while True:
                        _check_download_control(control)
                        try:
                            chunk = response.read(chunk_size)
                        except socket.timeout as exc:
                            raise TimeoutError(f"Timed out while downloading {filename}") from exc

                        if not chunk:
                            break

                        out.write(chunk)
                        bytes_downloaded += len(chunk)

                        now = time.monotonic()
                        if now - last_emit >= 0.5:
                            progress_cb({
                                "percent": base_percent,
                                "file": filename,
                                "fileIndex": file_index,
                                "totalFiles": total_files,
                                "status": _download_status(bytes_downloaded, total_bytes, attempt, retries, resumed=resumed),
                                "bytesDownloaded": bytes_downloaded,
                                "totalBytes": total_bytes,
                                "stalledSeconds": 0,
                            })
                            last_emit = now

            temp_path.replace(final_path)
            return bytes_downloaded

        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            preserved_bytes = temp_path.stat().st_size if temp_path.exists() else 0
            progress_cb({
                "percent": base_percent,
                "file": filename,
                "fileIndex": file_index,
                "totalFiles": total_files,
                "status": f"Retrying after error ({attempt}/{retries})…",
                "bytesDownloaded": preserved_bytes,
                "stalledSeconds": 0,
            })
            if attempt >= retries:
                break
            time.sleep(backoff)
            backoff *= 2

    raise RuntimeError(f"Failed to download {filename}: {last_error}")


def _resolve_direct_download_url(url: str, headers: dict[str, str]) -> str:
    # HEAD request: follows redirects to get the final CDN URL without downloading the body.
    request = Request(url, headers=headers, method="HEAD")
    with url_opener().open(request, timeout=30) as response:
        return response.geturl()


def _parse_content_length(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _download_status(downloaded: int, total: Optional[int], attempt: int, retries: int, resumed: bool = False) -> str:
    prefix = "Resuming…" if resumed and downloaded > 0 else "Downloading…"
    if total and total > 0:
        pct = min(100, round(downloaded / total * 100))
        return f"{prefix} {pct}%"
    if retries > 1 and attempt > 1:
        return f"{prefix} retry {attempt}/{retries}"
    return prefix


def _response_total_bytes(headers, already_downloaded: int) -> Optional[int]:
    content_range = headers.get("Content-Range")
    if content_range and "/" in content_range:
        total_raw = content_range.split("/")[-1].strip()
        try:
            return int(total_raw)
        except (TypeError, ValueError):
            pass

    content_length = _parse_content_length(headers.get("Content-Length"))
    if content_length is None:
        return None
    return already_downloaded + content_length
