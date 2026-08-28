import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

from services.model_runtime_registry import model_runtime_registry
from services.node_pack_inventory import get_pack, is_official, iter_installed_packs
from services.runtime_paths import runtime_paths
from services.runtime_settings import get_download_sources

router = APIRouter(tags=["node-packs"])
_setup_locks: dict[str, asyncio.Lock] = {}


def _source_node_payload(node: dict, source: dict) -> dict:
    return {
        "id": str(node.get("id", "")),
        "name": node.get("name") or node.get("id", ""),
        "i18n": node.get("i18n", {}),
        "input": node.get("input", "image"),
        "inputs": node.get("inputs"),
        "inputLabels": node.get("input_labels"),
        "output": node.get("output", "mesh"),
        "paramsSchema": node.get("params_schema", source.get("params_schema", [])),
        "hfRepo": node.get("hf_repo", ""),
        "downloadCheck": node.get("download_check", ""),
        "hfSkipPrefixes": node.get("hf_skip_prefixes", []),
        "hfIncludePrefixes": node.get("hf_include_prefixes", []),
    }


def _source_pack_payload(pack_id: str, source: dict, *, official: bool) -> dict:
    nodes = source.get("nodes") if isinstance(source.get("nodes"), list) else []
    pack_type = str(source.get("type") or "model")
    payload = {
        "type": pack_type,
        "id": pack_id,
        "name": source.get("name") or pack_id,
        "version": source.get("version", ""),
        "description": source.get("description", ""),
        "author": source.get("author", ""),
        "source": source.get("source", ""),
        "trusted": official,
        "builtin": official,
        "env": source.get("env", "shared"),
        "requirements": source.get("requirements", []),
        "download": source.get("download", {}),
        "models": source.get("models", []),
        "i18n": source.get("i18n", {}),
        "nodes": [
            _source_node_payload(node, source)
            for node in nodes
            if isinstance(node, dict) and node.get("id")
        ],
    }
    if pack_type == "process":
        payload["entry"] = source.get("entry", "")
    return payload


@router.get("/list")
async def list_node_packs():
    """Return manifest inventory enriched with available runtime metadata."""
    grouped: dict[str, dict] = {}
    source_cache: dict[str, tuple[Path, dict]] = {}

    for pack_dir, source in iter_installed_packs():
        source_type = str(source.get("type") or "model")
        if source_type not in {"model", "process"} or not source.get("id"):
            continue
        pack_id = str(source.get("id") or pack_dir.name)
        source_cache[pack_id] = (pack_dir, source)
        grouped[pack_id] = _source_pack_payload(
            pack_id,
            source,
            official=is_official(pack_dir),
        )

    def source_for(pack_id: str) -> tuple[Path, dict]:
        if pack_id not in source_cache:
            installed = get_pack(pack_id)
            source_cache[pack_id] = installed or (runtime_paths.node_packs / pack_id, {})
        return source_cache[pack_id]

    for full_id, manifest in model_runtime_registry.manifests().items():
        pack_id = str(manifest.get("pack_id") or full_id.split("/", 1)[0])
        node_id = str(
            manifest.get("node_id")
            or (full_id.split("/", 1)[1] if "/" in full_id else full_id)
        )
        pack_dir, source = source_for(pack_id)
        source_nodes = source.get("nodes") if isinstance(source.get("nodes"), list) else []
        source_node = next(
            (
                node
                for node in source_nodes
                if isinstance(node, dict) and str(node.get("id")) == node_id
            ),
            {},
        )

        official = is_official(pack_dir)
        node_pack = grouped.setdefault(
            pack_id,
            _source_pack_payload(pack_id, source, official=official),
        )
        node_pack.update({
            "name": source.get("name") or manifest.get("pack_name") or pack_id,
            "version": manifest.get("version", source.get("version", "")),
            "description": source.get("description") or manifest.get("description", ""),
            "author": manifest.get("author", source.get("author", "")),
            "source": manifest.get("source", source.get("source", "")),
            "trusted": official,
            "builtin": official,
            "env": manifest.get("env", source.get("env", "shared")),
            "requirements": manifest.get("requirements", source.get("requirements", [])),
            "download": source.get("download", manifest.get("download", {})),
            "models": source.get("models", []),
            "i18n": source.get("i18n", {}),
        })

        runtime_node = {
            "id": node_id,
            "name": source_node.get("name") or manifest.get("name", node_id),
            "i18n": source_node.get("i18n", {}),
            "input": manifest.get("input", "image"),
            "inputs": manifest.get("inputs"),
            "inputLabels": manifest.get("input_labels"),
            "output": manifest.get("output", "mesh"),
            "paramsSchema": manifest.get("params_schema", []),
            "hfRepo": manifest.get("hf_repo", ""),
            "downloadCheck": manifest.get("download_check", ""),
            "hfSkipPrefixes": manifest.get("hf_skip_prefixes", []),
            "hfIncludePrefixes": manifest.get("hf_include_prefixes", []),
        }
        nodes = node_pack["nodes"]
        index = next((i for i, node in enumerate(nodes) if node.get("id") == node_id), None)
        if index is None:
            nodes.append(runtime_node)
        else:
            nodes[index] = runtime_node

    errors = model_runtime_registry.load_errors()
    for pack_id, pack in grouped.items():
        matching = [
            message
            for model_id, message in errors.items()
            if model_id == pack_id or model_id.startswith(f"{pack_id}/")
        ]
        if matching:
            pack["loadError"] = "\n".join(matching)

    return list(grouped.values())


@router.post("/reload")
async def reload_node_packs():
    try:
        model_runtime_registry.reload()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "reloaded": True,
        "models": model_runtime_registry.model_ids(),
        "errors": model_runtime_registry.load_errors(),
    }


@router.post("/setup/{pack_id}")
async def setup_node_pack(pack_id: str):
    if (
        not pack_id
        or pack_id in {".", ".."}
        or Path(pack_id).name != pack_id
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", pack_id)
    ):
        raise HTTPException(400, "Invalid node pack id")

    node_packs_dir = runtime_paths.node_packs
    pack_dir = node_packs_dir / pack_id
    setup_py = pack_dir / "setup.py"
    if not pack_dir.exists():
        raise HTTPException(404, f"Node pack '{pack_id}' not found in {node_packs_dir}")
    if not setup_py.exists():
        return {"status": "skipped", "reason": "no setup.py"}
    if model_runtime_registry.has_active_generation():
        raise HTTPException(409, "Cannot repair a node pack while a generation is running")

    # A browser can drive this endpoint on a remote server, but setup executes
    # arbitrary code and must remain opt-in for third-party packs. Bundled
    # official packs are already trusted and can be repaired from the Web UI.
    if (
        os.environ.get("POLYKIT_HEADLESS") == "1"
        and not is_official(pack_dir)
        and os.environ.get("POLYKIT_ALLOW_NODE_PACK_SETUP") != "1"
    ):
        raise HTTPException(
            403,
            "Remote setup is limited to official node packs. "
            "Set POLYKIT_ALLOW_NODE_PACK_SETUP=1 only after reviewing third-party code.",
        )

    lock = _setup_locks.setdefault(pack_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, f"Node pack '{pack_id}' setup is already running")

    async with lock:
        try:
            manifest = json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            manifest = {}

        gpu_sm = _detect_gpu_sm()
        accelerator = "cuda" if gpu_sm > 0 else ("mps" if sys.platform == "darwin" else "cpu")
        setup_python = _select_setup_python(manifest)
        sources = get_download_sources()
        payload = {
            "python_exe": str(setup_python),
            "pack_dir": str(pack_dir),
            "ext_dir": str(pack_dir),
            "gpu_sm": gpu_sm,
            "cuda_version": _detect_cuda_version(),
            "accelerator": accelerator,
            "platform": sys.platform,
            "arch": platform.machine(),
        }
        payload.update({
            "pypi_index_url": sources.pypi_index_url,
            "pytorch_index_url": sources.pytorch_index_url,
        })

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                [str(setup_python), str(setup_py), json.dumps(payload)],
                capture_output=True,
                text=True,
                cwd=str(pack_dir),
            ),
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise HTTPException(500, f"setup.py failed:\n{details[-4000:]}")

        return {
            "status": "ok",
            "gpu_sm": gpu_sm,
            "python_exe": str(setup_python),
            "output": result.stdout,
        }


def _version_tuple(raw: object) -> tuple[int, int]:
    match = re.search(r"(\d+)\.(\d+)", str(raw or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _python_version(candidate: str) -> tuple[int, int]:
    try:
        result = subprocess.run(
            [candidate, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return (0, 0)
    return _version_tuple(result.stdout)


def _select_setup_python(manifest: dict) -> Path:
    """Choose a host Python capable of creating the pack's isolated venv."""
    required = _version_tuple(manifest.get("python_min", "3.10"))
    root = Path(__file__).resolve().parents[2]
    configured = os.environ.get("POLYKIT_SETUP_PYTHON")
    candidates = [
        configured,
        sys.executable,
        str(root / ".venv" / "bin" / "python"),
        str(root / ".venv" / "Scripts" / "python.exe"),
        shutil.which("python3.12"),
        shutil.which("python3.11"),
        shutil.which("python3"),
    ]
    seen: set[str] = set()
    for raw in candidates:
        if not raw:
            continue
        candidate = str(raw)
        if candidate in seen:
            continue
        seen.add(candidate)
        if _python_version(candidate) >= required:
            return Path(candidate)
    raise RuntimeError(
        f"Node pack setup requires Python >= {required[0]}.{required[1]}. "
        "Set POLYKIT_SETUP_PYTHON to a compatible interpreter and retry."
    )


@router.get("/errors")
async def node_pack_errors():
    return model_runtime_registry.load_errors()


def _detect_gpu_sm() -> int:
    try:
        import torch

        if torch.cuda.is_available():
            major, minor = torch.cuda.get_device_capability(0)
            return major * 10 + minor
    except Exception:
        pass
    return 0


def _detect_cuda_version() -> int:
    try:
        import torch

        version = str(getattr(torch.version, "cuda", "") or "")
        match = re.match(r"^(\d+)\.(\d+)", version)
        if match:
            return int(match.group(1)) * 10 + int(match.group(2))
    except Exception:
        pass
    return 0
