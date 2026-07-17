import tempfile
import unittest
from pathlib import Path

from ubiquiti_ops.store import Store


class StoreTests(unittest.TestCase):
    def test_summary_returns_latest_per_target(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(str(Path(temp) / "ops-console.db"))
            store.add_result({
                "kind": "device",
                "target": "192.168.1.1",
                "name": "UDM",
                "sensitivity": "critical",
                "status": "online",
                "ok": True,
                "latency_ms": 5,
                "checked_at": "2026-07-17T10:00:00+00:00",
                "details": {"ping": {"ok": True}},
            })
            store.add_result({
                "kind": "device",
                "target": "192.168.1.1",
                "name": "UDM",
                "sensitivity": "critical",
                "status": "offline",
                "ok": False,
                "latency_ms": 1000,
                "checked_at": "2026-07-17T10:01:00+00:00",
                "details": {"ping": {"ok": False}},
            })
            summary = store.summary()
            self.assertEqual(summary["totals"]["checks"], 1)
            self.assertEqual(summary["totals"]["down"], 1)
            self.assertEqual(summary["totals"]["critical_down"], 1)


if __name__ == "__main__":
    unittest.main()
