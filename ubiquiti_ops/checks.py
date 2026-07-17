from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import platform
import socket
import subprocess
import time
from urllib import request

from .config import NamedTarget, WatchedDevice


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_device(device: WatchedDevice) -> dict:
    started = time.perf_counter()
    ping = ping_host(device.address)
    open_ports: list[int] = []
    if not ping["ok"] and device.ports:
        for port in device.ports:
            if tcp_connect(device.address, port):
                open_ports.append(port)
    latency_ms = elapsed_ms(started)
    ok = bool(ping["ok"] or open_ports)
    return {
        "kind": "device",
        "target": device.address,
        "name": device.name,
        "sensitivity": device.sensitivity,
        "status": "online" if ok else "offline",
        "ok": ok,
        "latency_ms": ping["latency_ms"] if ping["latency_ms"] is not None else latency_ms,
        "checked_at": now_iso(),
        "details": {
            "ping": ping,
            "configured_ports": list(device.ports),
            "open_ports": open_ports,
        },
    }


def check_wan(target: NamedTarget) -> dict:
    started = time.perf_counter()
    ping = ping_host(target.target)
    return {
        "kind": "wan",
        "target": target.target,
        "name": target.name,
        "sensitivity": "critical",
        "status": "reachable" if ping["ok"] else "unreachable",
        "ok": bool(ping["ok"]),
        "latency_ms": ping["latency_ms"] if ping["latency_ms"] is not None else elapsed_ms(started),
        "checked_at": now_iso(),
        "details": {"ping": ping},
    }


def check_dns(hostname: str) -> dict:
    started = time.perf_counter()
    addresses: list[str] = []
    error = ""
    try:
        addresses = socket.gethostbyname_ex(hostname)[2]
    except OSError as exc:
        error = str(exc)
    ok = bool(addresses)
    return {
        "kind": "dns",
        "target": hostname,
        "name": hostname,
        "sensitivity": "high",
        "status": "resolved" if ok else "failed",
        "ok": ok,
        "latency_ms": elapsed_ms(started),
        "checked_at": now_iso(),
        "details": {"addresses": addresses, "error": error},
    }


def check_http(target: NamedTarget) -> dict:
    started = time.perf_counter()
    status_code = None
    error = ""
    try:
        req = request.Request(target.target, headers={"User-Agent": "UbiquitiOpsConsole/0.1"})
        with request.urlopen(req, timeout=8) as response:
            status_code = response.status
    except Exception as exc:  # noqa: BLE001 - expose operational error in dashboard
        error = str(exc)
    ok = status_code is not None and 200 <= status_code < 500
    return {
        "kind": "http",
        "target": target.target,
        "name": target.name,
        "sensitivity": "normal",
        "status": "up" if ok else "down",
        "ok": ok,
        "latency_ms": elapsed_ms(started),
        "checked_at": now_iso(),
        "details": {"status_code": status_code, "error": error},
    }


def ping_host(host: str) -> dict:
    system = platform.system().lower()
    count_arg = "-n" if system == "windows" else "-c"
    timeout_args = ["-w", "1000"] if system == "windows" else ["-W", "1"]
    command = ["ping", count_arg, "1", *timeout_args, host]
    started = time.perf_counter()
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": None, "error": str(exc)}
    ok = completed.returncode == 0
    return {
        "ok": ok,
        "latency_ms": elapsed_ms(started),
        "error": "" if ok else (completed.stderr.strip() or completed.stdout.strip()[-160:]),
    }


def tcp_connect(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def result_to_record(result: dict) -> dict:
    record = dict(result)
    record["details"] = asdict(result["details"]) if hasattr(result.get("details"), "__dataclass_fields__") else result.get("details", {})
    return record

