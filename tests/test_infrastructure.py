import unittest

from ubiquiti_ops.config import InfrastructureDevice
from ubiquiti_ops.infrastructure import build_infrastructure_summary


class InfrastructureTests(unittest.TestCase):
    def test_build_infrastructure_summary_matches_checks_and_unifi_by_ip(self):
        summary = build_infrastructure_summary(
            (
                InfrastructureDevice("192.168.1.1", "UDM Gateway", "gateway"),
                InfrastructureDevice("192.168.1.43", "USW Flex Mini", "switch", "UDM Gateway"),
                InfrastructureDevice("192.168.1.245", "Main AP U6+", "access_point", "USW Flex Mini"),
            ),
            {
                "latest": [
                    {
                        "kind": "device",
                        "target": "192.168.1.1",
                        "name": "UDM Gateway",
                        "ok": True,
                        "status": "online",
                        "latency_ms": 4,
                        "checked_at": "2026-07-18T10:00:00+00:00",
                    },
                    {
                        "kind": "device",
                        "target": "192.168.1.43",
                        "name": "USW Flex Mini",
                        "ok": False,
                        "status": "offline",
                        "latency_ms": 1000,
                        "checked_at": "2026-07-18T10:00:00+00:00",
                    },
                ],
            },
            {
                "site_manager_devices": [
                    {
                        "ipAddress": "192.168.1.1",
                        "name": "UDM",
                        "model": "UDM",
                        "state": "ONLINE",
                        "firmwareVersion": "5.1.19",
                    },
                    {
                        "ipAddress": "192.168.1.245",
                        "name": "U6+",
                        "model": "U6+",
                        "state": "ONLINE",
                    },
                ],
                "devices": [],
                "traffic_insights": {},
            },
        )

        self.assertTrue(summary["configured"])
        self.assertEqual(summary["totals"]["configured"], 3)
        self.assertEqual(summary["totals"]["online"], 2)
        self.assertEqual(summary["totals"]["offline"], 1)
        self.assertEqual(summary["backbone"]["gateway"]["name"], "UDM Gateway")
        self.assertEqual(summary["backbone"]["switches"][0]["status"], "offline")
        self.assertEqual(summary["backbone"]["access_points"][0]["status"], "online")

    def test_build_infrastructure_summary_flags_ip_mismatch(self):
        summary = build_infrastructure_summary(
            (InfrastructureDevice("192.168.1.245", "Main AP U6+", "access_point"),),
            {"latest": []},
            {
                "site_manager_devices": [
                    {"ipAddress": "192.168.1.250", "name": "Main AP U6+", "state": "ONLINE"},
                ],
                "devices": [],
                "traffic_insights": {},
            },
        )

        self.assertEqual(summary["totals"]["ip_mismatch"], 1)
        self.assertEqual(summary["devices"][0]["observed_ip"], "192.168.1.250")
        self.assertEqual(summary["devices"][0]["status"], "online")


if __name__ == "__main__":
    unittest.main()
