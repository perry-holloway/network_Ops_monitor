from __future__ import annotations

import logging
import threading
import time

from .checks import check_device, check_dns, check_http, check_wan
from .config import Config
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
        return results

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                results = self.run_once()
                LOG.info("completed %s operations checks", len(results))
            except Exception:
                LOG.exception("operations check failed")
            self._stop.wait(self.config.check_interval_seconds)
