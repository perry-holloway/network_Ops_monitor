import unittest
from unittest.mock import patch

from ubiquiti_ops.discovery import DiscoveryConfig, discover_lan, expand_targets, parse_neighbors


class DiscoveryTests(unittest.TestCase):
    def test_expand_targets_respects_max_hosts(self):
        self.assertEqual(expand_targets(("192.168.1.0/30", "192.168.2.0/30"), 3), [
            "192.168.1.1",
            "192.168.1.2",
            "192.168.2.1",
        ])

    def test_parse_neighbors_handles_ip_neigh_and_arp_output(self):
        raw = """
192.168.1.10 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE
? (192.168.1.11) at 11-22-33-44-55-66 on eth0
"""
        neighbors = parse_neighbors(raw)
        self.assertEqual(neighbors["192.168.1.10"]["mac"], "aa:bb:cc:dd:ee:ff")
        self.assertEqual(neighbors["192.168.1.10"]["state"], "reachable")
        self.assertEqual(neighbors["192.168.1.11"]["mac"], "11:22:33:44:55:66")

    def test_discover_lan_records_reachable_hosts(self):
        def fake_probe(address, ports):
            return {
                "ip": address,
                "hostname": "gateway" if address.endswith(".1") else "",
                "online": address.endswith(".1"),
                "latency_ms": 4,
                "ping": True,
                "open_ports": [],
                "mac": "",
                "neighbor_state": "",
            }

        with patch("ubiquiti_ops.discovery.probe_host", side_effect=fake_probe), \
             patch("ubiquiti_ops.discovery.read_neighbor_table", return_value={
                 "192.168.1.1": {"mac": "aa:bb:cc:dd:ee:ff", "state": "reachable"},
             }):
            snapshot = discover_lan(DiscoveryConfig(
                subnets=("192.168.1.0/30",),
                ports=(80, 443),
                max_hosts=10,
                workers=2,
            ))

        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["scanned_hosts"], 2)
        self.assertEqual(snapshot["device_count"], 1)
        self.assertEqual(snapshot["devices"][0]["ip"], "192.168.1.1")
        self.assertEqual(snapshot["devices"][0]["mac"], "aa:bb:cc:dd:ee:ff")


if __name__ == "__main__":
    unittest.main()
