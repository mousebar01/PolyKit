import json
import tempfile
import unittest
from pathlib import Path

from services.process_runner import ProcessExecutionError, run_processor


def _write_processor(pack_dir: Path, script: str) -> Path:
    entry = pack_dir / "processor.py"
    entry.write_text(script, encoding="utf-8")
    return entry


class ProcessRunnerTests(unittest.TestCase):
    def test_runs_processor_and_returns_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pack_dir = Path(td) / "ext"
            pack_dir.mkdir()
            out_dir = Path(td) / "out"
            out_dir.mkdir()
            tmp_dir = Path(td) / "tmp"
            tmp_dir.mkdir()
            _write_processor(pack_dir, """
import json, sys
def emit(obj):
    print(json.dumps(obj), flush=True)
raw = json.loads(sys.stdin.readline())
src = raw["input"]["filePath"]
emit({"type": "progress", "percent": 50, "label": "Working"})
out = raw["workspaceDir"] + "/result.glb"
import shutil
shutil.copy2(src, out)
emit({"type": "done", "result": {"filePath": out}})
""")
            source = out_dir / "source.glb"
            source.write_bytes(b"glb")

            result = run_processor(
                pack_dir,
                "processor.py",
                {"filePath": str(source)},
                {"mode": "test"},
                str(out_dir),
                str(tmp_dir),
            )

            self.assertEqual(result["filePath"], str(out_dir / "result.glb"))
            self.assertEqual((out_dir / "result.glb").read_bytes(), b"glb")

    def test_propagates_processor_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pack_dir = Path(td) / "ext"
            pack_dir.mkdir()
            _write_processor(pack_dir, """
import json, sys
def emit(obj):
    print(json.dumps(obj), flush=True)
json.loads(sys.stdin.readline())
emit({"type": "error", "message": "boom"})
""")
            with self.assertRaises(ProcessExecutionError) as ctx:
                run_processor(pack_dir, "processor.py", {}, {}, str(Path(td)), str(Path(td)))
            self.assertIn("boom", str(ctx.exception))

    def test_missing_entry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pack_dir = Path(td) / "ext"
            pack_dir.mkdir()
            with self.assertRaises(ProcessExecutionError):
                run_processor(pack_dir, "nope.py", {}, {}, str(Path(td)), str(Path(td)))


if __name__ == "__main__":
    unittest.main()


class NodeProcessorTests(unittest.TestCase):
    """JS process nodes run through the Node subprocess shim (headless parity)."""

    def _write_js_processor(self, pack_dir: Path) -> None:
        entry = pack_dir / "processor.js"
        entry.write_text(
            """
'use strict'
module.exports = async (input, params, context) => {
  context.progress(40, 'Working…')
  context.log('node processor running')
  const fs = require('fs')
  const out = context.workspaceDir + '/result.glb'
  fs.copyFileSync(input.filePath, out)
  return { filePath: out }
}
""".strip() + "\n",
            encoding="utf-8",
        )

    def test_runs_js_processor_via_node_shim(self) -> None:
        import shutil
        import subprocess
        # Skip when node is unavailable on the host.
        if shutil.which("node") is None:
            self.skipTest("node not installed")
        with tempfile.TemporaryDirectory() as td:
            pack_dir = Path(td) / "ext"
            pack_dir.mkdir()
            out_dir = Path(td) / "out"
            out_dir.mkdir()
            tmp_dir = Path(td) / "tmp"
            tmp_dir.mkdir()
            self._write_js_processor(pack_dir)
            source = out_dir / "source.glb"
            source.write_bytes(b"glb")

            result = run_processor(
                pack_dir,
                "processor.js",
                {"filePath": str(source)},
                {"mode": "test"},
                str(out_dir),
                str(tmp_dir),
                progress_cb=lambda pct, label: None,
                log_cb=lambda msg: None,
            )

            self.assertEqual(result["filePath"], str(out_dir / "result.glb"))
            self.assertEqual((out_dir / "result.glb").read_bytes(), b"glb")

    def test_js_processor_error_propagates(self) -> None:
        import shutil
        if shutil.which("node") is None:
            self.skipTest("node not installed")
        with tempfile.TemporaryDirectory() as td:
            pack_dir = Path(td) / "ext"
            pack_dir.mkdir()
            (pack_dir / "processor.js").write_text(
                "module.exports = async () => { throw new Error('js boom') }\n",
                encoding="utf-8",
            )
            with self.assertRaises(ProcessExecutionError) as ctx:
                run_processor(pack_dir, "processor.js", {}, {}, str(Path(td)), str(Path(td)))
            self.assertIn("js boom", str(ctx.exception))
