from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SpeedTestConfig:
    enabled: bool
    download_url: str
    upload_url: str
    upload_enabled: bool
    download_bytes: int
    upload_bytes: int
    timeout_seconds: int
    min_download_mbps: float
    min_upload_mbps: float


def run_speed_test(config: SpeedTestConfig) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat()
    result = {
        "configured": config.enabled,
        "ok": False,
        "checked_at": checked_at,
        "download_mbps": 0.0,
        "upload_mbps": None,
        "latency_ms": 0,
        "download_bytes": 0,
        "upload_bytes": 0,
        "duration_ms": 0,
        "download_url": config.download_url,
        "upload_enabled": config.upload_enabled,
        "thresholds": {
            "min_download_mbps": config.min_download_mbps,
            "min_upload_mbps": config.min_upload_mbps,
        },
        "error": "",
    }
    if not config.enabled:
        result["error"] = "Speed test is disabled."
        return result
    if not config.download_url:
        result["error"] = "SPEED_TEST_DOWNLOAD_URL is not configured."
        return result

    started = time.monotonic()
    try:
        download = measure_download(config.download_url, config.download_bytes, config.timeout_seconds)
        result.update(download)
        if config.upload_enabled:
            if not config.upload_url:
                raise ValueError("SPEED_TEST_UPLOAD_URL is not configured.")
            upload = measure_upload(config.upload_url, config.upload_bytes, config.timeout_seconds)
            result.update(upload)
        result["ok"] = passes_thresholds(result, config)
    except (OSError, TimeoutError, URLError, ValueError) as exc:
        result["error"] = str(exc)
    finally:
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
    return result


def measure_download(url: str, byte_limit: int, timeout_seconds: int) -> dict:
    started = time.monotonic()
    first_byte_at: float | None = None
    received = 0
    request = Request(url, headers={"User-Agent": "ubiquiti-ops-console/1.0"})
    with urlopen(request, timeout=timeout_seconds) as response:
        while received < byte_limit:
            chunk = response.read(min(64 * 1024, byte_limit - received))
            if not chunk:
                break
            if first_byte_at is None:
                first_byte_at = time.monotonic()
            received += len(chunk)
    finished = time.monotonic()
    elapsed = max(finished - started, 0.001)
    latency_ms = int(((first_byte_at or finished) - started) * 1000)
    return {
        "download_mbps": round((received * 8) / elapsed / 1_000_000, 2),
        "download_bytes": received,
        "latency_ms": latency_ms,
    }


def measure_upload(url: str, byte_limit: int, timeout_seconds: int) -> dict:
    payload = b"0" * byte_limit
    started = time.monotonic()
    request = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "User-Agent": "ubiquiti-ops-console/1.0",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        response.read(1024)
    elapsed = max(time.monotonic() - started, 0.001)
    return {
        "upload_mbps": round((byte_limit * 8) / elapsed / 1_000_000, 2),
        "upload_bytes": byte_limit,
    }


def passes_thresholds(result: dict, config: SpeedTestConfig) -> bool:
    if float(result.get("download_mbps") or 0) < config.min_download_mbps:
        return False
    if config.upload_enabled and float(result.get("upload_mbps") or 0) < config.min_upload_mbps:
        return False
    return not result.get("error")
