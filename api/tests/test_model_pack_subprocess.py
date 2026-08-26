import io
import platform
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.model_pack_subprocess import (
    ModelPackSubprocess,
    _package_install_command,
    _resolve_python,
    _venv_python,
)


def _make_proc() -> ModelPackSubprocess:
    return ModelPackSubprocess(pack_dir=None, manifest={"id": "demo"})  # type: ignore[arg-type]


class ModelPackSubprocessTests(unittest.TestCase):
    def test_read_loop_writes_sentinel_to_own_queue_only(self) -> None:
        proc = _make_proc()

        old_queue: queue.Queue = queue.Queue()
        new_queue: queue.Queue = queue.Queue()
        proc._queue = new_queue

        fake_proc = type("FakeProc", (), {"stdout": io.StringIO("")})()

        proc._read_loop(fake_proc, old_queue)

        self.assertFalse(old_queue.empty())
        self.assertTrue(new_queue.empty())

    def test_download_check_accepts_legacy_shared_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extension_dir = Path(tmp) / "trellis2"
            node_dir = extension_dir / "generate"
            node_dir.mkdir(parents=True)
            (extension_dir / "pipeline.json").write_text("{}", encoding="utf-8")

            proc = ModelPackSubprocess(
                pack_dir=Path(tmp),
                manifest={
                    "id": "trellis2/generate",
                    "download_check": "pipeline.json",
                },
            )
            proc.model_dir = node_dir

            self.assertTrue(proc.is_downloaded())

    def test_download_check_does_not_use_parent_for_flat_model_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "demo"
            model_dir.mkdir(parents=True)
            (model_dir.parent / "pipeline.json").write_text("{}", encoding="utf-8")

            proc = ModelPackSubprocess(
                pack_dir=Path(tmp),
                manifest={"id": "demo", "download_check": "pipeline.json"},
            )
            proc.model_dir = model_dir

            self.assertFalse(proc.is_downloaded())


class VenvPythonTests(unittest.TestCase):
    def test_resolves_interpreter_path_for_current_platform(self) -> None:
        result = _venv_python(Path("/tmp/ext"))
        if platform.system() == "Windows":
            self.assertEqual(result, Path("/tmp/ext") / "venv" / "Scripts" / "python.exe")
        else:
            self.assertEqual(result, Path("/tmp/ext") / "venv" / "bin" / "python")

    def test_package_install_command_targets_uv_and_managed_python(self) -> None:
        python = Path("/tmp/ext/venv/bin/python")
        with patch("services.model_pack_subprocess._uv_executable", return_value="/opt/uv"):
            self.assertEqual(
                _package_install_command(python, "Pillow"),
                ["/opt/uv", "pip", "install", "--python", str(python), "Pillow"],
            )


class MissingModuleExtractionTests(unittest.TestCase):
    def test_extracts_module_name_from_message(self) -> None:
        proc = _make_proc()
        name = proc._extract_missing_module({"message": "No module named 'PIL'"})
        self.assertEqual(name, "PIL")

    def test_extracts_module_name_from_traceback(self) -> None:
        proc = _make_proc()
        name = proc._extract_missing_module(
            {"message": "boom", "traceback": "...\nModuleNotFoundError: No module named \"numpy\"\n"}
        )
        self.assertEqual(name, "numpy")

    def test_returns_none_when_no_missing_module(self) -> None:
        proc = _make_proc()
        self.assertIsNone(proc._extract_missing_module({"message": "some other error"}))


class AutoRepairPackageTests(unittest.TestCase):
    """Safety: only known modules map to a package; never guess arbitrary names."""

    def test_maps_known_module_to_package(self) -> None:
        proc = _make_proc()
        self.assertEqual(proc._resolve_auto_repair_package("PIL"), "Pillow")

    def test_maps_known_module_via_root_package(self) -> None:
        proc = _make_proc()
        self.assertEqual(proc._resolve_auto_repair_package("PIL.Image"), "Pillow")

    def test_returns_none_for_unknown_module(self) -> None:
        proc = _make_proc()
        self.assertIsNone(proc._resolve_auto_repair_package("totally_unknown_pkg"))


class RecvTests(unittest.TestCase):
    def test_returns_message_from_queue(self) -> None:
        proc = _make_proc()
        proc._queue.put({"type": "ready"})
        self.assertEqual(proc._recv(timeout=1.0), {"type": "ready"})

    def test_none_sentinel_raises_runtime_error(self) -> None:
        proc = _make_proc()
        proc._queue.put(None)
        with self.assertRaises(RuntimeError):
            proc._recv(timeout=1.0)

    def test_empty_queue_raises_timeout_error(self) -> None:
        proc = _make_proc()
        with self.assertRaises(TimeoutError):
            proc._recv(timeout=0.05)


if __name__ == "__main__":
    unittest.main()


class ResolvePythonTests(unittest.TestCase):
    """Shared-by-default, isolated-on-demand interpreter resolution."""

    def test_isolated_env_requires_own_venv(self) -> None:
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = Path(tmp)
            result = _resolve_python(pack_dir, {"id": "demo", "env": "isolated"})
            self.assertEqual(result, _venv_python(pack_dir))
            # isolated never falls back to the API interpreter
            self.assertNotEqual(result, Path(sys.executable))

    def test_shared_env_uses_existing_venv_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = Path(tmp)
            venv_python = _venv_python(pack_dir)
            venv_python.parent.mkdir(parents=True)
            venv_python.touch()
            result = _resolve_python(pack_dir, {"id": "demo"})
            self.assertEqual(result, venv_python)

    def test_shared_env_falls_back_to_api_interpreter_without_venv(self) -> None:
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            result = _resolve_python(Path(tmp), {"id": "demo"})
            self.assertEqual(result, Path(sys.executable))

    def test_shared_env_is_the_default_when_env_absent(self) -> None:
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            pack_dir = Path(tmp)
            result = _resolve_python(pack_dir, {"id": "demo"})
            self.assertEqual(result, Path(sys.executable))
