"""Cheap, cached host resource sampling for the PolyKit control plane.

Resource monitoring belongs to the FastAPI host so remote Web clients see the
CPU/RAM/GPU that actually runs inference.
Sampling is demand-driven and cached for two seconds, so multiple clients share
one snapshot and disabling the UI monitor stops collection entirely.
"""
from __future__ import annotations

import copy
import os
import threading
import time
from typing import Any, Callable

try:
    import psutil
except ImportError:  # pragma: no cover - requirements install psutil; keep startup resilient.
    psutil = None  # type: ignore[assignment]

try:
    import pynvml
except ImportError:  # pragma: no cover - non-NVIDIA/minimal installs remain supported.
    pynvml = None  # type: ignore[assignment]

CACHE_SECONDS = 2.0


class SystemResourceSampler:
    def __init__(
        self,
        *,
        cache_seconds: float = CACHE_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.cache_seconds = cache_seconds
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._lock = threading.Lock()
        self._cached_at = float("-inf")
        self._cached: dict[str, Any] | None = None
        self._nvml_checked = False
        self._nvml_available = False

        # psutil's non-blocking CPU percentage is based on the delta since the
        # previous call. Prime it once so the first API request has a baseline.
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass

    def snapshot(self) -> dict[str, Any]:
        now = self._monotonic()
        with self._lock:
            if self._cached is not None and now - self._cached_at < self.cache_seconds:
                return copy.deepcopy(self._cached)

            snapshot = self._collect()
            snapshot["sampled_at"] = self._wall_time()
            snapshot["cache_seconds"] = self.cache_seconds
            self._cached = snapshot
            self._cached_at = now
            return copy.deepcopy(snapshot)

    def _collect(self) -> dict[str, Any]:
        cpu: dict[str, Any] = {
            "usage": None,
            "cores": os.cpu_count(),
        }
        memory: dict[str, Any] = {
            "used": None,
            "available": None,
            "total": None,
        }

        if psutil is not None:
            try:
                cpu["usage"] = round(float(psutil.cpu_percent(interval=None)), 1)
                cpu["cores"] = psutil.cpu_count(logical=True) or os.cpu_count()
            except Exception:
                pass
            try:
                mem = psutil.virtual_memory()
                memory = {
                    "used": int(mem.used),
                    "available": int(mem.available),
                    "total": int(mem.total),
                }
            except Exception:
                pass

        return {
            "cpu": cpu,
            "memory": memory,
            "gpus": self._collect_gpus(),
        }

    def _ensure_nvml(self) -> bool:
        if self._nvml_checked:
            return self._nvml_available
        self._nvml_checked = True
        if pynvml is None:
            return False
        try:
            pynvml.nvmlInit()
            self._nvml_available = True
        except Exception:
            self._nvml_available = False
        return self._nvml_available

    def _collect_gpus(self) -> list[dict[str, Any]]:
        if not self._ensure_nvml() or pynvml is None:
            return []

        try:
            count = int(pynvml.nvmlDeviceGetCount())
        except Exception:
            return []

        gpus: list[dict[str, Any]] = []
        for index in range(count):
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = raw_name.decode("utf-8", errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)

                usage = None
                try:
                    usage = int(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu)
                except Exception:
                    pass

                temperature = None
                try:
                    temperature = int(
                        pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                    )
                except Exception:
                    pass

                gpus.append({
                    "index": index,
                    "name": name,
                    "usage": usage,
                    "memory": {
                        "used": int(mem.used),
                        "total": int(mem.total),
                    },
                    "temperature": temperature,
                })
            except Exception:
                # One inaccessible device should not hide the rest of the host.
                continue
        return gpus


system_resource_sampler = SystemResourceSampler()
