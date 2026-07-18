import unittest

from ubiquiti_ops.control_plane import classify_error, snapshot_events


class ControlPlaneTests(unittest.TestCase):
    def test_classify_common_unifi_failures(self):
        self.assertEqual(classify_error("HTTP Error 401: Unauthorized")["kind"], "api_unauthorized")
        self.assertEqual(classify_error("HTTP Error 403: Forbidden")["kind"], "api_forbidden")
        self.assertEqual(classify_error("HTTP Error 404: Not Found")["kind"], "endpoint_not_found")
        self.assertEqual(
            classify_error("The underlying connection was closed unexpectedly")["kind"],
            "connection_interrupted",
        )

    def test_snapshot_events_include_local_and_site_manager_sources(self):
        events = snapshot_events({
            "ok": True,
            "checked_at": "2026-07-18T10:00:00+00:00",
            "network_error": "HTTP Error 404: Not Found",
            "site_manager_error": "",
            "error": "HTTP Error 404: Not Found",
            "site_manager_devices": [{"id": "udm"}],
            "site_manager_sites": [{"id": "site-1"}],
            "devices": [],
            "clients": [],
        })
        by_source = {event["source"]: event for event in events}

        self.assertEqual(by_source["unifi_collector"]["status"], "degraded")
        self.assertEqual(by_source["local_network_api"]["kind"], "endpoint_not_found")
        self.assertEqual(by_source["local_network_api"]["status"], "failing")
        self.assertEqual(by_source["site_manager_api"]["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
