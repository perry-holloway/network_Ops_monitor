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

    def test_unifi_history_summarizes_points_and_trends(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(str(Path(temp) / "ops-console.db"))
            first = {
                "ok": True,
                "checked_at": "2026-07-17T10:00:00+00:00",
                "site_id": "default",
                "latency_ms": 25,
                "devices": [{"id": "udm"}],
                "site_manager_devices": [{"id": "udm", "name": "UDM", "state": "ONLINE"}],
                "clients": [{"id": "laptop", "name": "Work Laptop", "trusted": True, "rxBytes": 120000000}],
                "traffic_insights": {
                    "active_client_count": 1,
                    "total_client_bytes": 120000000,
                    "total_client_rx_bytes": 90000000,
                    "total_client_tx_bytes": 30000000,
                    "total_device_rate_bps": 1000000,
                    "total_device_rx_rate_bps": 700000,
                    "total_device_tx_rate_bps": 300000,
                    "stressed_device_count": 0,
                },
            }
            second = {
                **first,
                "checked_at": "2026-07-17T10:05:00+00:00",
                "latency_ms": 30,
                "site_manager_devices": [{"id": "udm", "name": "UDM", "state": "OFFLINE"}],
                "clients": [
                    {"id": "laptop", "name": "Work Laptop", "trusted": True, "rxBytes": 280000000},
                    {"id": "camera", "name": "Camera", "trusted": False, "rxBytes": 40000000},
                ],
                "traffic_insights": {
                    "active_client_count": 2,
                    "total_client_bytes": 320000000,
                    "total_client_rx_bytes": 260000000,
                    "total_client_tx_bytes": 60000000,
                    "total_device_rate_bps": 7000000,
                    "total_device_rx_rate_bps": 5000000,
                    "total_device_tx_rate_bps": 2000000,
                    "stressed_device_count": 1,
                },
            }
            store.add_unifi_snapshot(first)
            store.add_unifi_snapshot(second)

            history = store.unifi_history(10)

            self.assertTrue(history["configured"])
            self.assertEqual(history["count"], 2)
            self.assertEqual(history["points"][-1]["client_count"], 2)
            self.assertEqual(history["points"][-1]["offline_device_count"], 1)
            self.assertEqual(history["points"][-1]["untrusted_client_count"], 1)
            self.assertEqual(history["trends"]["traffic_delta_bytes"], 200000000)
            self.assertEqual(history["trends"]["device_rate_delta_bps"], 6000000)
            self.assertTrue(any(alert["kind"] == "traffic_spike" for alert in history["trends"]["alerts"]))
            self.assertTrue(any(alert["kind"] == "offline_devices" for alert in history["trends"]["alerts"]))

    def test_entity_overrides_apply_to_latest_unifi_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(str(Path(temp) / "ops-console.db"))
            store.add_unifi_snapshot({
                "ok": True,
                "checked_at": "2026-07-17T10:00:00+00:00",
                "site_id": "default",
                "latency_ms": 25,
                "devices": [{"id": "device-1", "name": "UDM", "ipAddress": "192.168.1.1"}],
                "site_manager_devices": [],
                "clients": [{"id": "client-1", "name": "Unknown", "trusted": False}],
                "traffic_insights": {
                    "top_devices": [{"id": "device-1", "name": "UDM"}],
                    "top_clients": [{"id": "client-1", "name": "Unknown"}],
                },
            })

            device_override = store.update_entity_override("device", "device-1", {
                "display_name": "Dream Machine",
                "owner": "Network Team",
                "location": "Office",
                "category": "Gateway",
                "notes": "Primary router",
            })
            client_override = store.update_entity_override("client", "client-1", {
                "display_name": "Living Room Camera",
                "trusted": "true",
                "category": "Camera",
                "notes": "Known IoT client",
            })
            snapshot = store.latest_unifi_snapshot()

            self.assertEqual(device_override["display_name"], "Dream Machine")
            self.assertEqual(client_override["trusted"], "true")
            self.assertEqual(snapshot["devices"][0]["localDisplayName"], "Dream Machine")
            self.assertEqual(snapshot["devices"][0]["owner"], "Network Team")
            self.assertEqual(snapshot["clients"][0]["localDisplayName"], "Living Room Camera")
            self.assertTrue(snapshot["clients"][0]["trusted"])
            self.assertEqual(snapshot["clients"][0]["trustedCategory"], "Camera")
            self.assertEqual(snapshot["traffic_insights"]["top_devices"][0]["localDisplayName"], "Dream Machine")
            self.assertEqual(snapshot["traffic_insights"]["top_clients"][0]["localDisplayName"], "Living Room Camera")


if __name__ == "__main__":
    unittest.main()
