from __future__ import annotations

from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config
from .monitor import Monitor
from .store import Store


class OpsHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, config: Config, store: Store, monitor: Monitor, **kwargs):
        self.config = config
        self.store = store
        self.monitor = monitor
        static_dir = Path(__file__).with_name("static")
        super().__init__(*args, directory=str(static_dir), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json({"ok": True})
            return
        if parsed.path == "/api/summary":
            self._json(self.store.summary())
            return
        if parsed.path == "/api/unifi":
            snapshot = self.store.latest_unifi_snapshot()
            snapshot["api_enabled"] = self.config.unifi_api_enabled
            snapshot["api_configured"] = bool(
                self.config.unifi_api_enabled
                and (self.config.unifi_api_key or self.config.unifi_site_manager_api_key)
            )
            self._json(snapshot)
            return
        if parsed.path == "/api/discovery":
            snapshot = self.store.latest_discovery_snapshot()
            snapshot["discovery_enabled"] = self.config.lan_discovery_enabled
            snapshot["discovery_configured"] = bool(
                self.config.lan_discovery_enabled
                and self.config.lan_discovery_subnets
            )
            self._json(snapshot)
            return
        if parsed.path == "/api/speed-test":
            snapshot = self.store.latest_speed_test_snapshot()
            snapshot["speed_test_enabled"] = self.config.speed_test_enabled
            snapshot["speed_test_configured"] = bool(
                self.config.speed_test_enabled
                and self.config.speed_test_download_url
            )
            self._json(snapshot)
            return
        if parsed.path == "/api/speed-test/run":
            self._json(self.monitor.run_speed_test())
            return
        if parsed.path == "/api/history":
            query = parse_qs(parsed.query)
            target = query.get("target", [""])[0]
            limit = _int(query.get("limit", ["50"])[0], 50)
            self._json({"target": target, "history": self.store.history(target, limit)})
            return
        if parsed.path == "/api/run-checks":
            results = self.monitor.run_once()
            self._json({"ok": True, "results": results})
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, format: str, *args) -> None:
        return

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(config: Config, store: Store, monitor: Monitor) -> ThreadingHTTPServer:
    handler = partial(OpsHandler, config=config, store=store, monitor=monitor)
    server = ThreadingHTTPServer((config.app_host, config.app_port), handler)
    server.serve_forever()
    return server


def _int(raw: str, default: int) -> int:
    try:
        return int(raw)
    except ValueError:
        return default
