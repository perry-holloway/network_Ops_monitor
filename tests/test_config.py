import unittest
from unittest.mock import patch

from ubiquiti_ops.config import (
    Config,
    normalize_mac,
    parse_csv,
    parse_infrastructure_devices,
    parse_named_targets,
    parse_ports,
    parse_trusted_clients,
    parse_watched_devices,
)


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

    def test_parse_csv_accepts_commas_and_semicolons(self):
        self.assertEqual(parse_csv("192.168.1.0/24, 192.168.2.0/24;10.0.0.0/30"), (
            "192.168.1.0/24",
            "192.168.2.0/24",
            "10.0.0.0/30",
        ))

    def test_parse_trusted_clients_normalizes_mac_and_category(self):
        clients = parse_trusted_clients("AA-BB-CC-DD-EE-FF=Work Laptop:work;112233445566=NAS")
        self.assertEqual(len(clients), 2)
        self.assertEqual(clients[0].mac, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(clients[0].name, "Work Laptop")
        self.assertEqual(clients[0].category, "work")
        self.assertEqual(clients[1].mac, "11:22:33:44:55:66")
        self.assertEqual(clients[1].category, "trusted")

    def test_parse_infrastructure_devices(self):
        devices = parse_infrastructure_devices(
            "192.168.1.1=UDM Gateway:gateway;"
            "192.168.1.43=USW Flex Mini:switch:UDM Gateway;"
            "192.168.1.245=Main AP U6+:ap:USW Flex Mini"
        )
        self.assertEqual(len(devices), 3)
        self.assertEqual(devices[0].role, "gateway")
        self.assertEqual(devices[1].expected_uplink, "UDM Gateway")
        self.assertEqual(devices[2].role, "access_point")

    def test_normalize_mac_rejects_invalid_values(self):
        self.assertEqual(normalize_mac("AA-BB-CC-DD-EE-FF"), "aa:bb:cc:dd:ee:ff")
        self.assertEqual(normalize_mac("not-a-mac"), "")

    def test_unifi_api_config_from_env(self):
        with patch.dict("os.environ", {
            "UNIFI_API_ENABLED": "true",
            "UNIFI_API_BASE_URL": "https://192.168.1.1/proxy/network/integration/",
            "UNIFI_API_KEY": "test-key",
            "UNIFI_SITE_ID": "default",
            "UNIFI_VERIFY_TLS": "false",
            "UNIFI_TIMEOUT_SECONDS": "12",
            "UNIFI_LEGACY_STATS_ENABLED": "false",
            "UNIFI_SITE_MANAGER_ENABLED": "true",
            "UNIFI_SITE_MANAGER_BASE_URL": "https://api.ui.com/",
            "UNIFI_SITE_MANAGER_API_KEY": "site-manager-key",
            "UNIFI_WRITE_ACTIONS_ENABLED": "true",
            "UNIFI_WRITE_ACTIONS_CONFIRMATION": "MAINTENANCE",
            "INFRASTRUCTURE_DEVICES": "192.168.1.1=UDM Gateway:gateway",
            "LAN_DISCOVERY_ENABLED": "true",
            "LAN_DISCOVERY_SUBNETS": "192.168.1.0/24;192.168.2.0/24",
            "LAN_DISCOVERY_PORTS": "80,443",
            "LAN_DISCOVERY_MAX_HOSTS": "128",
            "SPEED_TEST_ENABLED": "true",
            "SPEED_TEST_DOWNLOAD_URL": "https://example.test/down",
            "SPEED_TEST_UPLOAD_URL": "https://example.test/up",
            "SPEED_TEST_UPLOAD_ENABLED": "true",
            "SPEED_TEST_DOWNLOAD_BYTES": "5000000",
            "SPEED_TEST_UPLOAD_BYTES": "500000",
            "SPEED_TEST_TIMEOUT_SECONDS": "15",
            "SPEED_TEST_MIN_DOWNLOAD_MBPS": "50.5",
            "SPEED_TEST_MIN_UPLOAD_MBPS": "8.5",
            "TRUSTED_CLIENTS": "AA-BB-CC-DD-EE-FF=Work Laptop:work",
        }):
            config = Config.from_env()
        self.assertTrue(config.unifi_api_enabled)
        self.assertEqual(config.unifi_api_base_url, "https://192.168.1.1/proxy/network/integration")
        self.assertEqual(config.unifi_api_key, "test-key")
        self.assertEqual(config.unifi_site_id, "default")
        self.assertFalse(config.unifi_verify_tls)
        self.assertEqual(config.unifi_timeout_seconds, 12)
        self.assertFalse(config.unifi_legacy_stats_enabled)
        self.assertTrue(config.unifi_site_manager_enabled)
        self.assertEqual(config.unifi_site_manager_base_url, "https://api.ui.com")
        self.assertEqual(config.unifi_site_manager_api_key, "site-manager-key")
        self.assertTrue(config.unifi_write_actions_enabled)
        self.assertEqual(config.unifi_write_actions_confirmation, "MAINTENANCE")
        self.assertEqual(config.infrastructure_devices[0].name, "UDM Gateway")
        self.assertEqual(config.infrastructure_devices[0].role, "gateway")
        self.assertTrue(config.lan_discovery_enabled)
        self.assertEqual(config.lan_discovery_subnets, ("192.168.1.0/24", "192.168.2.0/24"))
        self.assertEqual(config.lan_discovery_ports, (80, 443))
        self.assertEqual(config.lan_discovery_max_hosts, 128)
        self.assertTrue(config.speed_test_enabled)
        self.assertEqual(config.speed_test_download_url, "https://example.test/down")
        self.assertEqual(config.speed_test_upload_url, "https://example.test/up")
        self.assertTrue(config.speed_test_upload_enabled)
        self.assertEqual(config.speed_test_download_bytes, 5000000)
        self.assertEqual(config.speed_test_upload_bytes, 500000)
        self.assertEqual(config.speed_test_timeout_seconds, 15)
        self.assertEqual(config.speed_test_min_download_mbps, 50.5)
        self.assertEqual(config.speed_test_min_upload_mbps, 8.5)
        self.assertEqual(config.trusted_clients[0].mac, "aa:bb:cc:dd:ee:ff")
        self.assertEqual(config.trusted_clients[0].name, "Work Laptop")


if __name__ == "__main__":
    unittest.main()
