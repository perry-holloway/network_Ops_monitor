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

