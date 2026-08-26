import time
import threading
import unittest

from services.model_runtime_registry import ModelRuntimeRegistry


class _LoadedGenerator:
    def __init__(self) -> None:
        self.unloaded = threading.Event()

    def is_loaded(self) -> bool:
        return not self.unloaded.is_set()

    def unload(self) -> None:
        self.unloaded.set()


class ModelRuntimeRegistryIdleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ModelRuntimeRegistry()
        self.generator = _LoadedGenerator()
        self.registry._generators = {"test": self.generator}

    def tearDown(self) -> None:
        self.registry.unload_all()

    def test_unloads_after_idle_timeout(self) -> None:
        self.registry.idle_unload_seconds = 0.02

        self.registry.begin_generation("job-1")
        self.registry.end_generation("job-1")

        self.assertTrue(self.generator.unloaded.wait(1.0))

    def test_new_job_keeps_warm_model_loaded(self) -> None:
        self.registry.idle_unload_seconds = 0.05

        self.registry.begin_generation("job-1")
        self.registry.end_generation("job-1")
        time.sleep(0.01)
        self.registry.begin_generation("job-2")

        time.sleep(0.1)
        self.assertFalse(self.generator.unloaded.is_set())

        self.registry.end_generation("job-2")
        self.assertTrue(self.generator.unloaded.wait(1.0))

    def test_zero_timeout_disables_automatic_unload(self) -> None:
        self.registry.idle_unload_seconds = 0

        self.registry.begin_generation("job-1")
        self.registry.end_generation("job-1")
        time.sleep(0.05)

        self.assertFalse(self.generator.unloaded.is_set())

    def test_management_operations_cannot_unload_a_running_generation(self) -> None:
        other = _LoadedGenerator()
        self.registry._generators = {"test": self.generator, "other": other}
        self.registry._active_id = "test"
        self.registry.begin_generation("job-1")
        try:
            with self.assertRaises(RuntimeError):
                self.registry.switch_model("other")
            with self.assertRaises(RuntimeError):
                self.registry.unload_all()
            self.assertFalse(self.generator.unloaded.is_set())
        finally:
            self.registry.end_generation("job-1")


if __name__ == "__main__":
    unittest.main()
