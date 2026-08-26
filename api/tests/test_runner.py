import unittest
import os
import io
import sys
import json
import tempfile
import importlib
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path


_tmp_pack_dir = tempfile.mkdtemp(prefix="polykit-runner-test-")
Path(_tmp_pack_dir, "manifest.json").write_text("{}", encoding="utf-8")
os.environ.setdefault("NODE_PACK_DIR", _tmp_pack_dir)

runner = importlib.import_module("runner")
_apply_manifest_metadata = runner._apply_manifest_metadata
_resolve_ready_schema = runner._resolve_ready_schema
_select_node = runner._select_node


class RunnerTests(unittest.TestCase):
    def test_select_node_uses_model_dir_override(self) -> None:
        manifest = {
            "nodes": [
                {"id": "fast", "params_schema": [{"id": "a"}]},
                {"id": "quality", "params_schema": [{"id": "b"}]},
            ]
        }

        node = _select_node(manifest, str(Path("/tmp/ext/quality")))

        self.assertEqual(node["id"], "quality")

    def test_ready_schema_falls_back_to_selected_node_schema(self) -> None:
        class GenClass:
            @classmethod
            def params_schema(cls):
                raise RuntimeError("not available")

        manifest = {"params_schema": [{"id": "manifest"}]}
        node = {"params_schema": [{"id": "node"}]}

        schema = _resolve_ready_schema(GenClass, node, manifest)

        self.assertEqual(schema, [{"id": "node"}])

    def test_apply_manifest_metadata_prefers_node_specific_values(self) -> None:
        gen = type("Gen", (), {})()
        manifest = {
            "hf_repo": "top/repo",
            "hf_skip_prefixes": ["top/"],
            "download_check": "top/file",
            "params_schema": [{"id": "top"}],
        }
        node = {
            "hf_repo": "node/repo",
            "hf_skip_prefixes": ["node/"],
            "download_check": "node/file",
            "params_schema": [{"id": "node"}],
        }

        _apply_manifest_metadata(gen, manifest, node)

        self.assertEqual(gen.hf_repo, "node/repo")
        self.assertEqual(gen.hf_skip_prefixes, ["node/"])
        self.assertEqual(gen.download_check, "node/file")
        self.assertEqual(gen._params_schema, [{"id": "node"}])

    def test_apply_manifest_metadata_falls_back_to_manifest_when_node_empty(self) -> None:
        gen = type("Gen", (), {})()
        manifest = {
            "hf_repo": "top/repo",
            "hf_skip_prefixes": ["top/"],
            "download_check": "top/file",
            "params_schema": [{"id": "top"}],
        }

        _apply_manifest_metadata(gen, manifest, {})

        self.assertEqual(gen.hf_repo, "top/repo")
        self.assertEqual(gen.hf_skip_prefixes, ["top/"])
        self.assertEqual(gen.download_check, "top/file")
        self.assertEqual(gen._params_schema, [{"id": "top"}])


class SelectNodeTests(unittest.TestCase):
    def test_returns_empty_dict_when_manifest_has_no_nodes(self) -> None:
        self.assertEqual(_select_node({}, ""), {})

    def test_falls_back_to_first_node_when_override_matches_nothing(self) -> None:
        manifest = {"nodes": [{"id": "a"}, {"id": "b"}]}
        self.assertEqual(_select_node(manifest, str(Path("/tmp/ext/zzz")))["id"], "a")

    def test_returns_first_node_when_no_override(self) -> None:
        manifest = {"nodes": [{"id": "a"}, {"id": "b"}]}
        self.assertEqual(_select_node(manifest, "")["id"], "a")


class ResolveReadySchemaTests(unittest.TestCase):
    def test_uses_generator_classmethod_when_available(self) -> None:
        class GenClass:
            @classmethod
            def params_schema(cls):
                return [{"id": "from-class"}]

        schema = _resolve_ready_schema(GenClass, {"params_schema": [{"id": "node"}]}, {})
        self.assertEqual(schema, [{"id": "from-class"}])

    def test_falls_back_to_manifest_when_node_has_no_schema(self) -> None:
        class GenClass:
            @classmethod
            def params_schema(cls):
                raise RuntimeError("unavailable")

        schema = _resolve_ready_schema(GenClass, {}, {"params_schema": [{"id": "manifest"}]})
        self.assertEqual(schema, [{"id": "manifest"}])


class ProtocolTests(unittest.TestCase):
    """recv()/send() implement the newline-delimited JSON wire protocol."""

    def setUp(self) -> None:
        self._stdin = sys.stdin

    def tearDown(self) -> None:
        sys.stdin = self._stdin

    def test_recv_parses_lines_and_skips_blank_lines(self) -> None:
        sys.stdin = io.StringIO('{"a": 1}\n\n   \n{"b": 2}\n')
        self.assertEqual(list(runner.recv()), [{"a": 1}, {"b": 2}])

    def test_recv_skips_invalid_json_without_crashing_and_logs_error(self) -> None:
        sys.stdin = io.StringIO('not json\n{"ok": 1}\n')
        out = io.StringIO()
        with redirect_stdout(out):
            messages = list(runner.recv())

        self.assertEqual(messages, [{"ok": 1}])
        logged = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        self.assertTrue(any(
            entry.get("level") == "error" and "invalid JSON" in entry.get("message", "")
            for entry in logged
        ))

    def test_send_writes_single_json_line(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            runner.send({"type": "ready", "params_schema": []})

        written = out.getvalue()
        self.assertTrue(written.endswith("\n"))
        self.assertEqual(written.count("\n"), 1)
        self.assertEqual(json.loads(written), {"type": "ready", "params_schema": []})


if __name__ == "__main__":
    unittest.main()


class MainTests(unittest.TestCase):
    """main() is the real subprocess entry point — it must announce 'ready'
    for a valid node pack instead of crashing on startup. This guards the
    whole ModelPackSubprocess bootstrap path (see the EXT_DIR -> PACK_DIR fix)."""

    def _fresh_runner(self, pack_dir: str):
        """Re-import runner with NODE_PACK_DIR pointing at a real pack dir,
        so the module-level PACK_DIR constant resolves there."""
        old = os.environ.get("NODE_PACK_DIR")
        os.environ["NODE_PACK_DIR"] = pack_dir
        try:
            sys.modules.pop("runner", None)
            mod = importlib.import_module("runner")
        finally:
            if old is None:
                os.environ.pop("NODE_PACK_DIR", None)
            else:
                os.environ["NODE_PACK_DIR"] = old
        return mod

    def test_main_sends_ready_for_valid_pack(self) -> None:
        pack_dir = tempfile.mkdtemp(prefix="polykit-runner-main-")
        Path(pack_dir, "manifest.json").write_text(
            json.dumps({"id": "demo", "generator_class": "DemoGenerator"}),
            encoding="utf-8",
        )
        Path(pack_dir, "generator.py").write_text(
            "class DemoGenerator:\n"
            "    def __init__(self, model_dir, workspace_dir):\n"
            "        self.model_dir = model_dir\n"
            "    @classmethod\n"
            "    def params_schema(cls):\n"
            "        return [{'id': 'x', 'type': 'int', 'default': 1}]\n",
            encoding="utf-8",
        )

        mod = self._fresh_runner(pack_dir)
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stdin", io.StringIO()):
            mod.main()

        lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        self.assertTrue(any(msg.get("type") == "ready" for msg in lines))
        ready = next(msg for msg in lines if msg.get("type") == "ready")
        self.assertEqual(ready["params_schema"], [{"id": "x", "type": "int", "default": 1}])

class PrestartupScriptTests(unittest.TestCase):
    def test_executes_prestartup_script(self) -> None:
        marker = Path(_tmp_pack_dir, ".prestartup-ran")
        if marker.exists():
            marker.unlink()
        (Path(_tmp_pack_dir, "prestartup_script.py")).write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('ok')\n",
            encoding="utf-8",
        )
        try:
            runner.run_prestartup_script()
            self.assertTrue(marker.exists())
        finally:
            marker.unlink(missing_ok=True)
            (Path(_tmp_pack_dir, "prestartup_script.py")).unlink(missing_ok=True)

    def test_failing_prestartup_script_is_non_fatal(self) -> None:
        (Path(_tmp_pack_dir, "prestartup_script.py")).write_text(
            "raise RuntimeError('boom')\n", encoding="utf-8"
        )
        try:
            with redirect_stdout(io.StringIO()):
                runner.run_prestartup_script()  # must not raise
        finally:
            (Path(_tmp_pack_dir, "prestartup_script.py")).unlink(missing_ok=True)
