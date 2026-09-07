"""Integration tests: paper execution-parity persistence + weekly snapshot.

Covers the P0-1 parity chain end-to-end on a temp audit DB:
- audit_paper_executions table is created idempotently by AuditRepository
- export_paper_ledger persists adapter-ledger rows (fills AND rejections)
- re-export is a no-op (identity index dedupe — INV-006 idempotency class)
- paper_execution_stats / broker_execution_stats return honest aggregates
- build_parity_snapshot reports INSUFFICIENT_DATA when either side is empty
  and MEASURED (observational) when both sides have samples
- no calibration factors are invented anywhere in the snapshot
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.risk.paper_parity import (
    PAPER_SOURCE_TAG,
    build_parity_snapshot,
    export_paper_ledger,
)


class _FakePaperAdapter:
    """Minimal stand-in exposing the PaperMT5Adapter ledger surface."""

    def __init__(self, ledger: list[dict]) -> None:
        self._ledger = ledger
        self.current_account_source = "PAPER"

    def get_execution_ledger(self) -> list[dict]:
        return [dict(e) for e in self._ledger]


def _ledger_rows() -> list[dict]:
    return [
        {
            "ts": "2026-09-07T10:00:00.123456+00:00",
            "symbol": "XAUUSD",
            "order_type": "BUY",
            "volume": 0.10,
            "requested_price": 4400.00,
            "bid_at_request": 4399.95,
            "ask_at_request": 4400.10,
            "spread": 0.15,
            "fill_price": 4400.12,
            "slippage": 0.02,
            "latency_ticks": 1,
            "rejection_reason": None,
            "is_fill": True,
            "ticket": 100001,
        },
        {
            "ts": "2026-09-07T10:05:00.654321+00:00",
            "symbol": "XAUUSD",
            "order_type": "SELL_LIMIT",
            "volume": 0.20,
            "requested_price": 4401.50,
            "bid_at_request": 4400.90,
            "ask_at_request": 4401.05,
            "spread": 0.15,
            "fill_price": None,
            "slippage": None,
            "latency_ticks": 0,
            "rejection_reason": "insufficient_margin",
            "is_fill": False,
            "ticket": 0,
        },
    ]


@pytest.fixture()
def audit_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(tmp_path / "parity_test.db"))
    repo = AuditRepository()
    yield repo


class TestPaperParity:
    def test_table_created_and_rows_persist(self, audit_repo) -> None:
        adapter = _FakePaperAdapter(_ledger_rows())
        result = export_paper_ledger(adapter=adapter, audit=audit_repo)
        assert result["available"] is True
        assert result["exported"] == 2
        assert result["failed"] == 0
        with sqlite3.connect(audit_repo._db_path) as conn:
            rows = conn.execute(
                "SELECT ts, status, rejection_reason FROM audit_paper_executions ORDER BY ts"
            ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == "FILLED"
        assert rows[1][1] == "REJECTED"
        assert rows[1][2] == "insufficient_margin"

    def test_reexport_is_idempotent(self, audit_repo) -> None:
        adapter = _FakePaperAdapter(_ledger_rows())
        export_paper_ledger(adapter=adapter, audit=audit_repo)
        again = export_paper_ledger(adapter=adapter, audit=audit_repo)
        assert again["exported"] == 2  # inserts attempted
        with sqlite3.connect(audit_repo._db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM audit_paper_executions").fetchone()[0]
        assert n == 2  # but no duplicates (identity index)

    def test_stats_are_honest(self, audit_repo) -> None:
        adapter = _FakePaperAdapter(_ledger_rows())
        export_paper_ledger(adapter=adapter, audit=audit_repo)
        stats = audit_repo.paper_execution_stats(days=7)
        assert stats["attempts"] == 2
        assert stats["fills"] == 1
        assert stats["fill_rate"] == pytest.approx(0.5)
        assert stats["mean_spread"] == pytest.approx(0.15)
        # broker side: empty DB -> honest zero, no fabrication
        demo = audit_repo.broker_execution_stats(days=7)
        assert demo["trades"] == 0
        assert demo["win_rate"] is None

    def test_snapshot_insufficient_data_without_evidence(self, audit_repo) -> None:
        snap = build_parity_snapshot(audit=audit_repo, lookback_days=7)
        assert snap["status"] == "INSUFFICIENT_DATA"
        assert "comparison" not in snap
        assert any("insufficient samples" in n for n in snap["notes"])

    def test_snapshot_measured_with_both_sides(self, audit_repo) -> None:
        adapter = _FakePaperAdapter(_ledger_rows())
        export_paper_ledger(adapter=adapter, audit=audit_repo)
        # seed one broker trade (canonical audit_broker_trades copy)
        with sqlite3.connect(audit_repo._db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO audit_broker_trades
                    (trade_id, position_id, symbol, direction, entry_time, exit_time,
                     entry_price, exit_price, volume, net_pnl, duration_sec, source)
                VALUES ('T1', 1, 'XAUUSD', 'BUY', ?, ?, 4400.0, 4402.0, 0.1, 20.0, 120.0,
                        'BROKER_DEALS')
                """,
                ("2026-09-07T10:00:00+00:00", "2026-09-07T10:02:00+00:00"),
            )
        snap = build_parity_snapshot(audit=audit_repo, lookback_days=7)
        assert snap["status"] == "MEASURED"
        assert snap["comparison"]["paper_fill_rate"] == pytest.approx(0.5)
        assert snap["comparison"]["broker_trades"] == 1
        # no calibration/bias factor may be invented by the snapshot
        assert "calibration" not in snap and "bias_factor" not in snap

    def test_ledger_unavailable_is_honest(self, audit_repo) -> None:
        class _Broken:
            current_account_source = "PAPER"

            def get_execution_ledger(self):
                raise RuntimeError("boom")

        result = export_paper_ledger(adapter=_Broken(), audit=audit_repo)
        assert result["available"] is False
        assert "ledger_unavailable" in result.get("error", "")
