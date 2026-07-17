import tempfile
import unittest
from pathlib import Path

from ubiquiti_ops.store import Store


class StoreTests(unittest.TestCase):
    def test_summary_returns_latest_per_target(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(str(Path(temp) / "ops-console.db"))
            store.add_result({
                "kind": "device",
                "target": "192.168.1.1",
                "name": "UDM",
                "sensitivity": "critical",
                "status": "online",
                "ok": True,
                "latency_ms": 5,
                "checked_at": "2026-07-17T10:00:00+00:00",
                "details": {"ping": {"ok": True}},
            })
            store.add_result({
                "kind": "device",
                "target": "192.168.1.1",
                "name": "UDM",
                "sensitivity": "critical",
                "status": "offline",
                "ok": False,
                "latency_ms": 1000,
                "checked_at": "2026-07-17T10:01:00+00:00",
                "details": {"ping": {"ok": False}},
            })
            summary = store.summary()
            self.assertEqual(summary["totals"]["checks"], 1)
            self.assertEqual(summary["totals"]["down"], 1)
            self.assertEqual(summary["totals"]["critical_down"], 1)

    def test_empty_summary_is_valid_before_first_checks_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(str(Path(temp) / "ops-console.db"))
            summary = store.summary()
            self.assertEqual(summary["totals"]["checks"], 0)
            self.assertEqual(summary["latest"], [])
            self.assertEqual(summary["critical_down"], [])

    def test_discovery_snapshot_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(str(Path(temp) / "ops-console.db"))
            store.add_discovery_snapshot({
                "ok": True,
                "checked_at": "2026-07-17T10:00:00+00:00",
                "subnets": ["192.168.1.0/24"],
                "ports": [80, 443],
                "scanned_hosts": 254,
                "latency_ms": 1200,
                "devices": [{"ip": "192.168.1.1", "mac": "aa:bb:cc:dd:ee:ff"}],
                "errors": [],
            })
            snapshot = store.latest_discovery_snapshot()
            self.assertTrue(snapshot["configured"])
            self.assertEqual(snapshot["device_count"], 1)
            self.assertEqual(snapshot["devices"][0]["ip"], "192.168.1.1")

    def test_speed_test_snapshot_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(str(Path(temp) / "ops-console.db"))
            store.add_speed_test_snapshot({
                "ok": True,
                "checked_at": "2026-07-17T10:00:00+00:00",
                "download_mbps": 250.25,
                "upload_mbps": 30.5,
                "latency_ms": 18,
                "download_bytes": 10000000,
                "upload_bytes": 1000000,
                "duration_ms": 900,
                "upload_enabled": True,
                "thresholds": {"min_download_mbps": 100, "min_upload_mbps": 10},
                "error": "",
            })
            snapshot = store.latest_speed_test_snapshot()
            self.assertTrue(snapshot["configured"])
            self.assertTrue(snapshot["ok"])
            self.assertEqual(snapshot["download_mbps"], 250.25)
            self.assertEqual(snapshot["upload_mbps"], 30.5)


if __name__ == "__main__":
    unittest.main()
