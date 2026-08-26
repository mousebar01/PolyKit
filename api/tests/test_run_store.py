import tempfile
import unittest
from pathlib import Path

from services.run_store import JobStatus, RunStore


class RunStoreTests(unittest.TestCase):
    def test_round_trips_status_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp) / "runs.sqlite3")
            job = JobStatus(job_id="run-1", status="done", progress=100, meta={"executor": "fake"})
            store.save(job, completed_at=123.0)

            loaded = store.load()["run-1"]
            self.assertEqual(loaded.status, "done")
            self.assertEqual(loaded.meta, {"executor": "fake"})
            self.assertEqual(store.completed_times()["run-1"], 123.0)

    def test_marks_inflight_runs_interrupted_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.sqlite3"
            first = RunStore(path)
            first.save(JobStatus(job_id="run-1", status="running", progress=42))

            second = RunStore(path)
            loaded = second.load()["run-1"]
            self.assertEqual(loaded.status, "interrupted")
            self.assertIn("restarted", loaded.error or "")


if __name__ == "__main__":
    unittest.main()
