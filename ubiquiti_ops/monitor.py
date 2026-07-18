from __future__ import annotations

import logging
import threading
import time

from .checks import check_device, check_dns, check_http, check_wan
from .config import Config
from .control_plane import monitor_cycle_event
from .discovery import DiscoveryConfig, discover_lan
from .speedtest import SpeedTestConfig, run_speed_test
from .store import Store
from .unifi_api import UniFiApiClient

LOG = logging.getLogger("ubiquiti-ops.monitor")


class Monitor:
    def __init__(self, config: Config, store: Store):
        self.config = config
        self.store = store
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ops-monitor", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def run_once(self) -> list[dict]:
        results: list[dict] = []
        for device in self.config.watched_devices:
            results.append(check_device(device))
        for target in self.config.wan_targets:
            results.append(check_wan(target))
        for hostname in self.config.dns_lookups:
            results.append(check_dns(hostname))
        for target in self.config.http_checks:
            results.append(check_http(target))

        for result in results:
            self.store.add_result(result)
        if self.config.unifi_api_enabled:
            snapshot = UniFiApiClient.from_app_config(self.config).collect()
            self.store.add_unifi_snapshot(snapshot)
        if self.config.lan_discovery_enabled:
            snapshot = discover_lan(DiscoveryConfig(
                subnets=self.config.lan_discovery_subnets,
                ports=self.config.lan_discovery_ports,
                max_hosts=self.config.lan_discovery_max_hosts,
            ))
            self.store.add_discovery_snapshot(snapshot)
        return results

    def run_speed_test(self) -> dict:
        snapshot = run_speed_test(SpeedTestConfig(
            enabled=self.config.speed_test_enabled,
            download_url=self.config.speed_test_download_url,
            upload_url=self.config.speed_test_upload_url,
            upload_enabled=self.config.speed_test_upload_enabled,
            download_bytes=self.config.speed_test_download_bytes,
            upload_bytes=self.config.speed_test_upload_bytes,
            timeout_seconds=self.config.speed_test_timeout_seconds,
            min_download_mbps=self.config.speed_test_min_download_mbps,
            min_upload_mbps=self.config.speed_test_min_upload_mbps,
        ))
        self.store.add_speed_test_snapshot(snapshot)
        return snapshot

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                results = self.run_once()
                self.store.add_control_plane_event(monitor_cycle_event(
                    "healthy",
                    f"Completed {len(results)} configured health checks.",
                    {"check_count": len(results)},
                ))
                LOG.info("completed %s operations checks", len(results))
            except Exception as exc:
                self.store.add_control_plane_event(monitor_cycle_event(
                    "failing",
                    str(exc) or "Monitor cycle failed.",
                    {"exception": exc.__class__.__name__},
                ))
                LOG.exception("operations check failed")
            self._stop.wait(self.config.check_interval_seconds)
