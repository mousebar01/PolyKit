from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from routers import status
from services.model_pack_subprocess import ModelPackSubprocess


class HealthStatusTests(unittest.TestCase):
    """Small direct tests for readiness semantics without starting a server."""

    def test_cuda_runtime_without_device_is_not_inference_capable(self) -> None:
        with patch.object(status, "EXECUTOR", "cuda"), patch.object(
            status.model_runtime_registry,
            "all_status",
            return_value=[{"id": "trellis2/generate", "downloaded": False}],
        ), patch.object(status, "_cuda_available", return_value=False):
            assert status._inference_capable() is False

    def test_fake_runtime_is_inference_capable_for_smoke_artifacts(self) -> None:
        with patch.object(status, "EXECUTOR", "fake"), patch.object(
            status.model_runtime_registry,
            "all_status",
            return_value=[{"id": "fake", "downloaded": True}],
        ):
            assert status._inference_capable() is True

    def test_isolated_pack_runtime_is_used_for_cuda_readiness(self) -> None:
        pack = ModelPackSubprocess(Path("/tmp/anima"), {"id": "anima/generate"})
        with patch.object(status, "EXECUTOR", "cuda"), patch.object(
            status.model_runtime_registry,
            "all_status",
            return_value=[{"id": "anima/generate", "downloaded": True}],
        ), patch.object(
            status.model_runtime_registry,
            "active_status",
            return_value={"id": "anima/generate", "downloaded": True},
        ), patch.object(status.model_runtime_registry, "get_generator", return_value=pack), patch.object(
            pack,
            "runtime_status",
            return_value={"cuda": {"available": True}},
        ):
            assert status._inference_capable() is True
