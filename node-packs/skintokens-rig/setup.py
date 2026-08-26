"""Set up the isolated SkinTokens provider runtime.

The setup step downloads source code only.  Model weights are downloaded by
PolyKit's Hugging Face model flow into ``MODELS_DIR`` so they remain visible to
the normal model-management UI and are never hidden inside the provider venv.
"""
from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path


PROVIDER_REVISION = "273b691d35989d71cd17ff2895fdc735097b92d1"
PROVIDER_URL = (
    "https://github.com/VAST-AI-Research/SkinTokens/archive/"
    f"{PROVIDER_REVISION}.zip"
)
REQUIREMENTS = [
    "transformers>=4.57,<6",
    "diffusers>=0.35,<1",
    "python-box",
    "einops",
    "omegaconf",
    "lightning",
    "addict",
    "fast-simplification",
    "bpy>=4.2,<5.1",
    "trimesh",
    "open3d",
    "huggingface_hub",
    "numpy>=1.26,<2",
    "gradio",
    "bottle",
    "tornado",
    "requests",
    "scipy",
    "pillow",
    "tqdm",
]


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def _uv() -> str:
    configured = os.environ.get("POLYKIT_UV")
    resolved = shutil.which(configured) if configured else shutil.which("uv")
    if configured and Path(configured).is_file():
        resolved = configured
    if not resolved:
        raise RuntimeError(
            "SkinTokens setup requires uv. Install it from "
            "https://docs.astral.sh/uv/getting-started/installation/ and retry."
        )
    return resolved


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    print("[SkinTokens setup]", " ".join(command))
    subprocess.run(command, check=True, cwd=str(cwd) if cwd else None)


def _ensure_venv(pack_dir: Path, python_exe: str) -> Path:
    venv = pack_dir / "venv"
    if not _python(venv).exists():
        _run([python_exe, "-m", "venv", str(venv)])
    version = subprocess.check_output(
        [str(_python(venv)), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        text=True,
    ).strip()
    if tuple(int(part) for part in version.split(".")[:2]) < (3, 11):
        raise RuntimeError(
            f"SkinTokens requires Python >= 3.11, but the node-pack venv uses {version}. "
            "Run PolyKit with a Python 3.11+ backend and run Setup/Repair again."
        )
    return venv


def _torch_cuda_tag(payload: dict) -> str:
    try:
        cuda_version = int(payload.get("cuda_version") or 0)
    except (TypeError, ValueError):
        cuda_version = 0
    return "cu128" if cuda_version >= 128 else "cu126"


def _install_torch(venv: Path, payload: dict) -> None:
    if str(payload.get("accelerator", "cuda")).lower() != "cuda":
        raise RuntimeError("SkinTokens Rigging requires an NVIDIA CUDA runtime")
    python = _python(venv)
    try:
        installed = subprocess.check_output(
            [str(python), "-c", "import torch; print(torch.__version__)"],
            text=True,
        ).strip()
    except Exception:
        installed = ""
    if installed.startswith("2.7.0+"):
        print(f"[SkinTokens setup] Reusing {installed}")
        return
    tag = _torch_cuda_tag(payload)
    configured_index = (
        str(payload.get("pytorch_index_url") or os.environ.get("POLYKIT_PYTORCH_INDEX_URL") or "")
        .strip()
        .rstrip("/")
    )
    index_url = configured_index.replace("{tag}", tag) if configured_index else f"https://download.pytorch.org/whl/{tag}"
    _run(
        [
            _uv(),
            "pip",
            "install",
            "--python",
            str(python),
            "torch==2.7.0",
            "torchvision==0.22.0",
            "torchaudio==2.7.0",
            "--index-url",
            index_url,
        ]
    )


def _install_requirements(venv: Path) -> None:
    _run([_uv(), "pip", "install", "--python", str(_python(venv)), *REQUIREMENTS])


def _safe_extract(data: bytes, destination: Path) -> Path:
    staging = destination.parent / f".{destination.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            target = (staging / member.filename).resolve()
            if not str(target).startswith(str(staging.resolve()) + os.sep):
                raise RuntimeError(f"Unsafe provider archive entry: {member.filename}")
        archive.extractall(staging)
    roots = [path for path in staging.iterdir() if path.is_dir()]
    if len(roots) != 1 or not (roots[0] / "demo.py").is_file():
        raise RuntimeError("SkinTokens provider archive has an unexpected layout")
    if destination.exists():
        shutil.rmtree(destination)
    roots[0].rename(destination)
    shutil.rmtree(staging, ignore_errors=True)
    return destination


def _install_provider(pack_dir: Path) -> Path:
    provider = pack_dir / "provider"
    marker = provider / ".polykit-provider-revision"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == PROVIDER_REVISION:
        return provider
    print(f"[SkinTokens setup] Downloading provider revision {PROVIDER_REVISION[:8]} …")
    with urllib.request.urlopen(PROVIDER_URL, timeout=120) as response:
        data = response.read()
    provider = _safe_extract(data, provider)
    (provider / ".polykit-provider-revision").write_text(
        PROVIDER_REVISION + "\n", encoding="utf-8"
    )
    return provider


def _replace_once(path: Path, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"Cannot patch {path}: expected one provider fragment, found {count}. "
            "Pin a compatible SkinTokens revision or update the adapter patch."
        )
    path.write_text(text.replace(old, new), encoding="utf-8")
    return True


def _patch_provider(provider: Path) -> None:
    flash_fallback = '''try:
    from flash_attn_interface import flash_attn_func # type: ignore
except Exception:
    # PyTorch SDPA keeps the provider usable on systems where flash-attn's
    # optional binary is unavailable or was built against another glibc.
    def flash_attn_func(q, k, v, *args, **kwargs):
        out = F.scaled_dot_product_attention(
            q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2)
        )
        return out.transpose(1, 2), None
'''
    flash_import = '''try:
    from flash_attn_interface import flash_attn_func # type: ignore
except Exception as e:
    from flash_attn.flash_attn_interface import flash_attn_func as _flash_attn_func
    def flash_attn_func(*args, **kwargs):
        res = _flash_attn_func(*args, **kwargs)
        return res, None
'''
    for relative in ("src/model/skin_vae_model.py", "src/model/tokenrig.py"):
        _replace_once(provider / relative, flash_import, flash_fallback)

    _replace_once(
        provider / "src/model/tokenrig.py",
        'AutoModelForCausalLM.from_config(config=llm_config, attn_implementation="flash_attention_2")',
        'AutoModelForCausalLM.from_config(config=llm_config, attn_implementation="sdpa")',
    )
    _replace_once(
        provider / "src/server/spec.py",
        '_attn_implementation="flash_attention_2"',
        '_attn_implementation="sdpa"',
    )
    _replace_once(
        provider / "src/server/spec.py",
        "BPY_PORT = 59876",
        'BPY_PORT = int(os.environ.get("SKINTOKENS_BPY_PORT", "59876"))',
    )
    _replace_once(
        provider / "src/rig_package/parser/bpy.py",
        "            bpy.ops.export_scene.gltf(filepath=filepath)",
        '''            # The source GLB can legitimately omit NORMAL. Blender otherwise
            # exports generated loop/face normals and must split the indexed mesh at
            # every discontinuity, which can turn one shared vertex into one vertex
            # per triangle corner. Rigging does not require those generated normals.
            bpy.ops.export_scene.gltf(
                filepath=filepath,
                export_normals=False,
                export_tangents=False,
            )''',
    )


def main(payload: dict) -> None:
    pack_dir = Path(payload.get("pack_dir") or payload.get("ext_dir") or Path.cwd()).resolve()
    python_exe = str(payload.get("python_exe") or sys.executable)
    pypi_index = str(payload.get("pypi_index_url") or "").strip()
    if pypi_index:
        os.environ.setdefault("UV_INDEX_URL", pypi_index)
        os.environ.setdefault("PIP_INDEX_URL", pypi_index)
    pytorch_index = str(payload.get("pytorch_index_url") or "").strip()
    if pytorch_index:
        os.environ.setdefault("POLYKIT_PYTORCH_INDEX_URL", pytorch_index)
    venv = _ensure_venv(pack_dir, python_exe)
    _install_torch(venv, payload)
    _install_requirements(venv)
    provider = _install_provider(pack_dir)
    _patch_provider(provider)
    print(f"[SkinTokens setup] Ready: {provider}")


if __name__ == "__main__":
    config = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    main(config)
