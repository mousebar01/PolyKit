"""Server-side executor for Python process node-pack processors.

Python process node packs (``processor.py``) use a line-delimited JSON
protocol: the caller writes one JSON object on stdin
(``{input, params, workspaceDir, tempDir}``) and the processor emits JSON
lines on stdout:

    {"type": "progress", "percent": N, "label": "..."}
    {"type": "log", "message": "..."}
    {"type": "done", "result": {"filePath": "...", "text": "..."}}
    {"type": "error", "message": "..."}

This lets the FastAPI control plane execute process nodes generically without
requiring a UI bridge. JavaScript processors use the Node subprocess shim below
and Python processors use their configured virtual environment.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

from services.model_pack_subprocess import _venv_python

ProgressCallback = Callable[[int, str], None]
LogCallback      = Callable[[str], None]

_NODE_SHIM = Path(__file__).resolve().parent.parent / "node_processor_shim.js"
_JS_ENTRY_EXTS = {".js", ".cjs", ".mjs"}


def _is_js_entry(entry: str) -> bool:
    return Path(entry).suffix.lower() in _JS_ENTRY_EXTS


class ProcessExecutionError(RuntimeError):
    """User-facing failure from a process extension subprocess."""


def _process_python(pack_dir: Path) -> str:
    """Prefer the node pack's own venv, fall back to the API interpreter."""
    venv = _venv_python(pack_dir)
    if venv.exists():
        return str(venv)
    return sys.executable


def run_processor(
    pack_dir: Path,
    entry: str,
    input_data: Dict[str, object],
    params: Dict[str, object],
    workspace_dir: str,
    temp_dir: str,
    progress_cb: Optional[ProgressCallback] = None,
    log_cb: Optional[LogCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, object]:
    """Run one process node and return its ``{filePath?, text?}`` result.

    Raises ProcessExecutionError on missing entries, processor errors,
    cancellation, or a subprocess that exits without a ``done`` message.
    """
    entry_path = pack_dir / entry
    if not entry_path.is_file():
        raise ProcessExecutionError(f"Process entry not found: {entry_path}")

    # JS processors run through a Node subprocess shim that speaks the same
    # line-delimited JSON protocol as the Python processors, so a headless /
    # remote backend can execute them (ComfyUI-style: shell out to the right
    # runtime instead of forcing everything into one language).
    if _is_js_entry(entry):
        shim = _NODE_SHIM
        if not shim.is_file():
            raise ProcessExecutionError(f"Node processor shim not found: {shim}")
        command = ["node", str(shim)]
        payload = {
            "extDir": str(pack_dir),
            "entry": entry,
            "input": input_data,
            "params": params,
            "workspaceDir": workspace_dir,
            "tempDir": temp_dir,
        }
    else:
        command = [str(_process_python(pack_dir)), str(entry_path)]
        payload = {
            "input": input_data,
            "params": params,
            "workspaceDir": workspace_dir,
            "tempDir": temp_dir,
        }

    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        if cancel_event is not None:
            def _watch() -> None:
                cancel_event.wait()
                if proc.poll() is None:
                    proc.terminate()
            threading.Thread(target=_watch, daemon=True).start()

        stderr_lines: list[str] = []

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_lines.append(line.rstrip())
        threading.Thread(target=_drain_stderr, daemon=True).start()

        assert proc.stdout is not None
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            message_type = message.get("type")
            if message_type == "progress":
                if progress_cb:
                    progress_cb(int(message.get("percent", 0)), str(message.get("label", "")))
            elif message_type == "log":
                if log_cb:
                    log_cb(str(message.get("message", "")))
            elif message_type == "done":
                result = message.get("result") or {}
                return dict(result) if isinstance(result, dict) else {}
            elif message_type == "error":
                raise ProcessExecutionError(str(message.get("message", "Process extension failed")))

        proc.wait()
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessExecutionError("Cancelled")
        detail = "\n".join(stderr_lines[-20:]) or "no stderr output"
        raise ProcessExecutionError(
            f"Process extension exited without a result: {detail}"
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
