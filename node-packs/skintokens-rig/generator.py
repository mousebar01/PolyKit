"""PolyKit adapter for VAST-AI-Research/SkinTokens.

SkinTokens is kept as an external provider under ``provider/``.  This module
owns the boundary between the provider and PolyKit: model/checkpoint paths,
the Blender transfer service, output naming, cancellation checks and lifecycle
cleanup.  It intentionally exposes one first-stage contract only:
``mesh -> mesh`` (a rigged GLB).
"""
from __future__ import annotations

import atexit
import gc
import importlib.util
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator, Mapping

try:
    from services.generators.base import BaseGenerator, GenerationCancelled
except ImportError:  # pragma: no cover - only useful for standalone inspection
    class BaseGenerator:  # type: ignore[no-redef]
        def __init__(self, model_dir: Path, outputs_dir: Path) -> None:
            self.model_dir = Path(model_dir)
            self.outputs_dir = Path(outputs_dir)
            self._model = None
            self._params_schema: list = []

        def _check_cancelled(self, cancel_event: Any) -> None:
            if cancel_event and cancel_event.is_set():
                raise GenerationCancelled()

    class GenerationCancelled(Exception):
        pass

try:
    from services.gltf_skin import has_skin_metadata
except ImportError:  # pragma: no cover - standalone source inspection
    def has_skin_metadata(_path: str | Path) -> bool:
        return False


PACK_ROOT = Path(__file__).resolve().parent
PROVIDER_ROOT = Path(
    os.environ.get("SKINTOKENS_PROVIDER_ROOT", str(PACK_ROOT / "provider"))
).resolve()
PROVIDER_REVISION = "273b691d35989d71cd17ff2895fdc735097b92d1"
CHECKPOINT = "experiments/articulation_xl_quantization_256_token_4/grpo_1400.ckpt"
SKIN_VAE_CHECKPOINT = "experiments/skin_vae_2_10_32768/last.ckpt"
QWEN_ROOT = "models/Qwen3-0.6B"
QWEN_REPO = "Qwen/Qwen3-0.6B"
QWEN_ALLOW_PATTERNS = [
    "config.json",
    "generation_config.json",
    "merges.txt",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
]
DEFAULT_BPY_PORT = 59876
MIN_FREE_VRAM_GB = 14
GIB = 1024 * 1024 * 1024


def _check_cuda_capacity(required_gb: int = MIN_FREE_VRAM_GB) -> None:
    """Fail before model load when this node cannot satisfy its GPU contract."""
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "SkinTokens Rigging requires the isolated CUDA PyTorch runtime. "
            "Run Setup/Repair for the node pack."
        ) from exc

    if not torch.cuda.is_available():
        raise RuntimeError(
            "SkinTokens Rigging requires an NVIDIA CUDA GPU with at least "
            f"{required_gb} GB of free VRAM."
        )

    try:
        free_bytes, _total_bytes = torch.cuda.mem_get_info()
    except Exception as exc:
        raise RuntimeError(
            "SkinTokens Rigging could not query free CUDA memory; "
            "ensure the NVIDIA driver is available and retry."
        ) from exc

    if int(free_bytes) < required_gb * GIB:
        free_gb = int(free_bytes) / GIB
        raise RuntimeError(
            "SkinTokens Rigging needs about "
            f"{required_gb} GB of free VRAM, but only {free_gb:.1f} GB is available. "
            "Unload another model or use a larger NVIDIA GPU."
        )


def _provider_revision() -> str:
    try:
        return (PROVIDER_ROOT / ".polykit-provider-revision").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return ""


def _load_provider_demo() -> ModuleType:
    entry = PROVIDER_ROOT / "demo.py"
    if not entry.is_file():
        raise RuntimeError(
            "SkinTokens runtime is not set up. Open Models, select "
            "SkinTokens Rigging, and run Setup/Repair first."
        )

    installed_revision = _provider_revision()
    if installed_revision != PROVIDER_REVISION:
        raise RuntimeError(
            "SkinTokens provider revision is out of date "
            f"(installed {installed_revision[:8] or 'unknown'}, expected {PROVIDER_REVISION[:8]}). "
            "Run Setup/Repair from Models before loading the node."
        )

    for path in (PROVIDER_ROOT, PROVIDER_ROOT / "src"):
        value = str(path)
        if path.is_dir() and value not in sys.path:
            sys.path.insert(0, value)

    module_name = f"polykit_skintokens_demo_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load SkinTokens provider at {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _port() -> int:
    try:
        return int(os.environ.get("SKINTOKENS_BPY_PORT", str(DEFAULT_BPY_PORT)))
    except ValueError:
        return DEFAULT_BPY_PORT


def _ping_bpy(port: int, timeout: float = 1.0) -> bool:
    try:
        # Do not inherit an external HTTP proxy for this local health check.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/ping", timeout=timeout) as response:
            return response.read().decode("utf-8").strip() == "pong"
    except (OSError, urllib.error.URLError):
        return False


def _terminate_process(proc: subprocess.Popen[object] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def _disable_local_proxy() -> None:
    """Keep requests to the local Blender service off any external proxy."""
    for key in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(key, "")
        entries = [item for item in current.split(",") if item]
        for host in ("localhost", "127.0.0.1"):
            if host not in entries:
                entries.append(host)
        os.environ[key] = ",".join(entries)


def _download_qwen_metadata_raw(target: Path) -> None:
    """Download Qwen's small metadata files through the endpoint's raw route.

    Some Hugging Face mirrors serve ``/resolve`` through a redirect to the
    canonical host.  That redirect is useful for browsers but makes
    ``huggingface_hub``'s HEAD/ETag validation fail behind a proxy.  The raw
    route is supported by both huggingface.co and the common mirrors, and it
    still uses the configured HTTP(S)_PROXY environment variables.
    """
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    target.mkdir(parents=True, exist_ok=True)
    for filename in QWEN_ALLOW_PATTERNS:
        destination = target / filename
        if destination.is_file() and destination.stat().st_size > 0:
            continue
        url = (
            f"{endpoint}/{QWEN_REPO}/raw/main/"
            f"{urllib.parse.quote(filename, safe='')}"
        )
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "PolyKit-SkinTokens/0.1"},
        )
        temporary = target / f".{filename}.{uuid.uuid4().hex}.part"
        try:
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open(
                "wb"
            ) as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            temporary.replace(destination)
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise


class SkinTokensRigGenerator(BaseGenerator):
    """Persistent SkinTokens runtime for the ``auto-rig`` node."""

    MODEL_ID = "skintokens-rig/auto-rig"
    DISPLAY_NAME = "SkinTokens Rigging"
    VRAM_GB = 14

    def __init__(self, model_dir: str | Path, outputs_dir: str | Path) -> None:
        super().__init__(Path(model_dir), Path(outputs_dir))
        self._demo: ModuleType | None = None
        self._bpy_proc: subprocess.Popen[object] | None = None
        self._bpy_port = _port()
        self._lock = threading.RLock()
        self._cleanup_registered = False

    def is_downloaded(self) -> bool:
        return all(
            (self.model_dir / relative).is_file()
            for relative in (CHECKPOINT, SKIN_VAE_CHECKPOINT)
        ) and (self.model_dir / QWEN_ROOT / "config.json").is_file()

    def _ensure_qwen_metadata(self) -> None:
        target = self.model_dir / QWEN_ROOT
        if (target / "config.json").is_file():
            return
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise RuntimeError(
                "SkinTokens requires huggingface_hub to fetch the Qwen3 config. "
                "Run Setup/Repair for the node pack."
            ) from exc
        try:
            print(f"[SkinTokens] Downloading {QWEN_REPO} model metadata …", flush=True)
            snapshot_download(
                repo_id=QWEN_REPO,
                local_dir=str(target),
                allow_patterns=QWEN_ALLOW_PATTERNS,
                ignore_patterns=["*.bin", "*.safetensors", "*.pt", "*.pth"],
            )
        except Exception as exc:
            # Mirrors such as hf-mirror.com may redirect /resolve to
            # huggingface.co, which breaks the hub client's metadata HEAD
            # validation.  Retry the same small allow-list via /raw instead
            # of downloading the 1.5 GB model.safetensors file.
            try:
                print("[SkinTokens] Retrying Qwen metadata through the raw endpoint …", flush=True)
                _download_qwen_metadata_raw(target)
            except Exception as raw_exc:
                raise RuntimeError(
                    "SkinTokens could not download the Qwen3-0.6B model metadata. "
                    "Check the Hugging Face source, proxy, and access to Qwen/Qwen3-0.6B."
                ) from raw_exc
        if not (target / "config.json").is_file():
            raise RuntimeError(
                "SkinTokens downloaded Qwen3 metadata but config.json is missing. "
                "Retry the model download."
            )

    def _ensure_runtime_configs(self) -> None:
        """Make provider configs available from the model working directory.

        The upstream checkpoint stores relative paths such as
        ``./configs/skeleton/vroid.yaml``.  PolyKit intentionally runs the
        provider with ``MODEL_DIR`` as its cwd, so copy the small checked-in
        configs next to the downloaded checkpoints before loading.
        """
        source_root = PROVIDER_ROOT / "configs"
        if not source_root.is_dir():
            raise RuntimeError(
                "SkinTokens provider configs are missing. Run Setup/Repair "
                "from Models before loading the node."
            )
        destination_root = self.model_dir / "configs"
        for source in source_root.rglob("*"):
            if not source.is_file():
                continue
            destination = destination_root / source.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.is_file():
                shutil.copy2(source, destination)

    def _ensure_model_files(self) -> None:
        missing = [
            relative
            for relative in (CHECKPOINT, SKIN_VAE_CHECKPOINT)
            if not (self.model_dir / relative).is_file()
        ]
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                "SkinTokens model files are missing: "
                f"{joined}. Download the TokenRig resource from Models first."
            )
        self._ensure_runtime_configs()
        self._ensure_qwen_metadata()

    def _start_bpy_server(self) -> None:
        _disable_local_proxy()
        if _ping_bpy(self._bpy_port):
            return

        env = os.environ.copy()
        env["SKINTOKENS_BPY_PORT"] = str(self._bpy_port)
        # The service is local.  Explicitly bypass a user proxy so generation
        # cannot fail when the machine is configured for external downloads.
        env["NO_PROXY"] = os.environ["NO_PROXY"]
        env["no_proxy"] = os.environ["no_proxy"]

        kwargs: dict[str, Any] = {
            "cwd": str(PROVIDER_ROOT),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        self._bpy_proc = subprocess.Popen(
            [sys.executable, "bpy_server.py"],
            **kwargs,
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if _ping_bpy(self._bpy_port):
                return
            if self._bpy_proc.poll() is not None:
                raise RuntimeError("SkinTokens Blender transfer service exited during startup")
            time.sleep(0.25)
        _terminate_process(self._bpy_proc)
        self._bpy_proc = None
        raise TimeoutError("Timed out waiting for SkinTokens Blender transfer service")

    @contextmanager
    def _model_cwd(self) -> Iterator[None]:
        previous = Path.cwd()
        os.chdir(self.model_dir)
        try:
            yield
        finally:
            os.chdir(previous)

    def load(self) -> None:
        with self._lock:
            if self.is_loaded():
                return
            _check_cuda_capacity()
            self._ensure_model_files()
            if not PROVIDER_ROOT.is_dir():
                raise RuntimeError(
                    "SkinTokens provider files are missing. Run Setup/Repair from Models."
                )

            os.environ["SKINTOKENS_BPY_PORT"] = str(self._bpy_port)
            os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
            self._start_bpy_server()
            demo = _load_provider_demo()
            with self._model_cwd():
                demo.load_model(CHECKPOINT, None)
            self._demo = demo
            self._model = getattr(demo, "model", True)
            if not self._cleanup_registered:
                atexit.register(self.unload)
                self._cleanup_registered = True

    def unload(self) -> None:
        with self._lock:
            self._model = None
            demo = self._demo
            if demo is not None:
                # Drop provider globals before asking CUDA to release memory.
                for name in ("model", "tokenizer", "transform", "CURRENT_MODEL_CKPT", "CURRENT_HF_PATH"):
                    if hasattr(demo, name):
                        setattr(demo, name, None)
            self._demo = None
            _terminate_process(self._bpy_proc)
            self._bpy_proc = None
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

    def _params(self, params: Mapping[str, object] | None) -> dict[str, object]:
        raw = dict(params or {})
        return {
            "top_k": max(1, int(raw.get("top_k", 5))),
            "top_p": min(1.0, max(0.1, float(raw.get("top_p", 0.95)))),
            "temperature": min(2.0, max(0.1, float(raw.get("temperature", 1.0)))),
            "repetition_penalty": min(4.0, max(1.0, float(raw.get("repetition_penalty", 2.0)))),
            "num_beams": max(1, min(32, int(raw.get("num_beams", 10)))),
            "use_transfer": bool(raw.get("use_transfer", True)),
            "use_postprocess": bool(raw.get("use_postprocess", False)),
        }

    def generate(
        self,
        primary_input: object = None,
        params: Mapping[str, object] | None = None,
        progress_cb: Any | None = None,
        cancel_event: Any | None = None,
        **_: Any,
    ) -> Path:
        if not isinstance(primary_input, (str, Path)):
            raise TypeError("SkinTokens Rigging expects a mesh path as its primary input")
        input_path = Path(primary_input).expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"Input mesh does not exist: {input_path}")
        if input_path.suffix.lower() != ".glb":
            raise ValueError("SkinTokens Rigging requires a GLB mesh input")

        self._check_cancelled(cancel_event)
        if not self.is_loaded():
            self.load()
        self._check_cancelled(cancel_event)
        assert self._demo is not None

        normalized = self._params(params)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.outputs_dir / (
            f"{input_path.stem}_rigged_{uuid.uuid4().hex[:10]}.glb"
        )
        if progress_cb:
            progress_cb(5, "SkinTokens model loaded")

        with self._lock, self._model_cwd():
            self._demo.run_rig(
                [input_path],
                normalized["top_k"],
                normalized["top_p"],
                normalized["temperature"],
                normalized["repetition_penalty"],
                normalized["num_beams"],
                False,
                normalized["use_transfer"],
                normalized["use_postprocess"],
                [output_path],
                CHECKPOINT,
                None,
            )

        self._check_cancelled(cancel_event)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError(f"SkinTokens did not produce an output GLB: {output_path}")
        if not has_skin_metadata(output_path):
            raise RuntimeError(
                "SkinTokens produced a GLB without skins, JOINTS_0, and WEIGHTS_0; "
                "the result is not a valid rigged mesh."
            )
        if progress_cb:
            progress_cb(100, "Rigged GLB exported")
        return output_path
