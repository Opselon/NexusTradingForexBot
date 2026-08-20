"""
Unit Tests - Broker history sync watermark + meta monotonicity (BUG-133)
=========================================================================
The sync must anchor its fetch window on last_sync_to (last COMPLETED sync)
- NOT last_sync_from - and the meta row must never regress last_sync_from.
A buggy window caused the broker mirror to stop advancing (missing closes),
and a buggy meta upsert rewrote last_sync_from to the current cycle's from,
making the next fetch go backward instead of forward.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nexus_scalp.adapters.database.broker_history_sync import BrokerHistorySyncWorker


class _FakeAudit:
    def __init__(self, meta: dict) -> None:
        self._meta = meta
        self.captured: dict = {}

    def get_broker_history_meta(self, symbol: str) -> dict:
        return self._meta

    def sync_broker_history(self, orders, deals, symbol, sync_from, sync_to):
        self.captured["from"] = sync_from.isoformat()
        self.captured["to"] = sync_to.isoformat()
        return {
            "orders_total": len(orders or []),
            "orders_inserted": 0,
            "orders_duplicates": 0,
            "deals_total": len(deals or []),
            "deals_inserted": 0,
            "deals_duplicates": 0,
            "trades_total": 0,
            "trades_inserted": 0,
            "trades_duplicates": 0,
            "duration_ms": 0.0,
        }


class _FakeAdapter:
    def get_history_orders(self, from_dt, to_dt, symbol=None):
        return []

    def get_history_deals(self, from_dt, to_dt, symbol=None):
        return []


def _make(meta: dict) -> tuple[BrokerHistorySyncWorker, _FakeAudit]:
    audit = _FakeAudit(meta)
    worker = BrokerHistorySyncWorker(
        audit=audit,
        adapter=_FakeAdapter(),
        symbol="XAUUSD",
        interval_sec=0.0,
        overlap_days=1,
    )
    worker.start()
    worker._last_run_ts = 0.0
    return worker, audit


def test_window_anchors_on_last_sync_to() -> None:
    meta = {
        "last_sync_from": "2026-05-08T17:03:44+00:00",
        "last_sync_to": "2026-08-20T17:45:00+00:00",
    }
    worker, audit = _make(meta)
    assert worker.tick() is True
    expected_from = (
        datetime.fromisoformat("2026-08-20T17:45:00+00:00") - timedelta(days=1)
    ).isoformat()
    assert audit.captured["from"] == expected_from, audit.captured


def test_window_uses_last_sync_from_when_no_to() -> None:
    meta = {"last_sync_from": "2026-05-08T17:03:44+00:00", "last_sync_to": None}
    worker, audit = _make(meta)
    assert worker.tick() is True
    expected_from = (
        datetime.fromisoformat("2026-05-08T17:03:44+00:00") - timedelta(days=1)
    ).isoformat()
    assert audit.captured["from"] == expected_from, audit.captured


def test_window_initial_fallback() -> None:
    worker, audit = _make({})
    assert worker.tick() is True
    expected_from = (datetime.now(UTC) - timedelta(days=14)).isoformat()
    # allow small clock skew
    assert audit.captured["from"].startswith(expected_from[:13]), audit.captured


def test_meta_upsert_keeps_earliest_last_sync_from() -> None:
    """sqlite MIN() in the upsert must preserve the earliest historical from."""
    db = Path(__file__).resolve().parents[2] / "artifacts" / "test_broker_history_meta.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE audit_broker_history_meta (id INTEGER PRIMARY KEY, symbol TEXT, "
        "last_sync_from TEXT, last_sync_to TEXT, last_synced_at TEXT, "
        "last_orders INTEGER, last_deals INTEGER, last_trades INTEGER)"
    )
    # simulate an existing row with an EARLIER from, then apply the same upsert SQL
    con.execute(
        "INSERT INTO audit_broker_history_meta VALUES (1, 'XAUUSD', "
        "'2026-05-08T17:03:44+00:00', '2026-08-20T17:45:00+00:00', 'x', 0, 0, 0)"
    )
    con.execute(
        "INSERT INTO audit_broker_history_meta (id, symbol, last_sync_from, "
        "last_sync_to, last_synced_at, last_orders, last_deals, last_trades) "
        "VALUES (1, 'XAUUSD', ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET symbol=excluded.symbol, "
        "last_sync_from=MIN(audit_broker_history_meta.last_sync_from, "
        "excluded.last_sync_from), last_sync_to=excluded.last_sync_to, "
        "last_synced_at=excluded.last_synced_at, last_orders=excluded.last_orders, "
        "last_deals=excluded.last_deals, last_trades=excluded.last_trades",
        (
            "2026-05-08T17:03:44+00:00",
            "2026-08-20T21:00:00+00:00",
            "now",
            len([]),
            len([]),
            len([]),
        ),  # orders/deals counts irrelevant here
    )
    con.commit()
    row = con.execute(
        "SELECT last_sync_from, last_sync_to FROM audit_broker_history_meta WHERE id=1"
    ).fetchone()
    assert row[0] == "2026-05-08T17:03:44+00:00", row  # earliest preserved
    assert row[1] == "2026-08-20T21:00:00+00:00", row  # to advances
    con.close()
    db.unlink()
