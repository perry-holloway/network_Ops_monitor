import unittest

from ubiquiti_ops.unifi_api import (
    UniFiApiClient,
    UniFiApiConfig,
    annotate_trusted_clients,
    build_traffic_insights,
    infer_site_id,
    resolve_local_site_id,
)
from ubiquiti_ops.config import TrustedClient


class UniFiApiTests(unittest.TestCase):
    def test_infer_site_id_prefers_id(self):
        self.assertEqual(infer_site_id([{"id": "site-id", "internalReference": "default"}]), "site-id")

    def test_resolve_local_site_id_replaces_unknown_configured_id(self):
        self.assertEqual(
            resolve_local_site_id(
                "site-manager-id",
                [{"id": "local-site-uuid", "internalReference": "default", "name": "Default"}],
            ),
            "local-site-uuid",
        )

    def test_resolve_local_site_id_accepts_default_alias(self):
        self.assertEqual(
            resolve_local_site_id(
                "default",
                [{"id": "local-site-uuid", "internalReference": "default", "name": "Default"}],
            ),
            "local-site-uuid",
        )

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
            "rssi": -61,
            "ssid": "HomeWiFi",
        }]
        stats = [{
            "deviceId": "dev1",
            "uplink": {
                "rxRateBps": 500,
                "txRateBps": 100,
                "state": "active",
            },
            "temperatureC": 43,
            "memoryUtilizationPct": 52,
            "ports": [{
                "idx": 1,
                "name": "Port 1",
                "poeMode": "auto",
                "speedMbps": 1000,
            }],
        }]
        insights = build_traffic_insights(devices, clients, stats, {})
        self.assertEqual(insights["client_count"], 1)
        self.assertEqual(insights["device_count"], 1)
        self.assertEqual(insights["total_client_rx_bytes"], 1000)
        self.assertEqual(insights["total_client_tx_bytes"], 250)
        self.assertEqual(insights["top_clients"][0]["name"], "Laptop")
        self.assertEqual(insights["top_clients"][0]["signal_dbm"], -61)
        self.assertEqual(insights["top_clients"][0]["ssid"], "HomeWiFi")
        self.assertEqual(insights["top_devices"][0]["total_rate_bps"], 600)
        self.assertEqual(insights["top_devices"][0]["temperature_c"], 43)
        self.assertEqual(insights["top_devices"][0]["memory_pct"], 52)
        self.assertEqual(insights["top_devices"][0]["uplink_state"], "active")
        self.assertEqual(insights["top_devices"][0]["ports"][0]["poe"], "auto")

    def test_annotate_trusted_clients_marks_known_macs(self):
        clients = [
            {"id": "client-1", "macAddress": "AA-BB-CC-DD-EE-FF", "name": "Unknown"},
            {"id": "client-2", "macAddress": "11:22:33:44:55:66", "name": "Phone"},
        ]

        annotated = annotate_trusted_clients(clients, (
            TrustedClient("aa:bb:cc:dd:ee:ff", "Work Laptop", "work"),
        ))

        self.assertTrue(annotated[0]["trusted"])
        self.assertEqual(annotated[0]["trustedName"], "Work Laptop")
        self.assertEqual(annotated[0]["trustedCategory"], "work")
        self.assertFalse(annotated[1]["trusted"])
        self.assertEqual(annotated[1]["trustedCategory"], "untrusted")

    def test_paged_url_accepts_list_payloads(self):
        client = UniFiApiClient(UniFiApiConfig(base_url="https://example.local", api_key="key"))
        calls = []

        def fake_request(url, api_key=None, verify_tls=None):
            calls.append(url)
            return [{"id": "device-1"}]

        client._request_json = fake_request
        devices = client._paged_url("https://api.ui.com/v1/devices")
        self.assertEqual(devices, [{"id": "device-1"}])
        self.assertEqual(len(calls), 1)

    def test_collect_keeps_site_manager_data_when_network_api_fails(self):
        client = UniFiApiClient(UniFiApiConfig(
            base_url="https://example.local",
            api_key="key",
            site_manager_enabled=True,
            site_manager_api_key="key",
        ))
        client.list_sites = lambda: (_ for _ in ()).throw(Exception("HTTP Error 403: Forbidden"))
        client.collect_site_manager = lambda: {
            "sites": [],
            "hosts": [],
            "devices": [{"id": "gateway-1", "name": "UDM"}],
            "isp_metrics": [],
            "errors": {},
        }

        snapshot = client.collect()

        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["network_error"], "HTTP Error 403: Forbidden")
        self.assertEqual(snapshot["site_manager_devices"], [{"id": "gateway-1", "name": "UDM"}])

    def test_collect_supports_site_manager_only_key(self):
        client = UniFiApiClient(UniFiApiConfig(
            base_url="https://example.local",
            api_key="",
            site_manager_enabled=True,
            site_manager_api_key="site-manager-key",
        ))
        client.collect_site_manager = lambda: {
            "sites": [{"siteId": "site-1", "hostId": "host-1"}],
            "hosts": [{"id": "host-1", "type": "console"}],
            "devices": [{"id": "gateway-1", "name": "UDM"}],
            "isp_metrics": [{"siteId": "site-1"}],
            "errors": {},
        }

        snapshot = client.collect()

        self.assertTrue(snapshot["ok"])
        self.assertIn("local Network API collection skipped", snapshot["network_error"])
        self.assertEqual(snapshot["site_manager_sites"][0]["siteId"], "site-1")
        self.assertEqual(snapshot["site_manager_hosts"][0]["id"], "host-1")
        self.assertEqual(snapshot["site_manager_devices"][0]["name"], "UDM")

    def test_configured_site_id_continues_when_site_listing_404s(self):
        client = UniFiApiClient(UniFiApiConfig(
            base_url="https://example.local/proxy/network/integration",
            api_key="network-key",
            site_id="site-1",
            legacy_stats_enabled=False,
        ))
        client.list_sites = lambda: (_ for _ in ()).throw(Exception("HTTP Error 404: Not Found"))
        client.list_devices = lambda site_id: [{"id": "gateway-1", "name": "UDM"}]
        client.list_clients = lambda site_id: [{"id": "client-1", "name": "Laptop"}]
        client.collect_device_statistics = lambda site_id, devices: []

        snapshot = client.collect()

        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["site_id"], "site-1")
        self.assertEqual(snapshot["devices"][0]["name"], "UDM")
        self.assertEqual(snapshot["clients"][0]["name"], "Laptop")

    def test_collect_annotates_trusted_clients(self):
        client = UniFiApiClient(UniFiApiConfig(
            base_url="https://example.local/proxy/network/integration",
            api_key="network-key",
            site_id="site-1",
            legacy_stats_enabled=False,
            trusted_clients=(TrustedClient("aa:bb:cc:dd:ee:ff", "Work Laptop", "trusted"),),
        ))
        client.list_sites = lambda: [{"id": "site-1"}]
        client.list_devices = lambda site_id: []
        client.list_clients = lambda site_id: [{"id": "client-1", "macAddress": "AA-BB-CC-DD-EE-FF"}]
        client.collect_device_statistics = lambda site_id, devices: []

        snapshot = client.collect()

        self.assertTrue(snapshot["clients"][0]["trusted"])
        self.assertEqual(snapshot["clients"][0]["trustedName"], "Work Laptop")

    def test_network_devices_fall_back_to_top_level_endpoint_on_404(self):
        client = UniFiApiClient(UniFiApiConfig(
            base_url="https://example.local/proxy/network/integration",
            api_key="network-key",
            site_id="site-1",
        ))
        calls = []

        def fake_paged(path, limit=200):
            calls.append(path)
            if path == "/v1/sites/site-1/devices":
                raise Exception("HTTP Error 404: Not Found")
            return [{"id": "gateway-1"}]

        client._paged = fake_paged
        devices = client.list_devices("site-1")

        self.assertEqual(devices, [{"id": "gateway-1"}])
        self.assertEqual(calls, ["/v1/sites/site-1/devices", "/v1/devices"])

    def test_network_devices_fall_back_to_top_level_endpoint_on_400(self):
        client = UniFiApiClient(UniFiApiConfig(
            base_url="https://example.local/proxy/network/integration",
            api_key="network-key",
            site_id="site-1",
        ))
        calls = []

        def fake_paged(path, limit=200):
            calls.append(path)
            if path == "/v1/sites/site-1/devices":
                raise Exception("HTTP Error 400:")
            return [{"id": "gateway-1"}]

        client._paged = fake_paged
        devices = client.list_devices("site-1")

        self.assertEqual(devices, [{"id": "gateway-1"}])
        self.assertEqual(calls, ["/v1/sites/site-1/devices", "/v1/devices"])

    def test_network_clients_fall_back_to_top_level_endpoint_on_404(self):
        client = UniFiApiClient(UniFiApiConfig(
            base_url="https://example.local/proxy/network/integration",
            api_key="network-key",
            site_id="site-1",
        ))
        calls = []

        def fake_paged(path, limit=200):
            calls.append(path)
            if path == "/v1/sites/site-1/clients":
                raise Exception("HTTP Error 404: Not Found")
            return [{"id": "client-1"}]

        client._paged = fake_paged
        clients = client.list_clients("site-1")

        self.assertEqual(clients, [{"id": "client-1"}])
        self.assertEqual(calls, ["/v1/sites/site-1/clients", "/v1/clients"])

    def test_site_manager_paging_uses_next_token(self):
        client = UniFiApiClient(UniFiApiConfig(
            base_url="https://example.local",
            api_key="",
            site_manager_enabled=True,
            site_manager_api_key="site-manager-key",
        ))
        calls = []

        def fake_request(url, api_key=None, verify_tls=None):
            calls.append(url)
            self.assertEqual(api_key, "site-manager-key")
            self.assertTrue(verify_tls)
            if "nextToken=next-page" in url:
                return {"data": [{"id": "host-2"}]}
            return {"data": [{"id": "host-1"}], "nextToken": "next-page"}

        client._request_json = fake_request
        hosts = client.list_site_manager_hosts()

        self.assertEqual(hosts, [{"id": "host-1"}, {"id": "host-2"}])
        self.assertEqual(len(calls), 2)

    def test_site_manager_isp_metrics_404_is_not_an_error(self):
        client = UniFiApiClient(UniFiApiConfig(
            base_url="https://example.local",
            api_key="",
            site_manager_enabled=True,
            site_manager_api_key="site-manager-key",
        ))
        client.list_site_manager_sites = lambda: [{"siteId": "site-1"}]
        client.list_site_manager_hosts = lambda: [{"id": "host-1"}]
        client.list_site_manager_devices = lambda: [{"id": "gateway-1"}]
        client.list_site_manager_isp_metrics = lambda: (_ for _ in ()).throw(Exception("HTTP Error 404: Not Found"))

        snapshot = client.collect_site_manager()

        self.assertEqual(snapshot["isp_metrics"], [])
        self.assertEqual(snapshot["errors"], {})


if __name__ == "__main__":
    unittest.main()
