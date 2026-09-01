import os
import platform
import sys
from fastapi import APIRouter
from services.model_pack_subprocess import ModelPackSubprocess
from services.model_runtime_registry import EXECUTOR, model_runtime_registry
from services.runtime_paths import runtime_paths

router = APIRouter(tags=["health"])


def _host_runtime_status() -> dict:
    """Describe the API process's own Python/Torch runtime."""
    cuda = {"available": False, "device_count": 0, "devices": []}
    torch_version = None
    try:
        import torch

        torch_version = getattr(torch, "__version__", None)
        try:
            cuda["available"] = bool(torch.cuda.is_available())
            if cuda["available"]:
                cuda["device_count"] = int(torch.cuda.device_count())
                cuda["devices"] = [torch.cuda.get_device_name(i) for i in range(cuda["device_count"])]
        except Exception as exc:
            cuda["error"] = str(exc)
    except ImportError:
        cuda["error"] = "PyTorch is not installed"
    return {"python": sys.executable, "torch": torch_version, "cuda": cuda}


def _active_runtime_status() -> dict:
    """Describe the interpreter that will execute the active model node."""
    try:
        active_id = model_runtime_registry.active_status().get("id")
        generator = model_runtime_registry.get_generator(active_id)
    except (AttributeError, KeyError, ValueError, TypeError):
        return _host_runtime_status()
    if isinstance(generator, ModelPackSubprocess):
        return generator.runtime_status()
    return _host_runtime_status()


def _cuda_available() -> bool:
    """Return whether the selected model runtime can actually use CUDA.

    The health endpoints must not load a model, but they should still avoid
    claiming inference readiness when the CUDA runtime is missing. Isolated
    node packs are probed in their own interpreter; checking only the API
    process would incorrectly fail when that process intentionally has no
    heavyweight ML dependencies.
    """

    if EXECUTOR == "fake":
        return True
    return bool(_active_runtime_status().get("cuda", {}).get("available"))


def _inference_capable() -> bool:
    """Summarize whether at least one usable model runtime is available."""

    models = model_runtime_registry.all_status()
    if not models:
        return False
    if EXECUTOR == "fake":
        return True
    if not _cuda_available():
        return False
    active = model_runtime_registry.active_status()
    return bool(active.get("downloaded"))


@router.get("/health")
async def health():
    """Cheap liveness check that does not load a model or require CUDA."""
    return {
        "status": "ok",
        "service": "polykit-api",
        "mode": "headless",
    }


@router.get("/health/live")
async def health_live():
    return await health()


@router.get("/health/ready")
async def health_ready():
    """Control-plane readiness; model/GPU capability is reported separately."""
    paths_ready = runtime_paths.models.is_dir() and runtime_paths.workspace.is_dir()
    return {
        "status": "ready" if paths_ready else "starting",
        "service": "polykit-api",
        "mode": "headless",
        "control_plane": paths_ready,
        "inference_capable": _inference_capable(),
    }


@router.get("/system/resources")
async def system_resources():
    """Return a demand-driven, two-second-cached snapshot of this server host."""
    from services.system_resources import system_resource_sampler

    return system_resource_sampler.snapshot()


@router.get("/doctor")
async def doctor():
    """Return actionable runtime diagnostics without triggering inference."""
    host_runtime = _host_runtime_status()
    active_runtime = _active_runtime_status()
    cuda = host_runtime["cuda"]
    active_cuda = active_runtime.get("cuda", {})

    models = model_runtime_registry.all_status()
    active = model_runtime_registry.active_status()
    warnings = []
    if EXECUTOR == "fake":
        warnings.append("Fake executor is enabled; generated artifacts are synthetic and not inference benchmarks.")
    if not models:
        warnings.append("No model node packs are registered.")
    if EXECUTOR != "fake" and not active_cuda.get("available"):
        warnings.append("CUDA is unavailable; real model inference is not ready on this host.")

    inference_capable = (
        bool(models)
        and (EXECUTOR == "fake" or (bool(active_cuda.get("available")) and bool(active.get("downloaded"))))
    )

    return {
        "ok": True,
        "ready": bool(models),
        "inference_capable": inference_capable,
        "service": {"mode": "headless", "executor": EXECUTOR, "pid": os.getpid()},
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "torch": host_runtime["torch"],
            "cuda": cuda,
            "active_model_runtime": active_runtime,
        },
        "paths": {
            "models": str(runtime_paths.models),
            "workspace": str(runtime_paths.workspace),
            "node-packs": str(runtime_paths.node_packs),
        },
        "models": models,
        "active_model": active,
        "warnings": warnings,
    }
