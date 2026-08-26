import os
import platform
import sys
from fastapi import APIRouter
from services.model_runtime_registry import EXECUTOR, model_runtime_registry
from services.runtime_paths import runtime_paths

router = APIRouter(tags=["health"])


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
        "inference_capable": bool(model_runtime_registry.all_status()),
    }


@router.get("/system/resources")
async def system_resources():
    """Return a demand-driven, two-second-cached snapshot of this server host."""
    from services.system_resources import system_resource_sampler

    return system_resource_sampler.snapshot()


@router.get("/doctor")
async def doctor():
    """Return actionable runtime diagnostics without triggering inference."""
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

    models = model_runtime_registry.all_status()
    active = model_runtime_registry.active_status()
    warnings = []
    if EXECUTOR == "fake":
        warnings.append("Fake executor is enabled; generated artifacts are synthetic and not inference benchmarks.")
    if not models:
        warnings.append("No model node packs are registered.")
    if EXECUTOR != "fake" and not cuda["available"]:
        warnings.append("CUDA is unavailable; real model inference is not ready on this host.")

    return {
        "ok": True,
        "ready": bool(models),
        "service": {"mode": "headless", "executor": EXECUTOR, "pid": os.getpid()},
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "torch": torch_version,
            "cuda": cuda,
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
