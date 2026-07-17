from __future__ import annotations

import logging

from .config import Config
from .monitor import Monitor
from .server import serve
from .store import Store


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env()
    logging.getLogger("ubiquiti-ops").info(
        "starting with host=%s port=%s data_path=%s unifi_api_enabled=%s",
        config.app_host,
        config.app_port,
        config.data_path,
        config.unifi_api_enabled,
    )
    store = Store(config.data_path)
    monitor = Monitor(config, store)
    monitor.start()
    logging.getLogger("ubiquiti-ops").info("dashboard listening on http://%s:%s", config.app_host, config.app_port)
    serve(config, store, monitor)
