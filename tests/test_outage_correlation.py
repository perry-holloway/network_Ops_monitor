import unittest

from ubiquiti_ops.outage_correlation import build_outage_correlations


class OutageCorrelationTests(unittest.TestCase):
    def test_gateway_outage_groups_downstream_failures(self):
        result = build_outage_correlations(
            {
                "latest": [
                    check("device", "192.168.1.1", "UDM Gateway", False),
                    check("device", "192.168.1.43", "USW Flex Mini", False),
                    check("device", "192.168.1.245", "Main AP U6+", False),
                ],
            },
            {
                "devices": [
                    infra("192.168.1.1", "UDM Gateway", "gateway", "offline"),
                    infra("192.168.1.43", "USW Flex Mini", "switch", "offline"),
                    infra("192.168.1.245", "Main AP U6+", "access_point", "offline", "USW Flex Mini"),
                ],
            },
            {"events": []},
            {"sources": {}},
        )

        self.assertEqual(result["status"], "active")
        self.assertEqual(result["incidents"][0]["kind"], "gateway_outage")
        self.assertEqual(result["incidents"][0]["affected_count"], 3)

    def test_switch_outage_links_expected_downstream_ap(self):
        result = build_outage_correlations(
            {
                "latest": [
                    check("device", "192.168.1.1", "UDM Gateway", True),
                    check("device", "192.168.1.43", "USW Flex Mini", False),
                    check("device", "192.168.1.245", "Main AP U6+", False),
                ],
            },
            {
                "devices": [
                    infra("192.168.1.1", "UDM Gateway", "gateway", "online"),
                    infra("192.168.1.43", "USW Flex Mini", "switch", "offline"),
                    infra("192.168.1.245", "Main AP U6+", "access_point", "offline", "USW Flex Mini"),
                ],
            },
            {"events": []},
            {"sources": {}},
        )

        kinds = {incident["kind"] for incident in result["incidents"]}
        self.assertIn("switch_or_uplink_outage", kinds)

    def test_wan_failure_when_gateway_online(self):
        result = build_outage_correlations(
            {
                "latest": [
                    check("device", "192.168.1.1", "UDM Gateway", True),
                    check("wan", "1.1.1.1", "Cloudflare DNS", False),
                ],
            },
            {"devices": [infra("192.168.1.1", "UDM Gateway", "gateway", "online")]},
            {"events": []},
            {"sources": {}},
        )

        self.assertEqual(result["incidents"][0]["kind"], "wan_or_isp_issue")

    def test_control_plane_degraded_is_incident(self):
        result = build_outage_correlations(
            {"latest": []},
            {"devices": []},
            {"events": []},
            {
                "sources": {
                    "local_network_api": {
                        "source": "local_network_api",
                        "status": "failing",
                        "message": "HTTP Error 404: Not Found",
                    },
                },
            },
        )

        self.assertEqual(result["incidents"][0]["kind"], "control_plane_degraded")


def check(kind, target, name, ok):
    return {
        "kind": kind,
        "target": target,
        "name": name,
        "ok": ok,
        "status": "online" if ok else "offline",
    }


def infra(address, name, role, status, expected_uplink=""):
    return {
        "address": address,
        "expected_ip": address,
        "name": name,
        "role": role,
        "status": status,
        "expected_uplink": expected_uplink,
    }


if __name__ == "__main__":
    unittest.main()
