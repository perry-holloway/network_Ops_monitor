from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import re
import socket
import subprocess
import time

from .checks import ping_host


@dataclass(frozen=True)
class DiscoveryConfig:
    subnets: tuple[str, ...]
    ports: tuple[int, ...]
    max_hosts: int = 256
    workers: int = 64


def discover_lan(config: DiscoveryConfig) -> dict:
    started = time.perf_counter()
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    targets = expand_targets(config.subnets, config.max_hosts)
    arp_before = read_neighbor_table()
    devices: list[dict] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, config.workers)) as executor:
        futures = {
            executor.submit(probe_host, address, config.ports): address
            for address in targets
        }
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{futures[future]}: {exc}")
                continue
            if result["online"]:
                devices.append(result)

    arp_after = read_neighbor_table()
    neighbors = {**arp_before, **arp_after}
    for device in devices:
        neighbor = neighbors.get(device["ip"], {})
        device["mac"] = neighbor.get("mac", "")
        device["neighbor_state"] = neighbor.get("state", "")

    devices.sort(key=lambda item: ipaddress.ip_address(item["ip"]))
    return {
        "ok": True,
        "checked_at": checked_at,
        "latency_ms": elapsed_ms(started),
        "subnets": list(config.subnets),
        "ports": list(config.ports),
        "scanned_hosts": len(targets),
        "device_count": len(devices),
        "devices": devices,
        "errors": errors[:20],
    }


def expand_targets(subnets: tuple[str, ...], max_hosts: int) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    for raw in subnets:
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            continue
        for address in network.hosts():
            value = str(address)
            if value in seen:
                continue
            seen.add(value)
            targets.append(value)
            if len(targets) >= max_hosts:
                return targets
    return targets


def probe_host(address: str, ports: tuple[int, ...]) -> dict:
    started = time.perf_counter()
    ping = ping_host(address)
    open_ports: list[int] = []
    if not ping["ok"]:
        for port in ports:
            if tcp_connect_quick(address, port):
                open_ports.append(port)
    hostname = reverse_dns(address) if ping["ok"] or open_ports else ""
    online = bool(ping["ok"] or open_ports)
    return {
        "ip": address,
        "hostname": hostname,
        "online": online,
        "latency_ms": ping["latency_ms"] if ping["latency_ms"] is not None else elapsed_ms(started),
        "ping": bool(ping["ok"]),
        "open_ports": open_ports,
        "mac": "",
        "neighbor_state": "",
    }


def reverse_dns(address: str) -> str:
    try:
        return socket.gethostbyaddr(address)[0]
    except OSError:
        return ""


def tcp_connect_quick(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def read_neighbor_table() -> dict[str, dict]:
    commands = [["ip", "neigh"], ["arp", "-a"]]
    for command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
        except Exception:
            continue
        if completed.returncode == 0 and completed.stdout:
            parsed = parse_neighbors(completed.stdout)
            if parsed:
                return parsed
    return {}


def parse_neighbors(raw: str) -> dict[str, dict]:
    neighbors: dict[str, dict] = {}
    for line in raw.splitlines():
        ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
        mac_match = re.search(r"([0-9a-fA-F]{2}(?::|-)[0-9a-fA-F]{2}(?::|-)[0-9a-fA-F]{2}(?::|-)[0-9a-fA-F]{2}(?::|-)[0-9a-fA-F]{2}(?::|-)[0-9a-fA-F]{2})", line)
        if not ip_match:
            continue
        ip = ip_match.group(1)
        state = ""
        for candidate in ("REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT", "FAILED"):
            if candidate in line.upper():
                state = candidate.lower()
                break
        neighbors[ip] = {
            "mac": mac_match.group(1).replace("-", ":").lower() if mac_match else "",
            "state": state,
        }
    return neighbors


def elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
