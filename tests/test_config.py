import unittest
from unittest.mock import patch

from ubiquiti_ops.config import Config, parse_named_targets, parse_ports, parse_watched_devices


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

    def test_unifi_api_config_from_env(self):
        with patch.dict("os.environ", {
            "UNIFI_API_ENABLED": "true",
            "UNIFI_API_BASE_URL": "https://192.168.1.1/proxy/network/integration/",
            "UNIFI_API_KEY": "test-key",
            "UNIFI_SITE_ID": "default",
            "UNIFI_VERIFY_TLS": "false",
            "UNIFI_TIMEOUT_SECONDS": "12",
            "UNIFI_LEGACY_STATS_ENABLED": "false",
        }):
            config = Config.from_env()
        self.assertTrue(config.unifi_api_enabled)
        self.assertEqual(config.unifi_api_base_url, "https://192.168.1.1/proxy/network/integration")
        self.assertEqual(config.unifi_api_key, "test-key")
        self.assertEqual(config.unifi_site_id, "default")
        self.assertFalse(config.unifi_verify_tls)
        self.assertEqual(config.unifi_timeout_seconds, 12)
        self.assertFalse(config.unifi_legacy_stats_enabled)


if __name__ == "__main__":
    unittest.main()
