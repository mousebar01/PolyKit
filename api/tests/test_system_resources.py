import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import system_resources


class _FakePsutil:
    cpu_calls = 0

    @classmethod
    def cpu_percent(cls, interval=None):
        cls.cpu_calls += 1
        return 42.5

    @staticmethod
    def cpu_count(logical=True):
        return 16

    @staticmethod
    def virtual_memory():
        return SimpleNamespace(used=12_000, available=20_000, total=32_000)


class _FakeNvml:
    NVML_TEMPERATURE_GPU = 0

    @staticmethod
    def nvmlInit():
        return None

    @staticmethod
    def nvmlDeviceGetCount():
        return 1

    @staticmethod
    def nvmlDeviceGetHandleByIndex(index):
        return f"gpu-{index}"

    @staticmethod
    def nvmlDeviceGetName(handle):
        return b"Test GPU"

    @staticmethod
    def nvmlDeviceGetMemoryInfo(handle):
        return SimpleNamespace(used=8_000, total=16_000)

    @staticmethod
    def nvmlDeviceGetUtilizationRates(handle):
        return SimpleNamespace(gpu=77)

    @staticmethod
    def nvmlDeviceGetTemperature(handle, sensor):
        return 63


class SystemResourceSamplerTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakePsutil.cpu_calls = 0

    def test_snapshot_reports_cpu_memory_and_gpu(self) -> None:
        now = [100.0]
        with patch.object(system_resources, "psutil", _FakePsutil), patch.object(system_resources, "pynvml", _FakeNvml):
            sampler = system_resources.SystemResourceSampler(
                monotonic=lambda: now[0],
                wall_time=lambda: 1_700_000_000.0,
            )
            snapshot = sampler.snapshot()

        self.assertEqual(snapshot["cpu"], {"usage": 42.5, "cores": 16})
        self.assertEqual(snapshot["memory"], {"used": 12_000, "available": 20_000, "total": 32_000})
        self.assertEqual(snapshot["sampled_at"], 1_700_000_000.0)
        self.assertEqual(snapshot["cache_seconds"], 2.0)
        self.assertEqual(snapshot["gpus"], [{
            "index": 0,
            "name": "Test GPU",
            "usage": 77,
            "memory": {"used": 8_000, "total": 16_000},
            "temperature": 63,
        }])

    def test_snapshot_is_reused_inside_cache_window(self) -> None:
        now = [100.0]
        with patch.object(system_resources, "psutil", _FakePsutil), patch.object(system_resources, "pynvml", None):
            sampler = system_resources.SystemResourceSampler(
                monotonic=lambda: now[0],
                wall_time=lambda: now[0],
            )
            first = sampler.snapshot()
            now[0] = 101.9
            second = sampler.snapshot()
            now[0] = 102.1
            third = sampler.snapshot()

        # One prime call + one call per actual sample. The 101.9s request is cached.
        self.assertEqual(_FakePsutil.cpu_calls, 3)
        self.assertEqual(first["sampled_at"], second["sampled_at"])
        self.assertEqual(third["sampled_at"], 102.1)

    def test_missing_nvml_is_a_valid_cpu_only_snapshot(self) -> None:
        with patch.object(system_resources, "psutil", _FakePsutil), patch.object(system_resources, "pynvml", None):
            sampler = system_resources.SystemResourceSampler()
            snapshot = sampler.snapshot()

        self.assertEqual(snapshot["gpus"], [])
        self.assertEqual(snapshot["cpu"]["usage"], 42.5)


if __name__ == "__main__":
    unittest.main()
