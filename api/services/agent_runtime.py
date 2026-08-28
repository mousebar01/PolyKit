"""Lifecycle and loopback transport for the embedded Agent runtime.

The Agent UI is part of the Web client, but its session engine remains an
isolated Node process because the pi SDK is a Node/TypeScript runtime.  This
service starts that process on demand and keeps FastAPI as the only public
boundary exposed to browsers and desktop clients.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import threading
from pathlib import Path
from typing import Optional

from services.runtime_paths import runtime_paths


class AgentRuntimeError(RuntimeError):
    """Raised when the embedded Agent sidecar cannot be started."""


class AgentRuntime:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._process: Optional[subprocess.Popen[str]] = None
        self._port: Optional[int] = None
        self._token: Optional[str] = None
        self._stderr_thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None and self._port is not None

    async def ensure_started(self) -> tuple[int, str]:
        async with self._lock:
            if self.running:
                assert self._port is not None and self._token is not None
                return self._port, self._token
            await asyncio.to_thread(self._start_sync)
            if not self.running or self._port is None or self._token is None:
                raise AgentRuntimeError("Agent runtime did not become ready")
            return self._port, self._token

    def _start_sync(self) -> None:
        self._stop_sync()
        root = Path(__file__).resolve().parents[2]
        server = root / "agent" / "runtime" / "server.ts"
        jiti = root / "agent" / "node_modules" / "jiti" / "lib" / "jiti-cli.mjs"
        if not server.is_file():
            raise AgentRuntimeError(f"Agent runtime is missing: {server}")
        if not jiti.is_file():
            raise AgentRuntimeError(f"Agent runtime dependencies are missing: {jiti}")

        token = secrets.token_urlsafe(32)
        session_root = runtime_paths.data / "agent" / "sessions"
        configured_agent_dir = runtime_paths.data / "agent"
        legacy_agent_dir = Path.home() / ".pi" / "agent"
        # Preserve an existing local Agent login/model catalog without copying
        # credentials into a second location. Session JSONL stays server-owned
        # under runtime_paths.data regardless of which config dir is selected.
        agent_dir = configured_agent_dir
        if not os.environ.get("PI_CODING_AGENT_DIR") and (
            (legacy_agent_dir / "auth.json").is_file()
            or (legacy_agent_dir / "models.json").is_file()
            or (legacy_agent_dir / "models-store.json").is_file()
        ):
            agent_dir = legacy_agent_dir
        env = os.environ.copy()
        env.update({
            "POLYKIT_AGENT_INTERNAL_TOKEN": token,
            "POLYKIT_AGENT_PORT": "0",
            "POLYKIT_WORKSPACE_DIR": str(runtime_paths.workspace),
            # The embedded Agent sessions run inside the server-owned workspace,
            # while the project-owned MCP declaration lives beside this repo.
            # Passing the path explicitly lets the Agent discover local Worlds
            # tools without writing a hidden .mcp.json into the user's workspace.
            "POLYKIT_MCP_CONFIG": str(root / ".mcp.json"),
            "PI_CODING_AGENT_DIR": str(agent_dir),
            "PI_CODING_AGENT_SESSION_DIR": str(session_root),
        })
        process = subprocess.Popen(
            ["node", str(jiti), str(server)],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._process = process
        self._token = token
        self._port = None
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(process,),
            name="polykit-agent-stderr",
            daemon=True,
        )
        self._stderr_thread.start()

        assert process.stdout is not None
        import time

        end = time.monotonic() + 30
        while time.monotonic() < end:
            line = process.stdout.readline()
            if line:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("ready") is True and isinstance(message.get("port"), int):
                    self._port = int(message["port"])
                    return
            elif process.poll() is not None:
                break

        output = ""
        if process.poll() is not None:
            output = f" (exit code {process.returncode})"
        self._stop_sync()
        raise AgentRuntimeError(f"Agent runtime failed to start{output}")

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            # Keep sidecar diagnostics visible in the FastAPI logs without
            # allowing stderr back-pressure to deadlock startup or streaming.
            print(f"[Agent] {line.rstrip()}", flush=True)

    def _stop_sync(self) -> None:
        process = self._process
        self._process = None
        self._port = None
        self._token = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    async def stop(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._stop_sync)


agent_runtime = AgentRuntime()
