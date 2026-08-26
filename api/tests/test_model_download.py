"""Tests for the server-owned Hugging Face download contract."""
import asyncio
import os
import threading
import unittest
from unittest.mock import patch

from routers import model
from services.runtime_settings import DownloadSourceConfig


class ModelDownloadTests(unittest.TestCase):
    def test_node_model_id_resolves_to_manifest_download_location(self) -> None:
        with patch.object(
            model.model_runtime_registry,
            "get_manifest",
            return_value={"download": {"location": "trellis2"}},
        ):
            self.assertEqual(model._download_model_id("trellis2/generate"), "trellis2")

    def test_unknown_model_id_keeps_its_own_download_location(self) -> None:
        with patch.object(
            model.model_runtime_registry,
            "get_manifest",
            side_effect=KeyError("unknown"),
        ):
            self.assertEqual(model._download_model_id("custom/node"), "custom/node")

    def test_persisted_huggingface_endpoint_wins_over_process_environment(self) -> None:
        with patch.dict(os.environ, {"HF_ENDPOINT": "https://public.example"}):
            with patch.object(
                model,
                "get_download_sources",
                return_value=DownloadSourceConfig(huggingface_endpoint="https://mirror.example/"),
            ):
                self.assertEqual(model._huggingface_endpoint(), "https://mirror.example")

    def test_process_environment_is_used_when_no_mirror_is_configured(self) -> None:
        with patch.dict(os.environ, {"HF_ENDPOINT": "https://env.example"}):
            with patch.object(model, "get_download_sources", return_value=DownloadSourceConfig()):
                self.assertEqual(model._huggingface_endpoint(), "https://env.example")

    def test_pause_and_cancel_accept_shared_node_ids(self) -> None:
        control = {"pause": threading.Event(), "cancel": threading.Event()}
        model._download_controls.clear()
        model._download_controls["trellis2"] = control
        try:
            with patch.object(
                model.model_runtime_registry,
                "get_manifest",
                return_value={"download": {"location": "trellis2"}},
            ):
                paused = asyncio.run(model.pause_hf_download(form_model_id="trellis2/generate"))
                cancelled = asyncio.run(model.cancel_hf_download(model_id="trellis2/refine"))

            self.assertEqual(paused, {"paused": True})
            self.assertEqual(cancelled, {"cancelled": True})
            self.assertTrue(control["pause"].is_set())
            self.assertTrue(control["cancel"].is_set())
        finally:
            model._download_controls.clear()


if __name__ == "__main__":
    unittest.main()
