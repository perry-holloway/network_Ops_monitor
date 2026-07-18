from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import threading
from typing import Iterator

from .control_plane import recommendations, snapshot_events


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS unifi_action_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requested_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_kind TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_unifi_action_requests_time ON unifi_action_requests(requested_at DESC)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS control_plane_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_control_plane_events_time ON control_plane_events(timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_control_plane_events_source_time ON control_plane_events(source, timestamp DESC)")

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

    def network_timeline(self, limit: int = 100) -> dict:
        limit = max(1, min(int(limit or 100), 500))
        events: list[dict] = []
        with self._lock, self._connection() as conn:
            check_rows = conn.execute(
                """
                SELECT *
                FROM checks
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit * 4,),
            ).fetchall()
            unifi_rows = conn.execute(
                """
                SELECT *
                FROM unifi_snapshots
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            speed_rows = conn.execute(
                """
                SELECT *
                FROM speed_test_snapshots
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            override_rows = conn.execute(
                """
                SELECT *
                FROM entity_overrides
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        events.extend(_check_timeline_events([row_to_dict(row) for row in reversed(check_rows)]))
        events.extend(_unifi_timeline_events(list(reversed(unifi_rows))))
        events.extend(_speed_timeline_events(list(reversed(speed_rows))))
        events.extend(_override_timeline_events(list(override_rows)))
        events = sorted(events, key=lambda event: event.get("timestamp") or "", reverse=True)
        return {
            "configured": bool(events),
            "count": len(events[:limit]),
            "events": events[:limit],
            "totals": dict(Counter(event.get("severity", "info") for event in events[:limit])),
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
        self.record_control_plane_snapshot(snapshot)

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

    def record_unifi_action(self, action: dict) -> dict:
        requested_at = action.get("requested_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = dict(action)
        payload["requested_at"] = requested_at
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO unifi_action_requests
                    (requested_at, action, target_kind, target_id, status, message, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    requested_at,
                    payload.get("action", ""),
                    payload.get("target_kind", ""),
                    payload.get("target_id", ""),
                    payload.get("status", "queued"),
                    payload.get("message", ""),
                    json.dumps(payload, sort_keys=True),
                ),
            )
            payload["id"] = cursor.lastrowid
        return payload

    def unifi_actions(self, limit: int = 50) -> dict:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM unifi_action_requests
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit or 50), 200)),),
            ).fetchall()
        actions = []
        for row in rows:
            payload = _safe_json(row["payload"])
            payload["id"] = row["id"]
            payload["requested_at"] = row["requested_at"]
            payload["action"] = row["action"]
            payload["target_kind"] = row["target_kind"]
            payload["target_id"] = row["target_id"]
            payload["status"] = row["status"]
            payload["message"] = row["message"]
            actions.append(payload)
        return {"count": len(actions), "actions": actions}

    def add_control_plane_event(self, event: dict) -> dict:
        payload = {
            "timestamp": event.get("timestamp") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": str(event.get("source") or "unknown"),
            "kind": str(event.get("kind") or "unknown"),
            "status": str(event.get("status") or "unknown"),
            "severity": str(event.get("severity") or "info"),
            "title": str(event.get("title") or "Control plane event"),
            "message": str(event.get("message") or ""),
            "details": event.get("details", {}) if isinstance(event.get("details", {}), dict) else {},
        }
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO control_plane_events
                    (timestamp, source, kind, status, severity, title, message, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["timestamp"],
                    payload["source"],
                    payload["kind"],
                    payload["status"],
                    payload["severity"],
                    payload["title"],
                    payload["message"],
                    json.dumps(payload["details"], sort_keys=True),
                ),
            )
            payload["id"] = cursor.lastrowid
        return payload

    def record_control_plane_snapshot(self, snapshot: dict) -> list[dict]:
        recorded = []
        for event in snapshot_events(snapshot):
            recorded.append(self.add_control_plane_event(event))
        return recorded

    def control_plane_summary(self, limit: int = 100) -> dict:
        max_limit = max(1, min(int(limit or 100), 500))
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM control_plane_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (max_limit,),
            ).fetchall()

        events = [_control_plane_row(row) for row in rows]
        latest_by_source: dict[str, dict] = {}
        for event in events:
            latest_by_source.setdefault(event["source"], event)
        failing = [event for event in latest_by_source.values() if event.get("status") == "failing"]
        degraded = [event for event in latest_by_source.values() if event.get("status") == "degraded"]
        healthy = [event for event in latest_by_source.values() if event.get("status") == "healthy"]
        latest = events[0] if events else {}
        return {
            "configured": bool(events),
            "status": "failing" if failing else "degraded" if degraded else "healthy" if healthy else "unknown",
            "latest": latest,
            "sources": latest_by_source,
            "totals": {
                "sources": len(latest_by_source),
                "healthy": len(healthy),
                "degraded": len(degraded),
                "failing": len(failing),
                "events": len(events),
            },
            "recommendations": recommendations(latest_by_source),
            "events": events,
        }

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


def _control_plane_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["details"] = _safe_json(data.get("details", ""))
    return data


def _check_timeline_events(rows: list[dict]) -> list[dict]:
    events: list[dict] = []
    previous: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row.get("kind", ""), row.get("target", ""))
        prior = previous.get(key)
        if prior and prior.get("ok") != row.get("ok"):
            recovered = bool(row.get("ok"))
            events.append(_timeline_event(
                timestamp=row.get("checked_at", ""),
                kind="check_recovered" if recovered else "check_failed",
                severity="success" if recovered else "critical",
                title=f"{row.get('name') or row.get('target')} {'recovered' if recovered else 'went down'}",
                summary=f"{row.get('kind')} check changed from {prior.get('status')} to {row.get('status')}.",
                entity_kind="check",
                entity_id=row.get("target", ""),
                details={
                    "target": row.get("target", ""),
                    "kind": row.get("kind", ""),
                    "latency_ms": row.get("latency_ms", 0),
                    "status": row.get("status", ""),
                },
            ))
        if not row.get("ok"):
            events.append(_timeline_event(
                timestamp=row.get("checked_at", ""),
                kind="check_down",
                severity="critical" if row.get("sensitivity") in {"critical", "high"} else "warning",
                title=f"{row.get('name') or row.get('target')} reported {row.get('status')}",
                summary=f"{row.get('kind')} check for {row.get('target')} is not healthy.",
                entity_kind="check",
                entity_id=row.get("target", ""),
                details={
                    "target": row.get("target", ""),
                    "kind": row.get("kind", ""),
                    "latency_ms": row.get("latency_ms", 0),
                    "status": row.get("status", ""),
                },
            ))
        previous[key] = row
    return events


def _unifi_timeline_events(rows: list[sqlite3.Row]) -> list[dict]:
    events: list[dict] = []
    previous_devices: dict[str, dict] = {}
    previous_clients: dict[str, dict] = {}
    for row in rows:
        payload = _safe_json(row["payload"])
        timestamp = payload.get("checked_at") or row["checked_at"]
        devices = _entity_map([*(payload.get("site_manager_devices", []) or []), *(payload.get("devices", []) or [])])
        clients = _entity_map(payload.get("clients", []) or [])
        insights = payload.get("traffic_insights", {}) or {}

        for key, device in devices.items():
            prior = previous_devices.get(key)
            if not prior:
                events.append(_timeline_event(
                    timestamp=timestamp,
                    kind="device_seen",
                    severity="info",
                    title=f"{_entity_name(device, 'device')} appeared in UniFi inventory",
                    summary=f"{device.get('model') or device.get('type') or 'Device'} {device.get('ipAddress') or device.get('ip') or ''}".strip(),
                    entity_kind="device",
                    entity_id=key,
                    details={"model": device.get("model", ""), "ip": device.get("ipAddress") or device.get("ip") or ""},
                ))
            elif _device_is_online(prior) != _device_is_online(device):
                online = _device_is_online(device)
                events.append(_timeline_event(
                    timestamp=timestamp,
                    kind="device_online" if online else "device_offline",
                    severity="success" if online else "critical",
                    title=f"{_entity_name(device, 'device')} {'came online' if online else 'went offline'}",
                    summary=f"State changed from {_device_status(prior)} to {_device_status(device)}.",
                    entity_kind="device",
                    entity_id=key,
                    details={"previous": _device_status(prior), "current": _device_status(device)},
                ))

        for key, client in clients.items():
            prior = previous_clients.get(key)
            if not prior:
                events.append(_timeline_event(
                    timestamp=timestamp,
                    kind="client_seen",
                    severity="info" if _client_is_trusted(client) else "warning",
                    title=f"{_entity_name(client, 'client')} appeared on the network",
                    summary=f"{client.get('ipAddress') or client.get('ip') or 'No IP'} / {client.get('macAddress') or client.get('mac') or 'No MAC'}",
                    entity_kind="client",
                    entity_id=key,
                    details={"trusted": _client_is_trusted(client), "type": client.get("type", "")},
                ))
            elif _client_is_trusted(prior) != _client_is_trusted(client):
                trusted = _client_is_trusted(client)
                events.append(_timeline_event(
                    timestamp=timestamp,
                    kind="client_trust_changed",
                    severity="success" if trusted else "warning",
                    title=f"{_entity_name(client, 'client')} trust status changed",
                    summary=f"Client is now {'trusted' if trusted else 'untrusted / review'}.",
                    entity_kind="client",
                    entity_id=key,
                    details={"trusted": trusted},
                ))

        for key, prior in previous_clients.items():
            if key not in clients:
                events.append(_timeline_event(
                    timestamp=timestamp,
                    kind="client_disappeared",
                    severity="info",
                    title=f"{_entity_name(prior, 'client')} disappeared from the latest client list",
                    summary=f"Previously seen as {prior.get('ipAddress') or prior.get('ip') or 'No IP'}.",
                    entity_kind="client",
                    entity_id=key,
                    details={"last_ip": prior.get("ipAddress") or prior.get("ip") or ""},
                ))

        if int(insights.get("stressed_device_count") or 0):
            events.append(_timeline_event(
                timestamp=timestamp,
                kind="stressed_devices",
                severity="warning",
                title="UniFi reported stressed infrastructure devices",
                summary=f"{int(insights.get('stressed_device_count') or 0)} device(s) showed high CPU or memory.",
                entity_kind="unifi",
                entity_id=payload.get("site_id", ""),
                details={"stressed_device_count": int(insights.get("stressed_device_count") or 0)},
            ))

        previous_devices = devices
        previous_clients = clients
    return events


def _speed_timeline_events(rows: list[sqlite3.Row]) -> list[dict]:
    events: list[dict] = []
    previous: dict | None = None
    for row in rows:
        payload = _safe_json(row["payload"])
        timestamp = payload.get("checked_at") or row["checked_at"]
        ok = bool(payload.get("ok", row["ok"]))
        if not ok:
            events.append(_timeline_event(
                timestamp=timestamp,
                kind="speed_test_failed",
                severity="warning",
                title="Speed test reported a problem",
                summary=payload.get("error") or row["error"] or "Speed test failed.",
                entity_kind="speed",
                entity_id="wan",
                details={"error": payload.get("error") or row["error"] or ""},
            ))
        if previous:
            prior_download = float(previous.get("download_mbps") or 0)
            download = float(payload.get("download_mbps") or row["download_mbps"] or 0)
            if prior_download and download < prior_download * 0.6:
                events.append(_timeline_event(
                    timestamp=timestamp,
                    kind="speed_drop",
                    severity="warning",
                    title="WAN download speed dropped",
                    summary=f"Download changed from {prior_download:.1f} Mbps to {download:.1f} Mbps.",
                    entity_kind="speed",
                    entity_id="wan",
                    details={"previous_download_mbps": prior_download, "download_mbps": download},
                ))
        previous = {
            "download_mbps": payload.get("download_mbps") or row["download_mbps"],
            "upload_mbps": payload.get("upload_mbps") or row["upload_mbps"],
        }
    return events


def _override_timeline_events(rows: list[sqlite3.Row]) -> list[dict]:
    events: list[dict] = []
    for row in rows:
        name = row["display_name"] or row["entity_id"]
        fields = [
            label
            for label in ["display_name", "owner", "location", "category", "trusted", "notes"]
            if row[label]
        ]
        events.append(_timeline_event(
            timestamp=row["updated_at"],
            kind="inventory_updated",
            severity="info",
            title=f"{name} inventory metadata was updated",
            summary=f"Updated fields: {', '.join(fields) if fields else 'metadata'}",
            entity_kind=row["kind"],
            entity_id=row["entity_id"],
            details={
                "display_name": row["display_name"],
                "owner": row["owner"],
                "location": row["location"],
                "category": row["category"],
                "trusted": row["trusted"],
            },
        ))
    return events


def _timeline_event(
    *,
    timestamp: str,
    kind: str,
    severity: str,
    title: str,
    summary: str,
    entity_kind: str,
    entity_id: str,
    details: dict,
) -> dict:
    return {
        "timestamp": timestamp,
        "kind": kind,
        "severity": severity,
        "title": title,
        "summary": summary,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "details": details,
    }


def _safe_json(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _entity_map(items: list[dict]) -> dict[str, dict]:
    output: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _entity_key(item)
        if key and key not in output:
            output[key] = item
    return output


def _entity_name(item: dict, fallback: str) -> str:
    return str(
        item.get("localDisplayName")
        or item.get("trustedName")
        or item.get("name")
        or item.get("hostname")
        or item.get("displayName")
        or item.get("macAddress")
        or item.get("mac")
        or item.get("id")
        or f"Unknown {fallback}"
    )


def _device_status(device: dict) -> str:
    status = device.get("status") or device.get("state") or device.get("stateText") or device.get("online")
    if status is True:
        return "online"
    if status is False:
        return "offline"
    return str(status or "unknown")


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
