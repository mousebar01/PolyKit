"""Set up the isolated native Diffusers runtime for Anima.

This script installs Python dependencies only. Model weights stay in the
central PolyKit models directory and are downloaded explicitly by the Models
page, so setup never silently consumes tens of gigabytes or a Hub token.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


PACK_ROOT = Path(__file__).resolve().parent


def _payload() -> dict:
    if len(sys.argv) < 2:
        return {}
    value = json.loads(sys.argv[1].strip("'\""))
    return value if isinstance(value, dict) else {}


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def _uv() -> str:
    configured = os.environ.get("POLYKIT_UV")
    resolved = shutil.which(configured) if configured else shutil.which("uv")
    if configured and Path(configured).is_file():
        resolved = configured
    if not resolved:
        raise RuntimeError(
            "Anima setup requires uv. Install it from "
            "https://docs.astral.sh/uv/getting-started/installation/ and retry."
        )
    return resolved


def _run(command: list[str]) -> None:
    print("[Anima setup]", " ".join(command))
    subprocess.run(command, check=True, cwd=str(PACK_ROOT))


def _install_torch(venv: Path, payload: dict) -> None:
    python = _python(venv)
    try:
        installed = subprocess.check_output(
            [str(python), "-c", "import torch; print(torch.__version__)"],
            text=True,
        ).strip()
    except Exception:
        installed = ""
    if installed:
        print(f"[Anima setup] Reusing PyTorch {installed}")
        return

    accelerator = str(payload.get("accelerator") or "cuda").lower()
    index = str(
        payload.get("pytorch_index_url")
        or os.environ.get("POLYKIT_PYTORCH_INDEX_URL")
        or ""
    ).strip()
    if not index and accelerator == "cuda":
        cuda_version = int(payload.get("cuda_version") or 0)
        index = "https://download.pytorch.org/whl/cu128" if cuda_version >= 128 else "https://download.pytorch.org/whl/cu126"
    if "{tag}" in index:
        cuda_version = int(payload.get("cuda_version") or 0)
        index = index.replace("{tag}", "cu128" if cuda_version >= 128 else "cu126")
    command = [_uv(), "pip", "install", "--python", str(python), "torch>=2.6,<2.8"]
    if index:
        command.extend(["--index-url", index])
    _run(command)


def setup(payload: dict) -> None:
    python_exe = str(payload.get("python_exe") or sys.executable)
    venv = PACK_ROOT / "venv"
    if not _python(venv).exists():
        _run([python_exe, "-m", "venv", str(venv)])

    _install_torch(venv, payload)
    manifest = json.loads((PACK_ROOT / "manifest.json").read_text(encoding="utf-8"))
    requirements = manifest.get("requirements") or []
    _run([_uv(), "pip", "install", "--python", str(_python(venv)), *requirements])
    print(f"[Anima setup] Runtime ready at {venv}")


if __name__ == "__main__":
    setup(_payload())
