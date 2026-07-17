from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import ssl
import time
from typing import Any
from urllib.error import HTTPError
from urllib import parse, request

from .config import Config, TrustedClient, normalize_mac

LOG = logging.getLogger("ubiquiti-ops.unifi-api")


@dataclass(frozen=True)
class UniFiApiConfig:
    base_url: str
    api_key: str
    site_id: str = ""
    verify_tls: bool = False
    timeout_seconds: int = 10
    legacy_stats_enabled: bool = True
    site_manager_enabled: bool = False
    site_manager_base_url: str = "https://api.ui.com"
    site_manager_api_key: str = ""
    trusted_clients: tuple[TrustedClient, ...] = ()


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
            site_manager_enabled=config.unifi_site_manager_enabled,
            site_manager_base_url=config.unifi_site_manager_base_url,
            site_manager_api_key=config.unifi_site_manager_api_key or config.unifi_api_key,
            trusted_clients=config.trusted_clients,
        ))

    def collect(self) -> dict:
        started = time.perf_counter()
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        site_manager_key = self.config.site_manager_api_key
        if not self.config.api_key and not site_manager_key:
            return error_snapshot("No UniFi API key is configured", checked_at, started)

        sites: list[dict] = []
        devices: list[dict] = []
        clients: list[dict] = []
        site_manager_sites: list[dict] = []
        site_manager_hosts: list[dict] = []
        site_manager_devices: list[dict] = []
        site_manager_isp_metrics: list[dict] = []
        site_manager_errors: dict[str, str] = {}
        device_stats: list[dict] = []
        legacy_stats: dict = {}
        site_id = self.config.site_id
        network_error = ""
        site_manager_error = ""

        if self.config.api_key:
            try:
                if site_id:
                    try:
                        sites = self.list_sites()
                        site_id = resolve_local_site_id(site_id, sites)
                    except Exception as exc:  # noqa: BLE001
                        LOG.info("UniFi Network API site listing skipped because UNIFI_SITE_ID is configured: %s", exc)
                else:
                    sites = self.list_sites()
                    site_id = infer_site_id(sites)
                if not site_id:
                    network_error = "No UniFi site was found. Set UNIFI_SITE_ID in .env."
                else:
                    devices = self.list_devices(site_id)
                    clients = self.list_clients(site_id)
                    clients = annotate_trusted_clients(clients, self.config.trusted_clients)
                    device_stats = self.collect_device_statistics(site_id, devices)
                    legacy_stats = self.collect_legacy_stats(site_id) if self.config.legacy_stats_enabled else {}
            except Exception as exc:  # noqa: BLE001
                network_error = str(exc)
                LOG.warning("UniFi Network API collection failed: %s", exc)
        else:
            network_error = "UNIFI_API_KEY is not configured; local Network API collection skipped."

        if self.config.site_manager_enabled:
            site_manager = self.collect_site_manager()
            site_manager_sites = site_manager["sites"]
            site_manager_hosts = site_manager["hosts"]
            site_manager_devices = site_manager["devices"]
            site_manager_isp_metrics = site_manager["isp_metrics"]
            site_manager_errors = site_manager["errors"]
            site_manager_error = "; ".join(f"{key}: {value}" for key, value in site_manager_errors.items())

        insights = build_traffic_insights(devices, clients, device_stats, legacy_stats)
        site_manager_has_data = any((site_manager_sites, site_manager_hosts, site_manager_devices, site_manager_isp_metrics))
        ok = not network_error or site_manager_has_data
        error = network_error or site_manager_error
        return {
            "ok": ok,
            "checked_at": checked_at,
            "latency_ms": elapsed_ms(started),
            "site_id": site_id,
            "sites": sites,
            "devices": devices,
            "clients": clients,
            "site_manager_sites": site_manager_sites,
            "site_manager_hosts": site_manager_hosts,
            "site_manager_devices": site_manager_devices,
            "site_manager_isp_metrics": site_manager_isp_metrics,
            "site_manager_errors": site_manager_errors,
            "device_statistics": device_stats,
            "legacy_stats": legacy_stats,
            "traffic_insights": insights,
            "network_error": network_error,
            "site_manager_error": site_manager_error,
            "error": error,
        }

    def list_sites(self) -> list[dict]:
        return self._paged("/v1/sites")

    def list_devices(self, site_id: str) -> list[dict]:
        return self._paged_first_success(
            f"/v1/sites/{parse.quote(site_id)}/devices",
            "/v1/devices",
        )

    def list_clients(self, site_id: str) -> list[dict]:
        return self._paged_first_success(
            f"/v1/sites/{parse.quote(site_id)}/clients",
            "/v1/clients",
        )

    def collect_device_statistics(self, site_id: str, devices: list[dict]) -> list[dict]:
        stats: list[dict] = []
        for device in devices:
            device_id = str(device.get("id") or "")
            if not device_id:
                continue
            try:
                latest = self._get_first_success(
                    f"/v1/sites/{parse.quote(site_id)}/devices/{parse.quote(device_id)}/statistics/latest",
                    f"/v1/devices/{parse.quote(device_id)}/statistics/latest",
                )
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

    def list_site_manager_devices(self) -> list[dict]:
        if not self.config.site_manager_api_key:
            return []
        return self._site_manager_paged("/v1/devices")

    def list_site_manager_sites(self) -> list[dict]:
        if not self.config.site_manager_api_key:
            return []
        return self._site_manager_paged("/v1/sites")

    def list_site_manager_hosts(self) -> list[dict]:
        if not self.config.site_manager_api_key:
            return []
        return self._site_manager_paged("/v1/hosts")

    def list_site_manager_isp_metrics(self) -> list[dict]:
        if not self.config.site_manager_api_key:
            return []
        return self._site_manager_paged("/v1/isp-metrics")

    def collect_site_manager(self) -> dict:
        output: dict[str, Any] = {
            "sites": [],
            "hosts": [],
            "devices": [],
            "isp_metrics": [],
            "errors": {},
        }
        for key, collector in {
            "sites": self.list_site_manager_sites,
            "hosts": self.list_site_manager_hosts,
            "devices": self.list_site_manager_devices,
            "isp_metrics": self.list_site_manager_isp_metrics,
        }.items():
            try:
                output[key] = collector()
            except Exception as exc:  # noqa: BLE001
                if key == "isp_metrics" and is_not_available(exc):
                    LOG.info("UniFi Site Manager ISP metrics endpoint is not available: %s", exc)
                    output[key] = []
                    continue
                output["errors"][key] = str(exc)
                LOG.warning("UniFi Site Manager %s collection failed: %s", key, exc)
        return output

    def _paged(self, path: str, limit: int = 200) -> list[dict]:
        return self._paged_url(f"{self.config.base_url}{path}", limit=limit)

    def _paged_first_success(self, *paths: str, limit: int = 200) -> list[dict]:
        last_error: Exception | None = None
        for path in paths:
            try:
                return self._paged(path, limit=limit)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not is_endpoint_fallback_error(exc):
                    raise
                LOG.info("UniFi Network API path rejected, trying fallback: %s", path)
        if last_error:
            raise last_error
        return []

    def _paged_url(self, url: str, limit: int = 200, api_key: str | None = None, verify_tls: bool | None = None) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while True:
            separator = "&" if "?" in url else "?"
            payload = self._request_json(
                f"{url}{separator}offset={offset}&limit={limit}",
                api_key=api_key,
                verify_tls=verify_tls,
            )
            if isinstance(payload, list):
                data = payload
                total = len(items) + len(data)
                count = len(data)
            else:
                data = payload.get("data", [])
                total = payload.get("totalCount", payload.get("total", len(items) + len(data)))
                count = payload.get("count", len(data))
            if not isinstance(data, list):
                data = []
            items.extend(data)
            if not data or offset + count >= total:
                break
            offset += limit
        return items

    def _site_manager_paged(self, path: str, limit: int = 200) -> list[dict]:
        items: list[dict] = []
        next_token = ""
        while True:
            query = {"pageSize": str(limit)}
            if next_token:
                query["nextToken"] = next_token
            url = f"{self.config.site_manager_base_url}{path}?{parse.urlencode(query)}"
            payload = self._request_json(
                url,
                api_key=self.config.site_manager_api_key,
                verify_tls=True,
            )
            data = payload if isinstance(payload, list) else payload.get("data", [])
            if not isinstance(data, list):
                data = []
            items.extend(data)
            next_token = "" if isinstance(payload, list) else str(payload.get("nextToken") or "")
            if not data or not next_token:
                break
        return items

    def _get(self, path: str) -> dict:
        return self._request_json(f"{self.config.base_url}{path}")

    def _get_first_success(self, *paths: str) -> dict:
        last_error: Exception | None = None
        for path in paths:
            try:
                return self._get(path)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not is_endpoint_fallback_error(exc):
                    raise
                LOG.info("UniFi Network API path rejected, trying fallback: %s", path)
        if last_error:
            raise last_error
        return {}

    def _request_json(self, url: str, api_key: str | None = None, verify_tls: bool | None = None) -> dict:
        req = request.Request(url, headers={
            "Accept": "application/json",
            "X-API-Key": api_key or self.config.api_key,
            "User-Agent": "UbiquitiOpsConsole/0.2",
        })
        context = None if verify_tls else self._ssl_context
        with request.urlopen(req, timeout=self.config.timeout_seconds, context=context) as response:
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
    total_device_rx_rate = sum(item["rx_rate_bps"] for item in device_activity)
    total_device_tx_rate = sum(item["tx_rate_bps"] for item in device_activity)
    active_clients = len([item for item in client_activity if item["total_bytes"] > 0])
    stressed_devices = len([
        item for item in device_activity
        if item["cpu_pct"] >= 80 or item["memory_pct"] >= 80
    ])

    return {
        "client_count": len(client_activity),
        "device_count": len(device_activity),
        "active_client_count": active_clients,
        "total_client_rx_bytes": total_rx,
        "total_client_tx_bytes": total_tx,
        "total_client_bytes": total_rx + total_tx,
        "total_device_rx_rate_bps": total_device_rx_rate,
        "total_device_tx_rate_bps": total_device_tx_rate,
        "total_device_rate_bps": total_device_rx_rate + total_device_tx_rate,
        "stressed_device_count": stressed_devices,
        "top_clients": top_clients,
        "top_devices": top_devices,
    }


def summarize_client(client: dict) -> dict:
    rx = first_int(client, "rxBytes", "rx_bytes", "bytesIn", "wiredRxBytes", "wirelessRxBytes")
    tx = first_int(client, "txBytes", "tx_bytes", "bytesOut", "wiredTxBytes", "wirelessTxBytes")
    rx_rate = first_int(client, "rxRateBps", "rx_rate_bps", "rx_bytes-r", "wiredRxRateBps", "wirelessRxRateBps")
    tx_rate = first_int(client, "txRateBps", "tx_rate_bps", "tx_bytes-r", "wiredTxRateBps", "wirelessTxRateBps")
    return {
        "id": str(client.get("id") or client.get("macAddress") or client.get("mac") or ""),
        "name": str(client.get("trustedName") or client.get("name") or client.get("hostname") or client.get("macAddress") or "Unknown client"),
        "ip": str(client.get("ipAddress") or client.get("ip") or ""),
        "mac": str(client.get("macAddress") or client.get("mac") or ""),
        "type": str(client.get("type") or client.get("network") or "CLIENT"),
        "rx_bytes": rx,
        "tx_bytes": tx,
        "total_bytes": rx + tx,
        "rx_rate_bps": rx_rate,
        "tx_rate_bps": tx_rate,
        "total_rate_bps": rx_rate + tx_rate,
        "raw": compact_raw(client),
}


def annotate_trusted_clients(clients: list[dict], trusted_clients: tuple[TrustedClient, ...]) -> list[dict]:
    trusted_by_mac = {client.mac: client for client in trusted_clients if client.mac}
    annotated: list[dict] = []
    for client in clients:
        enriched = dict(client)
        mac = normalize_mac(str(client.get("macAddress") or client.get("mac") or ""))
        trusted = trusted_by_mac.get(mac)
        enriched["normalizedMac"] = mac
        if trusted:
            enriched["trusted"] = True
            enriched["trustedName"] = trusted.name
            enriched["trustedCategory"] = trusted.category
        else:
            enriched["trusted"] = False
            enriched.setdefault("trustedCategory", "untrusted")
        annotated.append(enriched)
    return annotated


def summarize_device(device: dict, stats: list[dict]) -> dict:
    device_id = str(device.get("id") or "")
    stat = next((item for item in stats if str(item.get("deviceId")) == device_id), {})
    uplink = stat.get("uplink") if isinstance(stat.get("uplink"), dict) else {}
    downlink = stat.get("downlink") if isinstance(stat.get("downlink"), dict) else {}
    tx_rate = first_int(uplink, "txRateBps", "tx_rate_bps", "tx_bytes-r", "tx_bytes") or first_int(stat, "txRateBps", "tx_rate_bps")
    rx_rate = first_int(uplink, "rxRateBps", "rx_rate_bps", "rx_bytes-r", "rx_bytes") or first_int(stat, "rxRateBps", "rx_rate_bps")
    uplink_tx_bytes = first_int(uplink, "txBytes", "tx_bytes", "bytes-s")
    uplink_rx_bytes = first_int(uplink, "rxBytes", "rx_bytes", "bytes-r")
    port_count = len(stat.get("ports") or device.get("ports") or []) if isinstance(stat.get("ports") or device.get("ports") or [], list) else 0
    radio_count = len(stat.get("radios") or device.get("radios") or []) if isinstance(stat.get("radios") or device.get("radios") or [], list) else 0
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
        "rx_bytes": uplink_rx_bytes,
        "tx_bytes": uplink_tx_bytes,
        "total_bytes": uplink_rx_bytes + uplink_tx_bytes,
        "cpu_pct": first_float(stat, "cpuUtilizationPct", "cpu", "systemStats.cpu"),
        "memory_pct": first_float(stat, "memoryUtilizationPct", "mem", "systemStats.mem"),
        "uplink_name": str(uplink.get("name") or uplink.get("interface") or uplink.get("port") or ""),
        "downlink_count": first_int(downlink, "count", "portCount"),
        "port_count": port_count,
        "radio_count": radio_count,
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


def resolve_local_site_id(configured_site_id: str, sites: list[dict]) -> str:
    configured = configured_site_id.strip()
    if not configured or not sites:
        return configured
    for site in sites:
        identifiers = {
            str(site.get("id") or ""),
            str(site.get("internalReference") or ""),
            str(site.get("name") or ""),
        }
        if configured in identifiers:
            return str(site.get("id") or configured)
    inferred = infer_site_id(sites)
    LOG.info(
        "Configured UNIFI_SITE_ID %s was not found in local sites; using local site id %s",
        configured,
        inferred,
    )
    return inferred or configured


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


def is_endpoint_fallback_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code in {400, 404}
    message = str(exc)
    return "HTTP Error 400" in message or "HTTP Error 404" in message


def is_not_available(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code == 404
    return "HTTP Error 404" in str(exc)


def error_snapshot(error: str, checked_at: str, started: float) -> dict:
    return {
        "ok": False,
        "checked_at": checked_at,
        "latency_ms": elapsed_ms(started),
        "site_id": "",
        "sites": [],
        "devices": [],
        "clients": [],
        "site_manager_sites": [],
        "site_manager_hosts": [],
        "site_manager_devices": [],
        "site_manager_isp_metrics": [],
        "site_manager_errors": {},
        "device_statistics": [],
        "legacy_stats": {},
        "network_error": error,
        "site_manager_error": "",
        "traffic_insights": {
            "client_count": 0,
            "device_count": 0,
            "active_client_count": 0,
            "total_client_rx_bytes": 0,
            "total_client_tx_bytes": 0,
            "total_client_bytes": 0,
            "total_device_rx_rate_bps": 0,
            "total_device_tx_rate_bps": 0,
            "total_device_rate_bps": 0,
            "stressed_device_count": 0,
            "top_clients": [],
            "top_devices": [],
        },
        "error": error,
    }


def elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
