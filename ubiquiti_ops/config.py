from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class WatchedDevice:
    address: str
    name: str
    sensitivity: str = "normal"
    ports: tuple[int, ...] = ()


@dataclass(frozen=True)
class NamedTarget:
    target: str
    name: str


@dataclass(frozen=True)
class TrustedClient:
    mac: str
    name: str
    category: str = "trusted"


@dataclass(frozen=True)
class Config:
    app_host: str
    app_port: int
    data_path: str
    watched_devices: tuple[WatchedDevice, ...]
    check_interval_seconds: int
    history_limit: int
    wan_targets: tuple[NamedTarget, ...]
    dns_lookups: tuple[str, ...]
    http_checks: tuple[NamedTarget, ...]
    unifi_api_enabled: bool
    unifi_api_base_url: str
    unifi_api_key: str
    unifi_site_id: str
    unifi_verify_tls: bool
    unifi_timeout_seconds: int
    unifi_legacy_stats_enabled: bool
    unifi_site_manager_enabled: bool
    unifi_site_manager_base_url: str
    unifi_site_manager_api_key: str
    trusted_clients: tuple[TrustedClient, ...]
    lan_discovery_enabled: bool
    lan_discovery_subnets: tuple[str, ...]
    lan_discovery_ports: tuple[int, ...]
    lan_discovery_max_hosts: int
    speed_test_enabled: bool
    speed_test_download_url: str
    speed_test_upload_url: str
    speed_test_upload_enabled: bool
    speed_test_download_bytes: int
    speed_test_upload_bytes: int
    speed_test_timeout_seconds: int
    speed_test_min_download_mbps: float
    speed_test_min_upload_mbps: float

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            app_host=os.getenv("APP_HOST", "0.0.0.0").strip() or "0.0.0.0",
            app_port=_int("APP_PORT", 8090),
            data_path=os.getenv("DATA_PATH", "data/ops-console.db").strip() or "data/ops-console.db",
            watched_devices=parse_watched_devices(os.getenv("WATCHED_DEVICES", "")),
            check_interval_seconds=max(10, _int("CHECK_INTERVAL_SECONDS", 60)),
            history_limit=max(10, _int("HISTORY_LIMIT", 100)),
            wan_targets=parse_named_targets(os.getenv("WAN_TARGETS", "1.1.1.1=Cloudflare DNS;8.8.8.8=Google DNS")),
            dns_lookups=tuple(item.strip() for item in os.getenv("DNS_LOOKUPS", "ui.com;github.com").split(";") if item.strip()),
            http_checks=parse_named_targets(os.getenv("HTTP_CHECKS", "https://ui.com=UniFi Website;https://github.com=GitHub")),
            unifi_api_enabled=_bool("UNIFI_API_ENABLED", False),
            unifi_api_base_url=os.getenv("UNIFI_API_BASE_URL", "https://192.168.1.1/proxy/network/integration").rstrip("/"),
            unifi_api_key=os.getenv("UNIFI_API_KEY", "").strip(),
            unifi_site_id=os.getenv("UNIFI_SITE_ID", "").strip(),
            unifi_verify_tls=_bool("UNIFI_VERIFY_TLS", False),
            unifi_timeout_seconds=max(3, _int("UNIFI_TIMEOUT_SECONDS", 10)),
            unifi_legacy_stats_enabled=_bool("UNIFI_LEGACY_STATS_ENABLED", True),
            unifi_site_manager_enabled=_bool("UNIFI_SITE_MANAGER_ENABLED", False),
            unifi_site_manager_base_url=os.getenv("UNIFI_SITE_MANAGER_BASE_URL", "https://api.ui.com").rstrip("/"),
            unifi_site_manager_api_key=os.getenv("UNIFI_SITE_MANAGER_API_KEY", "").strip(),
            trusted_clients=parse_trusted_clients(os.getenv("TRUSTED_CLIENTS", "")),
            lan_discovery_enabled=_bool("LAN_DISCOVERY_ENABLED", False),
            lan_discovery_subnets=parse_csv(os.getenv("LAN_DISCOVERY_SUBNETS", "192.168.1.0/24")),
            lan_discovery_ports=parse_ports(os.getenv("LAN_DISCOVERY_PORTS", "22,53,80,443,445,8080,8443")),
            lan_discovery_max_hosts=max(1, _int("LAN_DISCOVERY_MAX_HOSTS", 256)),
            speed_test_enabled=_bool("SPEED_TEST_ENABLED", True),
            speed_test_download_url=os.getenv(
                "SPEED_TEST_DOWNLOAD_URL",
                "https://speed.cloudflare.com/__down?bytes=10000000",
            ).strip(),
            speed_test_upload_url=os.getenv("SPEED_TEST_UPLOAD_URL", "https://speed.cloudflare.com/__up").strip(),
            speed_test_upload_enabled=_bool("SPEED_TEST_UPLOAD_ENABLED", False),
            speed_test_download_bytes=max(100_000, _int("SPEED_TEST_DOWNLOAD_BYTES", 10_000_000)),
            speed_test_upload_bytes=max(100_000, _int("SPEED_TEST_UPLOAD_BYTES", 1_000_000)),
            speed_test_timeout_seconds=max(5, _int("SPEED_TEST_TIMEOUT_SECONDS", 20)),
            speed_test_min_download_mbps=max(0.0, _float("SPEED_TEST_MIN_DOWNLOAD_MBPS", 100.0)),
            speed_test_min_upload_mbps=max(0.0, _float("SPEED_TEST_MIN_UPLOAD_MBPS", 10.0)),
        )


def parse_watched_devices(raw: str) -> tuple[WatchedDevice, ...]:
    devices: list[WatchedDevice] = []
    for item in raw.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        address, rest = item.split("=", 1)
        parts = [part.strip() for part in rest.split(":")]
        name = parts[0] if parts and parts[0] else address.strip()
        sensitivity = parts[1].lower() if len(parts) > 1 and parts[1] else "normal"
        ports = parse_ports(parts[2]) if len(parts) > 2 else ()
        devices.append(WatchedDevice(address.strip(), name, sensitivity, ports))
    return tuple(devices)


def parse_named_targets(raw: str) -> tuple[NamedTarget, ...]:
    targets: list[NamedTarget] = []
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            target, name = item.split("=", 1)
            targets.append(NamedTarget(target.strip(), name.strip() or target.strip()))
        else:
            targets.append(NamedTarget(item, item))
    return tuple(targets)


def parse_trusted_clients(raw: str) -> tuple[TrustedClient, ...]:
    clients: list[TrustedClient] = []
    for item in raw.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        mac, rest = item.split("=", 1)
        normalized_mac = normalize_mac(mac)
        if not normalized_mac:
            continue
        parts = [part.strip() for part in rest.split(":")]
        name = parts[0] if parts and parts[0] else normalized_mac
        category = parts[1].lower() if len(parts) > 1 and parts[1] else "trusted"
        clients.append(TrustedClient(normalized_mac, name, category))
    return tuple(clients)


def normalize_mac(raw: str) -> str:
    compact = "".join(char for char in str(raw or "").lower() if char in "0123456789abcdef")
    if len(compact) != 12:
        return ""
    return ":".join(compact[index:index + 2] for index in range(0, 12, 2))


def parse_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.replace(";", ",").split(",") if item.strip())


def parse_ports(raw: str) -> tuple[int, ...]:
    ports: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            port = int(item)
        except ValueError:
            continue
        if 1 <= port <= 65535:
            ports.append(port)
    return tuple(ports)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
