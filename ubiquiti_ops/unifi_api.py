from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import ssl
import time
from typing import Any
from urllib import parse, request

from .config import Config

LOG = logging.getLogger("ubiquiti-ops.unifi-api")


@dataclass(frozen=True)
class UniFiApiConfig:
    base_url: str
    api_key: str
    site_id: str = ""
    verify_tls: bool = False
    timeout_seconds: int = 10
    legacy_stats_enabled: bool = True


class UniFiApiClient:
    def __init__(self, config: UniFiApiConfig):
        self.config = config
        self._ssl_context = None if config.verify_tls else ssl._create_unverified_context()

    @classmethod
    def from_app_config(cls, config: Config) -> "UniFiApiClient":
        return cls(UniFiApiConfig(
            base_url=config.unifi_api_base_url,
            api_key=config.unifi_api_key,
            site_id=config.unifi_site_id,
            verify_tls=config.unifi_verify_tls,
            timeout_seconds=config.unifi_timeout_seconds,
            legacy_stats_enabled=config.unifi_legacy_stats_enabled,
        ))

    def collect(self) -> dict:
        started = time.perf_counter()
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.config.api_key:
            return error_snapshot("UniFi API key is not configured", checked_at, started)

        try:
            sites = self.list_sites()
            site_id = self.config.site_id or infer_site_id(sites)
            if not site_id:
                return error_snapshot("No UniFi site was found. Set UNIFI_SITE_ID in .env.", checked_at, started)

            devices = self.list_devices(site_id)
            clients = self.list_clients(site_id)
            device_stats = self.collect_device_statistics(site_id, devices)
            legacy_stats = self.collect_legacy_stats(site_id) if self.config.legacy_stats_enabled else {}
            insights = build_traffic_insights(devices, clients, device_stats, legacy_stats)
            return {
                "ok": True,
                "checked_at": checked_at,
                "latency_ms": elapsed_ms(started),
                "site_id": site_id,
                "sites": sites,
                "devices": devices,
                "clients": clients,
                "device_statistics": device_stats,
                "legacy_stats": legacy_stats,
                "traffic_insights": insights,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            LOG.exception("UniFi API collection failed")
            return error_snapshot(str(exc), checked_at, started)

    def list_sites(self) -> list[dict]:
        return self._paged("/v1/sites")

    def list_devices(self, site_id: str) -> list[dict]:
        return self._paged(f"/v1/sites/{parse.quote(site_id)}/devices")

    def list_clients(self, site_id: str) -> list[dict]:
        return self._paged(f"/v1/sites/{parse.quote(site_id)}/clients")

    def collect_device_statistics(self, site_id: str, devices: list[dict]) -> list[dict]:
        stats: list[dict] = []
        for device in devices:
            device_id = str(device.get("id") or "")
            if not device_id:
                continue
            try:
                latest = self._get(f"/v1/sites/{parse.quote(site_id)}/devices/{parse.quote(device_id)}/statistics/latest")
                latest["deviceId"] = device_id
                latest["deviceName"] = device.get("name") or device.get("model") or device.get("macAddress") or device_id
                stats.append(latest)
            except Exception as exc:  # noqa: BLE001
                stats.append({
                    "deviceId": device_id,
                    "deviceName": device.get("name") or device_id,
                    "error": str(exc),
                })
        return stats

    def collect_legacy_stats(self, site_id: str) -> dict:
        legacy_base = self.config.base_url.replace("/integration", "")
        output: dict[str, Any] = {}
        for key, path in {
            "devices": f"/api/s/{site_id}/stat/device",
            "clients": f"/api/s/{site_id}/stat/sta",
        }.items():
            try:
                output[key] = self._request_json(f"{legacy_base}{path}").get("data", [])
            except Exception as exc:  # noqa: BLE001
                output[f"{key}_error"] = str(exc)
        return output

    def _paged(self, path: str, limit: int = 200) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while True:
            separator = "&" if "?" in path else "?"
            payload = self._get(f"{path}{separator}offset={offset}&limit={limit}")
            data = payload.get("data", payload if isinstance(payload, list) else [])
            if not isinstance(data, list):
                data = []
            items.extend(data)
            total = payload.get("totalCount", payload.get("total", len(items)))
            count = payload.get("count", len(data))
            if not data or offset + count >= total:
                break
            offset += limit
        return items

    def _get(self, path: str) -> dict:
        return self._request_json(f"{self.config.base_url}{path}")

    def _request_json(self, url: str) -> dict:
        req = request.Request(url, headers={
            "Accept": "application/json",
            "X-API-Key": self.config.api_key,
            "User-Agent": "UbiquitiOpsConsole/0.2",
        })
        with request.urlopen(req, timeout=self.config.timeout_seconds, context=self._ssl_context) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def build_traffic_insights(
    devices: list[dict],
    clients: list[dict],
    device_stats: list[dict],
    legacy_stats: dict,
) -> dict:
    client_activity = [summarize_client(client) for client in clients]
    device_activity = [summarize_device(device, device_stats) for device in devices]

    legacy_clients = legacy_stats.get("clients") if isinstance(legacy_stats.get("clients"), list) else []
    legacy_devices = legacy_stats.get("devices") if isinstance(legacy_stats.get("devices"), list) else []
    if legacy_clients:
        client_activity = merge_legacy_client_activity(client_activity, legacy_clients)
    if legacy_devices:
        device_activity = merge_legacy_device_activity(device_activity, legacy_devices)

    top_clients = sorted(client_activity, key=lambda item: item["total_bytes"], reverse=True)[:10]
    top_devices = sorted(device_activity, key=lambda item: item["total_rate_bps"], reverse=True)[:10]
    total_rx = sum(item["rx_bytes"] for item in client_activity)
    total_tx = sum(item["tx_bytes"] for item in client_activity)

    return {
        "client_count": len(client_activity),
        "device_count": len(device_activity),
        "total_client_rx_bytes": total_rx,
        "total_client_tx_bytes": total_tx,
        "top_clients": top_clients,
        "top_devices": top_devices,
    }


def summarize_client(client: dict) -> dict:
    rx = first_int(client, "rxBytes", "rx_bytes", "bytesIn", "wiredRxBytes", "wirelessRxBytes")
    tx = first_int(client, "txBytes", "tx_bytes", "bytesOut", "wiredTxBytes", "wirelessTxBytes")
    return {
        "id": str(client.get("id") or client.get("macAddress") or client.get("mac") or ""),
        "name": str(client.get("name") or client.get("hostname") or client.get("macAddress") or "Unknown client"),
        "ip": str(client.get("ipAddress") or client.get("ip") or ""),
        "mac": str(client.get("macAddress") or client.get("mac") or ""),
        "type": str(client.get("type") or client.get("network") or "CLIENT"),
        "rx_bytes": rx,
        "tx_bytes": tx,
        "total_bytes": rx + tx,
        "raw": compact_raw(client),
    }


def summarize_device(device: dict, stats: list[dict]) -> dict:
    device_id = str(device.get("id") or "")
    stat = next((item for item in stats if str(item.get("deviceId")) == device_id), {})
    uplink = stat.get("uplink") if isinstance(stat.get("uplink"), dict) else {}
    tx_rate = first_int(uplink, "txRateBps", "tx_rate_bps", "tx_bytes-r")
    rx_rate = first_int(uplink, "rxRateBps", "rx_rate_bps", "rx_bytes-r")
    return {
        "id": device_id,
        "name": str(device.get("name") or device.get("model") or device.get("macAddress") or "Unknown device"),
        "ip": str(device.get("ipAddress") or device.get("ip") or ""),
        "mac": str(device.get("macAddress") or device.get("mac") or ""),
        "model": str(device.get("model") or device.get("type") or ""),
        "state": str(device.get("state") or ""),
        "rx_rate_bps": rx_rate,
        "tx_rate_bps": tx_rate,
        "total_rate_bps": rx_rate + tx_rate,
        "cpu_pct": first_float(stat, "cpuUtilizationPct", "cpu"),
        "memory_pct": first_float(stat, "memoryUtilizationPct", "mem"),
        "raw": compact_raw(device),
    }


def merge_legacy_client_activity(current: list[dict], legacy_clients: list[dict]) -> list[dict]:
    by_mac = {item["mac"].lower(): item for item in current if item["mac"]}
    for legacy in legacy_clients:
        mac = str(legacy.get("mac") or legacy.get("macAddress") or "").lower()
        rx = first_int(legacy, "rx_bytes", "rxBytes", "bytes-r", "rx_bytes-r")
        tx = first_int(legacy, "tx_bytes", "txBytes", "bytes-s", "tx_bytes-r")
        if mac in by_mac:
            by_mac[mac]["rx_bytes"] = max(by_mac[mac]["rx_bytes"], rx)
            by_mac[mac]["tx_bytes"] = max(by_mac[mac]["tx_bytes"], tx)
            by_mac[mac]["total_bytes"] = by_mac[mac]["rx_bytes"] + by_mac[mac]["tx_bytes"]
        else:
            current.append(summarize_client(legacy))
    return current


def merge_legacy_device_activity(current: list[dict], legacy_devices: list[dict]) -> list[dict]:
    by_mac = {item["mac"].lower(): item for item in current if item["mac"]}
    for legacy in legacy_devices:
        mac = str(legacy.get("mac") or legacy.get("macAddress") or "").lower()
        rx_rate = first_int(legacy, "rx_bytes-r", "rxRateBps")
        tx_rate = first_int(legacy, "tx_bytes-r", "txRateBps")
        if mac in by_mac:
            by_mac[mac]["rx_rate_bps"] = max(by_mac[mac]["rx_rate_bps"], rx_rate)
            by_mac[mac]["tx_rate_bps"] = max(by_mac[mac]["tx_rate_bps"], tx_rate)
            by_mac[mac]["total_rate_bps"] = by_mac[mac]["rx_rate_bps"] + by_mac[mac]["tx_rate_bps"]
        else:
            item = summarize_device(legacy, [])
            item["rx_rate_bps"] = rx_rate
            item["tx_rate_bps"] = tx_rate
            item["total_rate_bps"] = rx_rate + tx_rate
            current.append(item)
    return current


def infer_site_id(sites: list[dict]) -> str:
    if not sites:
        return ""
    first = sites[0]
    return str(first.get("id") or first.get("internalReference") or first.get("name") or "")


def first_int(source: dict, *keys: str) -> int:
    for key in keys:
        value = source.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def first_float(source: dict, *keys: str) -> float:
    for key in keys:
        value = source.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def compact_raw(source: dict) -> dict:
    keep = ("id", "name", "ipAddress", "macAddress", "model", "state", "type", "connectedAt", "uplinkDeviceId")
    return {key: source[key] for key in keep if key in source}


def error_snapshot(error: str, checked_at: str, started: float) -> dict:
    return {
        "ok": False,
        "checked_at": checked_at,
        "latency_ms": elapsed_ms(started),
        "site_id": "",
        "sites": [],
        "devices": [],
        "clients": [],
        "device_statistics": [],
        "legacy_stats": {},
        "traffic_insights": {
            "client_count": 0,
            "device_count": 0,
            "total_client_rx_bytes": 0,
            "total_client_tx_bytes": 0,
            "top_clients": [],
            "top_devices": [],
        },
        "error": error,
    }


def elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
