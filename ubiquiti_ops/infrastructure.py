from __future__ import annotations

from .config import InfrastructureDevice


ROLE_LABELS = {
    "gateway": "Gateway",
    "switch": "Switch",
    "access_point": "Access Point",
    "server": "Server",
    "storage": "Storage",
    "other": "Infrastructure",
}


def build_infrastructure_summary(
    configured_devices: tuple[InfrastructureDevice, ...],
    check_summary: dict,
    unifi_snapshot: dict,
) -> dict:
    checks = check_summary.get("latest", []) if isinstance(check_summary, dict) else []
    unifi_devices = [
        *(unifi_snapshot.get("site_manager_devices", []) or []),
        *(unifi_snapshot.get("devices", []) or []),
        *((unifi_snapshot.get("traffic_insights", {}) or {}).get("top_devices", []) or []),
    ]
    check_by_ip = {
        str(item.get("target") or ""): item
        for item in checks
        if item.get("kind") == "device"
    }
    unifi_by_ip = {
        _device_ip(item): item
        for item in unifi_devices
        if _device_ip(item)
    }
    unifi_by_name = {
        _normalize_name(_device_name(item)): item
        for item in unifi_devices
        if _device_name(item)
    }

    devices = []
    for configured in configured_devices:
        check = check_by_ip.get(configured.address, {})
        unifi = unifi_by_ip.get(configured.address, {}) or unifi_by_name.get(_normalize_name(configured.name), {})
        observed_ip = _device_ip(unifi) or check.get("target") or ""
        status = _status(configured, check, unifi)
        devices.append({
            "address": configured.address,
            "expected_ip": configured.address,
            "observed_ip": observed_ip,
            "name": configured.name,
            "role": configured.role,
            "role_label": ROLE_LABELS.get(configured.role, "Infrastructure"),
            "expected_uplink": configured.expected_uplink,
            "status": status,
            "ok": status == "online",
            "ip_mismatch": bool(observed_ip and observed_ip != configured.address),
            "latency_ms": int(check.get("latency_ms") or 0),
            "checked_at": check.get("checked_at", ""),
            "check_status": check.get("status", ""),
            "unifi_state": str(unifi.get("state") or unifi.get("status") or unifi.get("stateText") or ""),
            "model": str(unifi.get("model") or unifi.get("type") or ""),
            "firmware": str(unifi.get("firmwareVersion") or unifi.get("version") or ""),
            "uplink_state": str(unifi.get("uplink_state") or unifi.get("uplinkDeviceId") or ""),
            "source": "check+unifi" if check and unifi else "check" if check else "unifi" if unifi else "configured",
        })

    totals = {
        "configured": len(devices),
        "online": len([item for item in devices if item["status"] == "online"]),
        "offline": len([item for item in devices if item["status"] == "offline"]),
        "warning": len([item for item in devices if item["status"] in {"warning", "unknown"} or item["ip_mismatch"]]),
        "ip_mismatch": len([item for item in devices if item["ip_mismatch"]]),
    }
    return {
        "configured": bool(devices),
        "totals": totals,
        "roles": _role_counts(devices),
        "devices": devices,
        "backbone": _backbone(devices),
    }


def _status(configured: InfrastructureDevice, check: dict, unifi: dict) -> str:
    if check:
        return "online" if check.get("ok") else "offline"
    unifi_state = str(unifi.get("state") or unifi.get("status") or unifi.get("stateText") or "").lower()
    if unifi_state in {"online", "connected", "active", "up"}:
        return "online"
    if unifi_state in {"offline", "disconnected", "down"}:
        return "offline"
    return "unknown"


def _device_ip(device: dict) -> str:
    return str(device.get("ipAddress") or device.get("ip") or "")


def _device_name(device: dict) -> str:
    return str(device.get("name") or device.get("displayName") or device.get("hostname") or "")


def _normalize_name(name: str) -> str:
    return "".join(char for char in str(name or "").lower() if char.isalnum())


def _role_counts(devices: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for device in devices:
        role = device.get("role_label") or device.get("role") or "Infrastructure"
        counts[role] = counts.get(role, 0) + 1
    return counts


def _backbone(devices: list[dict]) -> dict:
    gateway = next((item for item in devices if item["role"] == "gateway"), {})
    switches = [item for item in devices if item["role"] == "switch"]
    access_points = [item for item in devices if item["role"] == "access_point"]
    other = [item for item in devices if item["role"] not in {"gateway", "switch", "access_point"}]
    return {
        "gateway": gateway,
        "switches": switches,
        "access_points": access_points,
        "other": other,
    }
