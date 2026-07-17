from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import threading
from typing import Iterator


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    target TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    checked_at TEXT NOT NULL,
                    details TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_target_time ON checks(target, checked_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_checks_kind_time ON checks(kind, checked_at DESC)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS unifi_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checked_at TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    site_id TEXT NOT NULL,
                    device_count INTEGER NOT NULL,
                    client_count INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_unifi_snapshots_time ON unifi_snapshots(checked_at DESC)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS discovery_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checked_at TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    scanned_hosts INTEGER NOT NULL,
                    device_count INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_discovery_snapshots_time ON discovery_snapshots(checked_at DESC)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS speed_test_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checked_at TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    download_mbps REAL NOT NULL,
                    upload_mbps REAL,
                    latency_ms INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_speed_test_snapshots_time ON speed_test_snapshots(checked_at DESC)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entity_overrides (
                    kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    trusted TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, entity_id)
                )
                """
            )

    def add_result(self, result: dict) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO checks (kind, target, name, sensitivity, status, ok, latency_ms, checked_at, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["kind"],
                    result["target"],
                    result["name"],
                    result.get("sensitivity", "normal"),
                    result["status"],
                    1 if result["ok"] else 0,
                    int(result.get("latency_ms") or 0),
                    result["checked_at"],
                    json.dumps(result.get("details", {}), sort_keys=True),
                ),
            )

    def latest(self) -> list[dict]:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT c.*
                FROM checks c
                INNER JOIN (
                    SELECT kind, target, MAX(id) AS max_id
                    FROM checks
                    GROUP BY kind, target
                ) latest ON c.id = latest.max_id
                ORDER BY c.kind, c.name
                """
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def history(self, target: str, limit: int = 50) -> list[dict]:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM checks
                WHERE target = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (target, limit),
            ).fetchall()
        return [row_to_dict(row) for row in rows]

    def summary(self) -> dict:
        latest = self.latest()
        counts = Counter("ok" if item["ok"] else "down" for item in latest)
        critical_down = [
            item for item in latest
            if not item["ok"] and item.get("sensitivity") in {"critical", "high"}
        ]
        return {
            "totals": {
                "checks": len(latest),
                "ok": counts["ok"],
                "down": counts["down"],
                "critical_down": len(critical_down),
            },
            "latest": latest,
            "critical_down": critical_down,
        }

    def add_unifi_snapshot(self, snapshot: dict) -> None:
        insights = snapshot.get("traffic_insights", {})
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO unifi_snapshots
                    (checked_at, ok, site_id, device_count, client_count, latency_ms, error, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.get("checked_at", ""),
                    1 if snapshot.get("ok") else 0,
                    snapshot.get("site_id", ""),
                    len(snapshot.get("devices", [])) or int(insights.get("device_count") or 0),
                    len(snapshot.get("clients", [])) or int(insights.get("client_count") or 0),
                    int(snapshot.get("latency_ms") or 0),
                    snapshot.get("error", ""),
                    json.dumps(snapshot, sort_keys=True),
                ),
            )

    def latest_unifi_snapshot(self) -> dict:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM unifi_snapshots
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return {
                "configured": False,
                "ok": False,
                "checked_at": "",
                "site_id": "",
                "devices": [],
                "clients": [],
                "device_statistics": [],
                "traffic_insights": {
                    "client_count": 0,
                    "device_count": 0,
                    "active_client_count": 0,
                    "total_client_rx_bytes": 0,
                    "total_client_tx_bytes": 0,
                    "total_client_bytes": 0,
                    "total_device_rx_rate_bps": 0,
                    "total_device_tx_rate_bps": 0,
                    "total_device_rate_bps": 0,
                    "stressed_device_count": 0,
                    "top_clients": [],
                    "top_devices": [],
                },
                "error": "No UniFi API snapshot has been collected yet.",
            }
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = {}
        payload = self.apply_entity_overrides(payload)
        payload["configured"] = True
        return payload

    def unifi_history(self, limit: int = 120) -> dict:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM unifi_snapshots
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()

        points = []
        for row in reversed(rows):
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                payload = {}
            insights = payload.get("traffic_insights", {})
            devices = payload.get("devices", [])
            site_manager_devices = payload.get("site_manager_devices", [])
            offline_devices = len([
                device for device in site_manager_devices
                if not _device_is_online(device)
            ])
            clients = payload.get("clients", [])
            untrusted_clients = len([
                client for client in clients
                if not _client_is_trusted(client)
            ])
            point = {
                "checked_at": payload.get("checked_at") or row["checked_at"],
                "ok": bool(payload.get("ok", row["ok"])),
                "site_id": payload.get("site_id") or row["site_id"],
                "latency_ms": int(payload.get("latency_ms") or row["latency_ms"] or 0),
                "device_count": len(devices) or len(site_manager_devices) or int(insights.get("device_count") or row["device_count"] or 0),
                "client_count": len(clients) or int(insights.get("client_count") or row["client_count"] or 0),
                "active_client_count": int(insights.get("active_client_count") or 0),
                "total_client_bytes": int(insights.get("total_client_bytes") or 0),
                "total_client_rx_bytes": int(insights.get("total_client_rx_bytes") or 0),
                "total_client_tx_bytes": int(insights.get("total_client_tx_bytes") or 0),
                "total_device_rate_bps": int(insights.get("total_device_rate_bps") or 0),
                "total_device_rx_rate_bps": int(insights.get("total_device_rx_rate_bps") or 0),
                "total_device_tx_rate_bps": int(insights.get("total_device_tx_rate_bps") or 0),
                "stressed_device_count": int(insights.get("stressed_device_count") or 0),
                "offline_device_count": offline_devices,
                "untrusted_client_count": untrusted_clients,
                "top_client_name": _top_name(insights.get("top_clients", [])),
                "top_device_name": _top_name(insights.get("top_devices", [])),
                "error": payload.get("error") or row["error"],
            }
            points.append(point)

        return {
            "configured": bool(points),
            "count": len(points),
            "points": points,
            "trends": summarize_unifi_trends(points),
        }

    def entity_overrides(self) -> dict[str, dict[str, dict]]:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM entity_overrides
                ORDER BY updated_at DESC
                """
            ).fetchall()
        overrides: dict[str, dict[str, dict]] = {"device": {}, "client": {}}
        for row in rows:
            kind = row["kind"]
            if kind not in overrides:
                overrides[kind] = {}
            override = {
                "kind": kind,
                "entity_id": row["entity_id"],
                "display_name": row["display_name"],
                "owner": row["owner"],
                "location": row["location"],
                "category": row["category"],
                "trusted": _trusted_value(row["trusted"]),
                "notes": row["notes"],
                "updated_at": row["updated_at"],
            }
            overrides[kind][row["entity_id"]] = override
        return overrides

    def update_entity_override(self, kind: str, entity_id: str, updates: dict) -> dict:
        kind = kind if kind in {"device", "client"} else ""
        entity_id = str(entity_id or "").strip()
        if not kind or not entity_id:
            raise ValueError("kind and entity_id are required")

        existing = self.entity_overrides().get(kind, {}).get(entity_id, {})
        trusted = updates.get("trusted", existing.get("trusted", ""))
        if isinstance(trusted, bool):
            trusted_value = "true" if trusted else "false"
        else:
            trusted_value = str(trusted or "").strip().lower()
            if trusted_value not in {"", "true", "false"}:
                trusted_value = ""

        override = {
            "kind": kind,
            "entity_id": entity_id,
            "display_name": _clean_override_text(updates.get("display_name", existing.get("display_name", ""))),
            "owner": _clean_override_text(updates.get("owner", existing.get("owner", ""))),
            "location": _clean_override_text(updates.get("location", existing.get("location", ""))),
            "category": _clean_override_text(updates.get("category", existing.get("category", ""))),
            "trusted": trusted_value,
            "notes": _clean_override_text(updates.get("notes", existing.get("notes", "")), 2000),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO entity_overrides
                    (kind, entity_id, display_name, owner, location, category, trusted, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, entity_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    owner=excluded.owner,
                    location=excluded.location,
                    category=excluded.category,
                    trusted=excluded.trusted,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    override["kind"],
                    override["entity_id"],
                    override["display_name"],
                    override["owner"],
                    override["location"],
                    override["category"],
                    override["trusted"],
                    override["notes"],
                    override["updated_at"],
                ),
            )
        return override

    def apply_entity_overrides(self, snapshot: dict) -> dict:
        overrides = self.entity_overrides()
        snapshot = dict(snapshot)
        snapshot["local_overrides"] = overrides
        for collection, kind in [
            ("devices", "device"),
            ("site_manager_devices", "device"),
            ("clients", "client"),
        ]:
            snapshot[collection] = [
                _apply_override_to_entity(item, overrides.get(kind, {}), kind)
                for item in snapshot.get(collection, [])
            ]
        insights = dict(snapshot.get("traffic_insights", {}))
        insights["top_devices"] = [
            _apply_override_to_entity(item, overrides.get("device", {}), "device")
            for item in insights.get("top_devices", [])
        ]
        insights["top_clients"] = [
            _apply_override_to_entity(item, overrides.get("client", {}), "client")
            for item in insights.get("top_clients", [])
        ]
        snapshot["traffic_insights"] = insights
        return snapshot

    def add_discovery_snapshot(self, snapshot: dict) -> None:
        payload = dict(snapshot)
        payload["device_count"] = len(payload.get("devices", [])) or int(payload.get("device_count") or 0)
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO discovery_snapshots
                    (checked_at, ok, scanned_hosts, device_count, latency_ms, error, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.get("checked_at", ""),
                    1 if snapshot.get("ok") else 0,
                    int(snapshot.get("scanned_hosts") or 0),
                    payload["device_count"],
                    int(snapshot.get("latency_ms") or 0),
                    snapshot.get("error", ""),
                    json.dumps(payload, sort_keys=True),
                ),
            )

    def latest_discovery_snapshot(self) -> dict:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM discovery_snapshots
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return {
                "configured": False,
                "ok": False,
                "checked_at": "",
                "subnets": [],
                "ports": [],
                "scanned_hosts": 0,
                "device_count": 0,
                "devices": [],
                "errors": [],
                "error": "No LAN discovery snapshot has been collected yet.",
            }
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = {}
        payload["configured"] = True
        return payload

    def add_speed_test_snapshot(self, snapshot: dict) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO speed_test_snapshots
                    (checked_at, ok, download_mbps, upload_mbps, latency_ms, error, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.get("checked_at", ""),
                    1 if snapshot.get("ok") else 0,
                    float(snapshot.get("download_mbps") or 0),
                    snapshot.get("upload_mbps"),
                    int(snapshot.get("latency_ms") or 0),
                    snapshot.get("error", ""),
                    json.dumps(snapshot, sort_keys=True),
                ),
            )

    def latest_speed_test_snapshot(self) -> dict:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM speed_test_snapshots
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return {
                "configured": False,
                "ok": False,
                "checked_at": "",
                "download_mbps": 0.0,
                "upload_mbps": None,
                "latency_ms": 0,
                "download_bytes": 0,
                "upload_bytes": 0,
                "duration_ms": 0,
                "upload_enabled": False,
                "thresholds": {},
                "error": "No speed test has been run yet.",
            }
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = {}
        payload["configured"] = True
        return payload


def row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["ok"] = bool(data["ok"])
    try:
        data["details"] = json.loads(data["details"])
    except json.JSONDecodeError:
        data["details"] = {}
    return data


def summarize_unifi_trends(points: list[dict]) -> dict:
    if not points:
        return {
            "latest": {},
            "previous": {},
            "traffic_delta_bytes": 0,
            "device_rate_delta_bps": 0,
            "client_count_delta": 0,
            "offline_device_delta": 0,
            "alerts": [],
        }

    latest = points[-1]
    previous = points[-2] if len(points) > 1 else {}
    traffic_delta = int(latest.get("total_client_bytes") or 0) - int(previous.get("total_client_bytes") or 0)
    rate_delta = int(latest.get("total_device_rate_bps") or 0) - int(previous.get("total_device_rate_bps") or 0)
    client_delta = int(latest.get("client_count") or 0) - int(previous.get("client_count") or 0)
    offline_delta = int(latest.get("offline_device_count") or 0) - int(previous.get("offline_device_count") or 0)

    alerts: list[dict] = []
    if traffic_delta > 100_000_000:
        alerts.append({
            "kind": "traffic_spike",
            "severity": "info",
            "message": "Client traffic increased sharply since the previous UniFi snapshot.",
            "value": traffic_delta,
        })
    if rate_delta > 5_000_000:
        alerts.append({
            "kind": "throughput_spike",
            "severity": "info",
            "message": "Device throughput is higher than the previous UniFi snapshot.",
            "value": rate_delta,
        })
    if offline_delta > 0:
        alerts.append({
            "kind": "offline_devices",
            "severity": "warning",
            "message": "More UniFi devices are offline than in the previous snapshot.",
            "value": offline_delta,
        })
    if int(latest.get("stressed_device_count") or 0):
        alerts.append({
            "kind": "stressed_devices",
            "severity": "warning",
            "message": "One or more devices reported high CPU or memory.",
            "value": int(latest.get("stressed_device_count") or 0),
        })

    return {
        "latest": latest,
        "previous": previous,
        "traffic_delta_bytes": traffic_delta,
        "device_rate_delta_bps": rate_delta,
        "client_count_delta": client_delta,
        "offline_device_delta": offline_delta,
        "alerts": alerts,
    }


def _top_name(items: object) -> str:
    if not isinstance(items, list) or not items:
        return ""
    first = items[0]
    if not isinstance(first, dict):
        return ""
    return str(first.get("name") or first.get("id") or first.get("mac") or "")


def _client_is_trusted(client: dict) -> bool:
    return client.get("trusted") is True or str(client.get("trusted") or "").lower() == "true"


def _device_is_online(device: dict) -> bool:
    status = device.get("status") or device.get("state") or device.get("stateText") or device.get("online")
    if status is True:
        return True
    if status is False:
        return False
    return str(status or "").lower() in {"online", "connected", "active", "up", "true"}


def _entity_key(item: dict) -> str:
    return str(
        item.get("id")
        or item.get("deviceId")
        or item.get("macAddress")
        or item.get("mac")
        or item.get("normalizedMac")
        or item.get("ipAddress")
        or item.get("ip")
        or item.get("name")
        or ""
    )


def _trusted_value(value: object) -> bool | str:
    if value is True or str(value or "").lower() == "true":
        return True
    if value is False or str(value or "").lower() == "false":
        return False
    return ""


def _clean_override_text(value: object, limit: int = 240) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _apply_override_to_entity(item: dict, overrides: dict[str, dict], kind: str) -> dict:
    if not isinstance(item, dict):
        return item
    entity_id = _entity_key(item)
    override = overrides.get(entity_id)
    if not override:
        return item
    entity = dict(item)
    entity["localOverride"] = override
    if override.get("display_name"):
        entity["localDisplayName"] = override["display_name"]
    if override.get("owner"):
        entity["owner"] = override["owner"]
    if override.get("location"):
        entity["location"] = override["location"]
    if override.get("category"):
        entity["category"] = override["category"]
        if kind == "client":
            entity["trustedCategory"] = override["category"]
    if override.get("notes"):
        entity["notes"] = override["notes"]
    if kind == "client" and override.get("trusted") != "":
        entity["trusted"] = bool(override["trusted"])
    return entity
