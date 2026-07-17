from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
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


def row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["ok"] = bool(data["ok"])
    try:
        data["details"] = json.loads(data["details"])
    except json.JSONDecodeError:
        data["details"] = {}
    return data
