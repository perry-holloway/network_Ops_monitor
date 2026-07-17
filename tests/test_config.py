import unittest

from ubiquiti_ops.config import parse_named_targets, parse_ports, parse_watched_devices


class ConfigTests(unittest.TestCase):
    def test_parse_watched_devices(self):
        devices = parse_watched_devices("192.168.1.1=UDM Gateway:critical:443,80;192.168.1.20=NAS")
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].address, "192.168.1.1")
        self.assertEqual(devices[0].name, "UDM Gateway")
        self.assertEqual(devices[0].sensitivity, "critical")
        self.assertEqual(devices[0].ports, (443, 80))
        self.assertEqual(devices[1].sensitivity, "normal")

    def test_parse_ports_ignores_invalid_values(self):
        self.assertEqual(parse_ports("443,abc,0,65536,80"), (443, 80))

    def test_parse_named_targets(self):
        targets = parse_named_targets("1.1.1.1=Cloudflare DNS;8.8.8.8")
        self.assertEqual(targets[0].name, "Cloudflare DNS")
        self.assertEqual(targets[1].name, "8.8.8.8")


if __name__ == "__main__":
    unittest.main()

