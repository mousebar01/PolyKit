"""Tests for the server-owned runtime settings store and proxy handling."""
import os
import tempfile
import unittest
from pathlib import Path

import services.runtime_settings as rs


class RuntimeSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_file = rs.SETTINGS_FILE
        self._tmp = tempfile.TemporaryDirectory()
        rs.SETTINGS_FILE = Path(self._tmp.name) / "settings.json"
        self._saved_env = {
            key: os.environ.get(key)
            for key in (*rs._PROXY_ENV_KEYS, *rs._NO_PROXY_ENV_KEYS, *rs._SOURCE_ENV_KEYS)
        }
        for key in self._saved_env:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        rs.SETTINGS_FILE = self._orig_file
        self._tmp.cleanup()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_save_and_load_persist_proxy(self) -> None:
        proxy = rs.ProxyConfig(
            enabled=True,
            url="http://127.0.0.1:7890",
            username="user",
            password="pass",
            bypass="internal.local",
        )
        rs.set_proxy(proxy)
        loaded = rs.get_proxy()
        self.assertTrue(loaded.enabled)
        self.assertEqual(loaded.url, "http://127.0.0.1:7890")
        self.assertEqual(loaded.username, "user")
        self.assertEqual(loaded.password, "pass")
        self.assertEqual(loaded.bypass, "internal.local")

    def test_effective_url_embeds_credentials(self) -> None:
        proxy = rs.ProxyConfig(
            enabled=True, url="http://127.0.0.1:7890", username="a b", password="p@ss"
        )
        self.assertEqual(proxy.effective_url, "http://a%20b:p%40ss@127.0.0.1:7890")

    def test_effective_url_without_credentials_is_passthrough(self) -> None:
        proxy = rs.ProxyConfig(enabled=True, url="http://127.0.0.1:7890")
        self.assertEqual(proxy.effective_url, "http://127.0.0.1:7890")

    def test_apply_proxy_env_http(self) -> None:
        rs.apply_proxy_env(
            rs.ProxyConfig(enabled=True, url="http://127.0.0.1:7890", bypass="internal.local")
        )
        self.assertEqual(os.environ.get("HTTP_PROXY"), "http://127.0.0.1:7890")
        self.assertEqual(os.environ.get("HTTPS_PROXY"), "http://127.0.0.1:7890")
        self.assertEqual(os.environ.get("http_proxy"), "http://127.0.0.1:7890")
        self.assertEqual(os.environ.get("https_proxy"), "http://127.0.0.1:7890")
        no_proxy = os.environ.get("NO_PROXY") or ""
        self.assertIn("127.0.0.1", no_proxy)
        self.assertIn("localhost", no_proxy)
        self.assertIn("internal.local", no_proxy)
        self.assertIsNone(os.environ.get("ALL_PROXY"))

    def test_apply_proxy_env_socks_sets_all_proxy(self) -> None:
        rs.apply_proxy_env(rs.ProxyConfig(enabled=True, url="socks5://127.0.0.1:1080"))
        self.assertEqual(os.environ.get("ALL_PROXY"), "socks5://127.0.0.1:1080")
        self.assertIsNone(os.environ.get("HTTP_PROXY"))

    def test_disable_clears_proxy_env(self) -> None:
        rs.apply_proxy_env(rs.ProxyConfig(enabled=True, url="http://127.0.0.1:7890"))
        rs.apply_proxy_env(rs.ProxyConfig())
        for key in (*rs._PROXY_ENV_KEYS, *rs._NO_PROXY_ENV_KEYS):
            self.assertIsNone(os.environ.get(key))

    def test_apply_persisted_proxy_applies_saved(self) -> None:
        rs.set_proxy(rs.ProxyConfig(enabled=True, url="http://127.0.0.1:7890"))
        rs.apply_persisted_proxy()
        self.assertEqual(os.environ.get("HTTP_PROXY"), "http://127.0.0.1:7890")

    def test_apply_persisted_proxy_skips_when_shell_configured(self) -> None:
        rs.set_proxy(rs.ProxyConfig(enabled=True, url="http://127.0.0.1:7890"))
        # Simulate a fresh boot where only the launcher's shell vars exist.
        rs.apply_proxy_env(rs.ProxyConfig())
        os.environ["HTTP_PROXY"] = "http://shell:8080"
        rs.apply_persisted_proxy()
        self.assertEqual(os.environ.get("HTTP_PROXY"), "http://shell:8080")
        self.assertIsNone(os.environ.get("https_proxy"))

    def test_apply_persisted_proxy_normalizes_mixed_case_launcher_vars(self) -> None:
        rs.set_proxy(rs.ProxyConfig(enabled=True, url="http://saved:8080"))
        rs.apply_proxy_env(rs.ProxyConfig())
        os.environ["HTTP_PROXY"] = "http://shell:8080"
        os.environ["HTTPS_PROXY"] = "http://shell:8080"
        os.environ["https_proxy"] = "http://stale:7897"
        rs.apply_persisted_proxy()
        self.assertEqual(os.environ.get("HTTPS_PROXY"), "http://shell:8080")
        self.assertEqual(os.environ.get("https_proxy"), "http://shell:8080")

    def test_missing_settings_file_defaults_to_disabled(self) -> None:
        self.assertFalse(rs.get_proxy().enabled)
        self.assertEqual(rs.get_proxy().url, "")

    def test_save_and_load_download_sources(self) -> None:
        sources = rs.DownloadSourceConfig(
            huggingface_endpoint="https://hf-mirror.example/",
            pypi_index_url="https://pypi.example/simple/",
            pytorch_index_url="https://torch.example/{tag}/",
        )
        rs.set_download_sources(sources)
        loaded = rs.get_download_sources()
        self.assertEqual(loaded.huggingface_endpoint, "https://hf-mirror.example")
        self.assertEqual(loaded.pypi_index_url, "https://pypi.example/simple")
        self.assertEqual(loaded.pytorch_index_url, "https://torch.example/{tag}")

    def test_apply_download_sources_sets_child_process_env(self) -> None:
        rs.apply_download_sources(rs.DownloadSourceConfig(
            huggingface_endpoint="https://hf-mirror.example/",
            pypi_index_url="https://pypi.example/simple/",
            pytorch_index_url="https://torch.example/{tag}/",
        ))
        self.assertEqual(os.environ.get("HF_ENDPOINT"), "https://hf-mirror.example")
        self.assertEqual(os.environ.get("UV_INDEX_URL"), "https://pypi.example/simple")
        self.assertEqual(os.environ.get("PIP_INDEX_URL"), "https://pypi.example/simple")
        self.assertEqual(os.environ.get("POLYKIT_PYTORCH_INDEX_URL"), "https://torch.example/{tag}")

    def test_empty_download_sources_clear_env(self) -> None:
        rs.apply_download_sources(rs.DownloadSourceConfig(huggingface_endpoint="https://hf.example"))
        rs.apply_download_sources(rs.DownloadSourceConfig())
        for key in rs._SOURCE_ENV_KEYS:
            self.assertIsNone(os.environ.get(key))

    def test_persisted_sources_respect_explicit_env_per_ecosystem(self) -> None:
        rs.save_settings({
            "sources": rs.DownloadSourceConfig(
                huggingface_endpoint="https://hf.example",
                pypi_index_url="https://pypi.example/simple",
                pytorch_index_url="https://torch.example/{tag}",
            ).to_dict(),
        })
        os.environ["PIP_INDEX_URL"] = "https://shell.example/simple"
        rs.apply_persisted_download_sources()
        self.assertEqual(os.environ.get("HF_ENDPOINT"), "https://hf.example")
        self.assertEqual(os.environ.get("PIP_INDEX_URL"), "https://shell.example/simple")
        self.assertIsNone(os.environ.get("UV_INDEX_URL"))
        self.assertEqual(os.environ.get("POLYKIT_PYTORCH_INDEX_URL"), "https://torch.example/{tag}")


if __name__ == "__main__":
    unittest.main()
