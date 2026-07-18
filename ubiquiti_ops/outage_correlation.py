from __future__ import annotations


def build_outage_correlations(
    check_summary: dict,
    infrastructure: dict,
    timeline: dict,
    control_plane: dict,
) -> dict:
    latest_checks = check_summary.get("latest", []) if isinstance(check_summary, dict) else []
    failed_checks = [item for item in latest_checks if not item.get("ok")]
    infra_devices = infrastructure.get("devices", []) if isinstance(infrastructure, dict) else []
    infra_failed = [item for item in infra_devices if item.get("status") == "offline"]
    timeline_events = timeline.get("events", []) if isinstance(timeline, dict) else []
    control_sources = control_plane.get("sources", {}) if isinstance(control_plane, dict) else {}

    incidents: list[dict] = []
    consumed_targets: set[str] = set()
    gateway = _role_device(infra_devices, "gateway")
    switches = [item for item in infra_devices if item.get("role") == "switch"]
    access_points = [item for item in infra_devices if item.get("role") == "access_point"]

    if gateway and gateway.get("status") == "offline":
        affected = _affected_checks(failed_checks, exclude_kinds=set())
        incidents.append(_incident(
            kind="gateway_outage",
            severity="critical",
            confidence="high",
            title=f"{gateway.get('name')} appears to be the root outage",
            summary="The configured gateway is offline, so downstream infrastructure and local device failures are likely symptoms.",
            root_device=gateway,
            affected=affected,
            evidence=[
                f"Gateway {gateway.get('expected_ip') or gateway.get('address')} is offline.",
                f"{len(affected)} current health check(s) are failing.",
            ],
            recommendation="Start at the UDM: confirm power, WAN/LAN link, UniFi OS health, and whether the Network app is restarting.",
        ))
        consumed_targets.update(item.get("target", "") for item in affected)

    for switch in switches:
        if switch.get("status") != "offline":
            continue
        downstream = [
            item for item in access_points
            if str(item.get("expected_uplink") or "").lower() == str(switch.get("name") or "").lower()
            and item.get("status") in {"offline", "unknown"}
        ]
        affected = _checks_for_devices(failed_checks, [switch, *downstream])
        if not affected and not downstream:
            continue
        incidents.append(_incident(
            kind="switch_or_uplink_outage",
            severity="critical",
            confidence="high" if downstream else "medium",
            title=f"{switch.get('name')} may be taking downstream devices offline",
            summary="A switch is offline and one or more expected downstream APs/devices are also unhealthy.",
            root_device=switch,
            affected=affected,
            evidence=[
                f"Switch {switch.get('name')} is offline.",
                f"{len(downstream)} expected downstream access point(s) are offline or unknown.",
            ],
            recommendation="Check the switch power, uplink cable, PoE source, and the port connecting it back to the UDM.",
        ))
        consumed_targets.update(item.get("target", "") for item in affected)

    for ap in access_points:
        if ap.get("status") != "offline":
            continue
        target = ap.get("expected_ip") or ap.get("address")
        if target in consumed_targets:
            continue
        affected = _checks_for_devices(failed_checks, [ap])
        incidents.append(_incident(
            kind="access_point_outage",
            severity="warning",
            confidence="medium",
            title=f"{ap.get('name')} is offline",
            summary="An access point is unhealthy while the rest of the backbone may still be available.",
            root_device=ap,
            affected=affected,
            evidence=[f"AP {target} is offline."],
            recommendation="Check AP power/PoE, switch port state, cabling, and whether clients near that AP dropped at the same time.",
        ))
        consumed_targets.update(item.get("target", "") for item in affected)

    wan_failures = [item for item in failed_checks if item.get("kind") in {"wan", "dns", "http"}]
    gateway_online = not gateway or gateway.get("status") == "online"
    if wan_failures and gateway_online:
        incidents.append(_incident(
            kind="wan_or_isp_issue",
            severity="critical" if any(item.get("kind") == "wan" for item in wan_failures) else "warning",
            confidence="medium",
            title="WAN or upstream internet issue likely",
            summary="External WAN/DNS/HTTP checks are failing while the local gateway does not appear to be the root cause.",
            root_device={},
            affected=wan_failures,
            evidence=[
                f"{len(wan_failures)} external connectivity check(s) are failing.",
                "Gateway is not currently marked offline.",
            ],
            recommendation="Check ISP modem/ONT, WAN link, DNS settings, and whether the UDM reports WAN failover or packet loss.",
        ))
        consumed_targets.update(item.get("target", "") for item in wan_failures)

    control_failures = [
        event for event in control_sources.values()
        if event.get("status") in {"failing", "degraded"}
    ]
    if control_failures:
        incidents.append(_incident(
            kind="control_plane_degraded",
            severity="warning",
            confidence="medium",
            title="UniFi control plane is degraded",
            summary="Collector or control-plane sources are failing, so missing inventory/client data may be a telemetry issue rather than a network outage.",
            root_device={},
            affected=[],
            evidence=[f"{event.get('source')}: {event.get('message')}" for event in control_failures[:3]],
            recommendation="Review the Control Plane page first. If the Network app is stuck starting, fix that before chasing client/device data gaps.",
        ))

    remaining = [
        item for item in failed_checks
        if item.get("target", "") not in consumed_targets
        and item.get("kind") not in {"wan", "dns", "http"}
    ]
    if remaining:
        incidents.append(_incident(
            kind="ungrouped_failures",
            severity="warning",
            confidence="low",
            title="Some failures do not share an obvious root cause yet",
            summary="These checks are failing but do not currently align with a known gateway, switch, AP, WAN, or control-plane pattern.",
            root_device={},
            affected=remaining,
            evidence=[f"{item.get('name')}: {item.get('status')}" for item in remaining[:5]],
            recommendation="Inspect these individually or add them to INFRASTRUCTURE_DEVICES with roles/uplinks so future outages can be correlated.",
        ))

    recent_disappeared_clients = [
        event for event in timeline_events
        if event.get("kind") == "client_disappeared"
    ]
    return {
        "configured": bool(latest_checks or infra_devices),
        "status": "active" if incidents else "clear",
        "totals": {
            "incidents": len(incidents),
            "critical": len([item for item in incidents if item["severity"] == "critical"]),
            "warning": len([item for item in incidents if item["severity"] == "warning"]),
            "affected_checks": len(failed_checks),
            "recent_client_disappearances": len(recent_disappeared_clients),
        },
        "incidents": incidents,
    }


def _incident(
    *,
    kind: str,
    severity: str,
    confidence: str,
    title: str,
    summary: str,
    root_device: dict,
    affected: list[dict],
    evidence: list[str],
    recommendation: str,
) -> dict:
    return {
        "kind": kind,
        "severity": severity,
        "confidence": confidence,
        "title": title,
        "summary": summary,
        "root_device": root_device,
        "affected": affected,
        "affected_count": len(affected),
        "evidence": evidence,
        "recommendation": recommendation,
    }


def _role_device(devices: list[dict], role: str) -> dict:
    return next((item for item in devices if item.get("role") == role), {})


def _checks_for_devices(failed_checks: list[dict], devices: list[dict]) -> list[dict]:
    targets = {str(item.get("expected_ip") or item.get("address") or "") for item in devices}
    return [item for item in failed_checks if str(item.get("target") or "") in targets]


def _affected_checks(failed_checks: list[dict], exclude_kinds: set[str]) -> list[dict]:
    return [item for item in failed_checks if item.get("kind") not in exclude_kinds]
