import unittest
from unittest.mock import patch

from ubiquiti_ops.speedtest import SpeedTestConfig, passes_thresholds, run_speed_test


class SpeedTestTests(unittest.TestCase):
    def test_disabled_speed_test_returns_configuration_error(self):
        result = run_speed_test(SpeedTestConfig(
            enabled=False,
            download_url="https://example.test/down",
            upload_url="",
            upload_enabled=False,
            download_bytes=1000000,
            upload_bytes=100000,
            timeout_seconds=5,
            min_download_mbps=10,
            min_upload_mbps=1,
        ))
        self.assertFalse(result["ok"])
        self.assertIn("disabled", result["error"])

    def test_run_speed_test_uses_download_and_optional_upload(self):
        config = SpeedTestConfig(
            enabled=True,
            download_url="https://example.test/down",
            upload_url="https://example.test/up",
            upload_enabled=True,
            download_bytes=1000000,
            upload_bytes=100000,
            timeout_seconds=5,
            min_download_mbps=10,
            min_upload_mbps=1,
        )
        with patch("ubiquiti_ops.speedtest.measure_download", return_value={
            "download_mbps": 100.0,
            "download_bytes": 1000000,
            "latency_ms": 20,
        }), patch("ubiquiti_ops.speedtest.measure_upload", return_value={
            "upload_mbps": 20.0,
            "upload_bytes": 100000,
        }):
            result = run_speed_test(config)
        self.assertTrue(result["ok"])
        self.assertEqual(result["download_mbps"], 100.0)
        self.assertEqual(result["upload_mbps"], 20.0)

    def test_thresholds_flag_slow_download(self):
        config = SpeedTestConfig(
            enabled=True,
            download_url="https://example.test/down",
            upload_url="",
            upload_enabled=False,
            download_bytes=1000000,
            upload_bytes=100000,
            timeout_seconds=5,
            min_download_mbps=100,
            min_upload_mbps=0,
        )
        self.assertFalse(passes_thresholds({"download_mbps": 25, "error": ""}, config))


if __name__ == "__main__":
    unittest.main()
