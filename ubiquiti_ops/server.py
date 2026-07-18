from __future__ import annotations

from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config
from .control_plane import monitor_cycle_event
from .infrastructure import build_infrastructure_summary
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
        if parsed.path == "/api/unifi/history":
            query = parse_qs(parsed.query)
            limit = _int(query.get("limit", ["120"])[0], 120)
            self._json(self.store.unifi_history(limit))
            return
        if parsed.path == "/api/unifi/actions":
            query = parse_qs(parsed.query)
            limit = _int(query.get("limit", ["50"])[0], 50)
            payload = self.store.unifi_actions(limit)
            payload["write_actions_enabled"] = self.config.unifi_write_actions_enabled
            self._json(payload)
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
        if parsed.path == "/api/timeline":
            query = parse_qs(parsed.query)
            limit = _int(query.get("limit", ["100"])[0], 100)
            self._json(self.store.network_timeline(limit))
            return
        if parsed.path == "/api/control-plane":
            query = parse_qs(parsed.query)
            limit = _int(query.get("limit", ["100"])[0], 100)
            self._json(self.store.control_plane_summary(limit))
            return
        if parsed.path == "/api/infrastructure":
            self._json(build_infrastructure_summary(
                self.config.infrastructure_devices,
                self.store.summary(),
                self.store.latest_unifi_snapshot(),
            ))
            return
        if parsed.path == "/api/run-checks":
            results = self.monitor.run_once()
            self.store.add_control_plane_event(monitor_cycle_event(
                "healthy",
                f"Manual check completed {len(results)} configured health checks.",
                {"check_count": len(results), "manual": True},
            ))
            self._json({"ok": True, "results": results})
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/entities/update":
            try:
                payload = self._read_json()
                override = self.store.update_entity_override(
                    payload.get("kind", ""),
                    payload.get("entity_id", ""),
                    payload,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._json({"ok": True, "override": override})
            return
        if parsed.path == "/api/unifi/action":
            try:
                payload = self._read_json()
                action = self._prepare_unifi_action(payload)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            recorded = self.store.record_unifi_action(action)
            self._json({"ok": True, "action": recorded})
            return
        self._json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _prepare_unifi_action(self, payload: dict) -> dict:
        allowed_actions = {
            "reboot_device",
            "update_firmware",
            "toggle_poe",
            "set_port_profile",
            "set_port_speed",
            "block_client",
            "unblock_client",
            "assign_client_vlan",
            "apply_firewall_rule",
            "apply_wifi_setting",
            "apply_acl",
        }
        action = str(payload.get("action") or "").strip()
        target_kind = str(payload.get("target_kind") or "").strip()
        target_id = str(payload.get("target_id") or "").strip()
        if action not in allowed_actions:
            raise ValueError("Unsupported UniFi action")
        if target_kind not in {"device", "client", "port", "policy"}:
            raise ValueError("target_kind must be device, client, port, or policy")
        if not target_id:
            raise ValueError("target_id is required")

        confirmation = str(payload.get("confirmation") or "").strip()
        enabled = self.config.unifi_write_actions_enabled
        confirmed = confirmation == self.config.unifi_write_actions_confirmation
        status = "recorded"
        message = (
            "Local operation request recorded. Controller write execution is intentionally gated in this build."
        )
        if not enabled:
            status = "blocked"
            message = "Write actions are disabled. Set UNIFI_WRITE_ACTIONS_ENABLED=true only during a planned maintenance window."
        elif not confirmed:
            status = "blocked"
            message = "Confirmation token did not match UNIFI_WRITE_ACTIONS_CONFIRMATION."

        return {
            "action": action,
            "target_kind": target_kind,
            "target_id": target_id,
            "status": status,
            "message": message,
            "params": payload.get("params", {}) if isinstance(payload.get("params", {}), dict) else {},
            "write_actions_enabled": enabled,
            "confirmed": confirmed,
        }

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
