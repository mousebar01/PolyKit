"""
ModelPackSubprocess — manages a generator running in an isolated subprocess.

Each node pack runs in its own venv via runner.py. Communication is newline-
delimited JSON on stdin/stdout. The interface intentionally mirrors direct
BaseGenerator usage so ModelRuntimeRegistry can treat both transparently.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from services.runtime_paths import runtime_paths

_RUNNER_PATH = Path(__file__).parent.parent / "runner.py"
_MISSING_MODULE_RE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
_AUTO_REPAIR_PACKAGE_MAP = {"PIL": "Pillow"}

# Hunyuan3D-Part P3-SAM VRAM profiles. The upstream adapter's Linux defaults
# (100k points, 400 prompts, batch 32) need more than 24 GB of VRAM; these
# tiers are injected only when the user has not pinned the env vars explicitly.
_P3SAM_KNOB_ENV_VARS = {
    "point_num": "HUNYUAN3D_PART_P3SAM_POINT_NUM",
    "prompt_num": "HUNYUAN3D_PART_P3SAM_PROMPT_NUM",
    "prompt_bs": "HUNYUAN3D_PART_P3SAM_PROMPT_BS",
}
_GIB = 1024 * 1024 * 1024
_UNSET = object()
_P3SAM_VRAM_PROFILES: tuple[tuple[int, dict[str, int]], ...] = (
    (18, {"point_num": 40000, "prompt_num": 160, "prompt_bs": 4}),
    (0, {"point_num": 32768, "prompt_num": 128, "prompt_bs": 1}),
)


def _free_cuda_bytes() -> Optional[int]:
    """Best-effort free VRAM probe for the primary CUDA device."""
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return int(info.free)
    except Exception:
        pass
    try:
        import torch

        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info()
            return int(free)
    except Exception:
        pass
    return None


def _p3sam_vram_env_overrides(free_bytes: Any = _UNSET) -> dict[str, str]:
    """Env defaults that keep P3-SAM inside the available VRAM budget."""
    if free_bytes is _UNSET:
        free_bytes = _free_cuda_bytes()
    if free_bytes is None or free_bytes >= 26 * _GIB:
        return {}
    for min_free_gib, knobs in _P3SAM_VRAM_PROFILES:
        if free_bytes >= min_free_gib * _GIB:
            return {var: str(knobs[name]) for name, var in _P3SAM_KNOB_ENV_VARS.items()}
    return {}


def _venv_python(pack_dir: Path) -> Path:
    if platform.system() == "Windows":
        return pack_dir / "venv" / "Scripts" / "python.exe"
    return pack_dir / "venv" / "bin" / "python"


def _uv_executable() -> str:
    """Return the configured uv executable or fail with an actionable error."""
    configured = os.environ.get("POLYKIT_UV")
    if configured:
        if Path(configured).is_file():
            return configured
        resolved = shutil.which(configured)
    else:
        resolved = shutil.which("uv")
    if resolved:
        return resolved
    raise RuntimeError(
        "PolyKit requires uv to repair Node Pack dependencies. "
        "Install it from https://docs.astral.sh/uv/getting-started/installation/ "
        "and retry the operation."
    )


def _package_install_command(python: Path, package_name: str) -> list[str]:
    """Build an isolated package install command through uv."""
    uv = _uv_executable()
    return [uv, "pip", "install", "--python", str(python), package_name]


def _resolve_python(pack_dir: Path, manifest: dict) -> Path:
    """Pick the interpreter a node pack should run under."""
    if manifest.get("env", "shared") == "isolated":
        return _venv_python(pack_dir)
    venv = _venv_python(pack_dir)
    if venv.exists():
        return venv
    return Path(sys.executable)


class ModelPackSubprocess:
    """Wrap an extension subprocess with the BaseGenerator-compatible API."""

    def __init__(self, pack_dir: Path, manifest: dict) -> None:
        self.pack_dir = pack_dir
        self.manifest = manifest
        self.model_dir = None
        self.outputs_dir = None

        self._proc: Optional[subprocess.Popen] = None
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._loaded = False

        self.hf_repo = manifest.get("hf_repo", "")
        self.hf_skip_prefixes = manifest.get("hf_skip_prefixes", [])
        self.download_check = manifest.get("download_check", "")
        self._params_schema = manifest.get("params_schema", [])

        self.MODEL_ID = manifest.get("id", "")
        self.DISPLAY_NAME = manifest.get("name", "")
        self.VRAM_GB = manifest.get("vram_gb", 0)

    def _build_env(self) -> dict:
        """Build the child environment from the central runtime-path owner."""
        env = os.environ.copy()
        env["NODE_PACK_DIR"] = str(self.pack_dir)
        env["MODELS_DIR"] = str(runtime_paths.models)
        env["WORKSPACE_DIR"] = str(runtime_paths.workspace)
        env["POLYKIT_API_DIR"] = str(Path(__file__).parent.parent)
        manifest_id = str(self.manifest.get("id") or "")
        if manifest_id.split("/", 1)[0] == "hunyuan3d-part":
            already_pinned = any(var in os.environ for var in _P3SAM_KNOB_ENV_VARS.values())
            if not already_pinned:
                env.update(_p3sam_vram_env_overrides())
        if sys.platform == "darwin":
            env.setdefault("NUMBA_DISABLE_JIT", "1")
        if self.model_dir is not None:
            env["MODEL_DIR"] = str(self.model_dir)
        if "SSL_CERT_FILE" not in env:
            try:
                import certifi

                env["SSL_CERT_FILE"] = certifi.where()
            except ImportError:
                pass
        return env

    def _start(self) -> None:
        python = _resolve_python(self.pack_dir, self.manifest)
        if not python.exists():
            mode = self.manifest.get("env", "shared")
            if mode == "isolated":
                raise RuntimeError(
                    f"[{self.MODEL_ID}] isolated env requires a venv at {python}. "
                    "Run the node pack's setup.py first."
                )
            raise RuntimeError(f"[{self.MODEL_ID}] interpreter not found at {python}.")

        for attempt in range(3):
            run_queue: queue.Queue = queue.Queue()
            self._queue = run_queue
            self._proc = subprocess.Popen(
                [str(python), str(_RUNNER_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self._build_env(),
            )

            reader = threading.Thread(
                target=self._read_loop, args=(self._proc, run_queue), daemon=True
            )
            reader.start()
            stderr_fwd = threading.Thread(
                target=self._stderr_loop, args=(self._proc,), daemon=True
            )
            stderr_fwd.start()

            msg = self._recv(timeout=None)
            if msg.get("type") == "ready":
                if msg.get("params_schema"):
                    self._params_schema = msg["params_schema"]
                print(
                    f"[ModelPackSubprocess] {self.MODEL_ID} subprocess started "
                    f"(pid {self._proc.pid})"
                )
                return

            self._proc.kill()
            self._proc.wait()
            missing_module = self._extract_missing_module(msg)
            package_name = (
                self._resolve_auto_repair_package(missing_module)
                if missing_module
                else None
            )
            if package_name and attempt < 2:
                self._install_missing_package(python, missing_module, package_name)
                continue
            raise RuntimeError(f"[{self.MODEL_ID}] Expected 'ready', got: {msg}")

    def _extract_missing_module(self, msg: dict) -> Optional[str]:
        blob = f"{msg.get('message', '')}\n{msg.get('traceback', '')}"
        match = _MISSING_MODULE_RE.search(blob)
        return match.group(1) if match else None

    def _resolve_auto_repair_package(self, module_name: str) -> Optional[str]:
        if module_name in _AUTO_REPAIR_PACKAGE_MAP:
            return _AUTO_REPAIR_PACKAGE_MAP[module_name]
        return _AUTO_REPAIR_PACKAGE_MAP.get(module_name.split(".")[0])

    def _install_missing_package(
        self, python: Path, module_name: str, package_name: str
    ) -> None:
        print(
            f"[ModelPackSubprocess] {self.MODEL_ID} missing module '{module_name}' "
            f"-> installing '{package_name}'"
        )
        try:
            subprocess.run(
                _package_install_command(python, package_name),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(
                f"[{self.MODEL_ID}] Auto-repair failed while installing "
                f"'{package_name}' for missing module '{module_name}'.\n{details[-2000:]}"
            ) from exc

    def _read_loop(self, proc: subprocess.Popen, msg_queue: queue.Queue) -> None:
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg_queue.put(json.loads(line))
                except json.JSONDecodeError:
                    print(f"[{self.MODEL_ID}] {line}", file=sys.stderr)
        finally:
            msg_queue.put(None)

    def _stderr_loop(self, proc: subprocess.Popen) -> None:
        """Forward stderr, treating both newline and carriage return as lines."""
        stream = proc.stderr
        if stream is None:
            return
        buf: list[str] = []
        while True:
            ch = stream.read(1)
            if not ch:
                if buf:
                    print("".join(buf), file=sys.stderr, flush=True)
                return
            if ch in ("\r", "\n"):
                if buf:
                    print("".join(buf), file=sys.stderr, flush=True)
                    buf = []
            else:
                buf.append(ch)

    def _send(self, msg: dict) -> None:
        with self._lock:
            if self._proc is None or self._proc.stdin is None:
                raise RuntimeError(f"[{self.MODEL_ID}] subprocess is not running")
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()

    def _recv(self, timeout: float | None = 120.0) -> dict:
        try:
            msg = self._queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(
                f"[{self.MODEL_ID}] No response from subprocess after {timeout}s"
            ) from exc
        if msg is None:
            raise RuntimeError(f"[{self.MODEL_ID}] Subprocess died unexpectedly")
        return msg

    def _ensure_started(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._start()

    def is_downloaded(self) -> bool:
        if self.model_dir is None:
            return False
        if self.download_check:
            if (self.model_dir / self.download_check).exists():
                return True
            if "/" in self.MODEL_ID:
                return (self.model_dir.parent / self.download_check).exists()
            return False
        return self.model_dir.exists() and any(self.model_dir.iterdir())

    def is_loaded(self) -> bool:
        return (
            self._loaded
            and self._proc is not None
            and self._proc.poll() is None
        )

    def load(self) -> None:
        self._ensure_started()
        self._send({"action": "load"})
        msg = self._recv(timeout=None)
        if msg.get("type") in ("loaded", "ready"):
            self._loaded = True
        elif msg.get("type") == "error":
            raise RuntimeError(msg.get("traceback") or msg.get("message"))
        else:
            raise RuntimeError(f"[{self.MODEL_ID}] Unexpected response to load: {msg}")

    def unload(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._send({"action": "unload"})
                self._recv(timeout=30.0)
            except Exception:
                pass
        self._loaded = False

    def generate(
        self,
        image_bytes: Any,
        params: dict,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Path:
        """Generate from either image bytes or a path-like primary input."""
        from services.generators.base import GenerationCancelled

        req_id = str(uuid.uuid4())
        request: dict[str, Any] = {
            "action": "generate",
            "id": req_id,
            "params": params,
            "outputs_dir": str(self.outputs_dir) if self.outputs_dir else None,
        }
        if isinstance(image_bytes, (str, Path)):
            request["primary_input"] = {
                "kind": "path",
                "path": str(image_bytes),
            }
        elif image_bytes is None:
            request["primary_input"] = {"kind": "none"}
        elif isinstance(image_bytes, (bytes, bytearray, memoryview)):
            request["image_b64"] = base64.b64encode(bytes(image_bytes)).decode()
        else:
            raise TypeError(
                f"[{self.MODEL_ID}] primary input must be bytes, path-like, or None; "
                f"got {type(image_bytes).__name__}"
            )
        self._send(request)

        cancel_grace_seconds = 3.0
        cancel_sent_at: Optional[float] = None
        while True:
            if cancel_event and cancel_event.is_set():
                if cancel_sent_at is None:
                    try:
                        self._send({"action": "cancel", "id": req_id})
                    except Exception:
                        pass
                    import time

                    cancel_sent_at = time.monotonic()
                else:
                    import time

                    if time.monotonic() - cancel_sent_at >= cancel_grace_seconds:
                        try:
                            if self._proc and self._proc.poll() is None:
                                self._proc.kill()
                                self._proc.wait(timeout=5.0)
                        except Exception:
                            pass
                        self._loaded = False
                        self._proc = None
                        print(
                            f"[ModelPackSubprocess] {self.MODEL_ID} subprocess killed "
                            f"after {cancel_grace_seconds}s grace; model will reload "
                            "on next run",
                            file=sys.stderr,
                        )
                        raise GenerationCancelled()

            try:
                msg = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if msg is None:
                raise RuntimeError(
                    f"[{self.MODEL_ID}] Subprocess died during generation"
                )

            msg_type = msg.get("type")
            if msg_type == "progress":
                if progress_cb:
                    progress_cb(msg.get("pct", 0), msg.get("step", ""))
            elif msg_type == "done":
                return Path(msg["output_path"])
            elif msg_type == "error":
                raise RuntimeError(
                    msg.get("traceback") or msg.get("message", "Unknown error")
                )
            elif msg_type == "cancelled":
                raise GenerationCancelled()
            elif msg_type == "log":
                print(f"[{self.MODEL_ID}] {msg.get('message', '')}", file=sys.stderr)

    def params_schema(self) -> list:
        return self._params_schema

    def stop(self) -> None:
        """Stop the subprocess and give the generator a chance to clean up.

        A hard kill is still the fallback for an in-flight generation, but an
        idle generator gets a shutdown message first so provider-owned child
        services (for example SkinTokens' Blender process) are not orphaned.
        """
        proc = self._proc
        self._loaded = False
        if proc and proc.poll() is None:
            try:
                self._send({"action": "shutdown"})
                proc.wait(timeout=10)
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        self._proc = None
        self._drain_queue()

    def _drain_queue(self) -> None:
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
