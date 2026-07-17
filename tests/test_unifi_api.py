import unittest

from ubiquiti_ops.unifi_api import build_traffic_insights, infer_site_id


class UniFiApiTests(unittest.TestCase):
    def test_infer_site_id_prefers_id(self):
        self.assertEqual(infer_site_id([{"id": "site-id", "internalReference": "default"}]), "site-id")

    def test_build_traffic_insights_uses_clients_and_device_stats(self):
        devices = [{
            "id": "dev1",
            "name": "UDM Gateway",
            "ipAddress": "192.168.1.1",
            "macAddress": "aa:bb:cc:dd:ee:ff",
            "model": "UDM",
            "state": "ONLINE",
        }]
        clients = [{
            "id": "client1",
            "name": "Laptop",
            "ipAddress": "192.168.1.50",
            "macAddress": "11:22:33:44:55:66",
            "rxBytes": 1000,
            "txBytes": 250,
        }]
        stats = [{
            "deviceId": "dev1",
            "uplink": {
                "rxRateBps": 500,
                "txRateBps": 100,
            },
        }]
        insights = build_traffic_insights(devices, clients, stats, {})
        self.assertEqual(insights["client_count"], 1)
        self.assertEqual(insights["device_count"], 1)
        self.assertEqual(insights["total_client_rx_bytes"], 1000)
        self.assertEqual(insights["total_client_tx_bytes"], 250)
        self.assertEqual(insights["top_clients"][0]["name"], "Laptop")
        self.assertEqual(insights["top_devices"][0]["total_rate_bps"], 600)


if __name__ == "__main__":
    unittest.main()
