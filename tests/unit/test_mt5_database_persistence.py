"""Database persistence + exact idempotency tests (RED phase).

The durable normalized copy lives in audit_broker_orders / audit_broker_deals /
audit_broker_trades. Re-ingesting the SAME real fixture 10 times must not
change any count or accounting total.
"""

from __future__ import annotations

import gc
import sqlite3
from datetime import UTC, datetime

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from tests.helpers.mt5_fixtures import EXPECTED, fixture_objects


@pytest.fixture()
def audit(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'hist.db'}", flush_interval_sec=0.05)
    yield repo
    repo.close()
    gc.collect()


def _sync_once(repo: AuditRepository, *, deals=None, orders=None) -> dict:
    """Runs one broker-history upsert pass; returns sync counts."""
    ctx = repo.sync_broker_history(
        orders=orders if orders is not None else fixture_objects("history_orders"),
        deals=deals if deals is not None else fixture_objects("history_deals"),
        symbol="XAUUSD",
        sync_from=datetime(2026, 8, 17, tzinfo=UTC),
        sync_to=datetime(2026, 8, 17, 23, 59, tzinfo=UTC),
    )
    return ctx


class TestBrokerHistoryTables:
    def test_tables_exist_after_repo_create(self, audit: AuditRepository) -> None:
        with sqlite3.connect(audit._db_path, timeout=5.0) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "audit_broker_orders" in tables
        assert "audit_broker_deals" in tables
        assert "audit_broker_trades" in tables

    def test_first_sync_persists_everything(self, audit: AuditRepository) -> None:
        ctx = _sync_once(audit)
        with sqlite3.connect(audit._db_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            deals = conn.execute("SELECT COUNT(*) FROM audit_broker_deals").fetchone()[0]
            orders = conn.execute("SELECT COUNT(*) FROM audit_broker_orders").fetchone()[0]
            trades = conn.execute("SELECT COUNT(*) FROM audit_broker_trades").fetchone()[0]
        assert ctx["deals_total"] == EXPECTED["deals_count"]
        assert ctx["orders_total"] == EXPECTED["orders_count"]
        assert ctx["trades_total"] == EXPECTED["positions_count"]  # 44 reconstructed
        assert trades == EXPECTED["closed_trades"]  # 42 persisted (2 still open)
        assert deals == EXPECTED["deals_count"]
        assert orders == EXPECTED["orders_count"]
        assert trades == EXPECTED["closed_trades"]
        assert ctx["deals_inserted"] == EXPECTED["deals_count"]
        assert ctx["deals_duplicates"] == 0

    def test_reingestion_is_idempotent_ten_times(self, audit: AuditRepository) -> None:
        _sync_once(audit)
        for _ in range(9):
            _sync_once(audit)
        with sqlite3.connect(audit._db_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            deals = conn.execute("SELECT COUNT(*) FROM audit_broker_deals").fetchone()[0]
            orders = conn.execute("SELECT COUNT(*) FROM audit_broker_orders").fetchone()[0]
            trades = conn.execute("SELECT COUNT(*) FROM audit_broker_trades").fetchone()[0]
            net = conn.execute(
                "SELECT COALESCE(SUM(net_pnl), 0) FROM audit_broker_trades"
            ).fetchone()[0]
        # Counts unchanged after 10 identical syncs.
        assert deals == EXPECTED["deals_count"]
        assert orders == EXPECTED["orders_count"]
        assert trades == EXPECTED["closed_trades"]
        assert round(net, 2) == EXPECTED["trades_net_total"]
        # Second sync reported zero inserts (pure duplicates).
        ctx2 = _sync_once(audit)
        assert ctx2["deals_inserted"] == 0
        assert ctx2["deals_duplicates"] == EXPECTED["deals_count"]
        # trade reconstructions are re-attempted but all 42 closed lifecycles
        # already exist -> 0 inserted, 42 duplicates.
        assert ctx2["trades_total"] == EXPECTED["positions_count"]
        assert ctx2["trades_inserted"] == 0
        assert ctx2["trades_duplicates"] == EXPECTED["closed_trades"]

    def test_partial_close_never_duplicates_trade(self, audit: AuditRepository) -> None:
        _sync_once(audit)
        with sqlite3.connect(audit._db_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT COUNT(*) FROM audit_broker_trades WHERE position_id = ?",
                (EXPECTED["partial_close_position"],),
            ).fetchone()[0]
            deals = conn.execute(
                "SELECT COUNT(*) FROM audit_broker_deals WHERE position_id = ?",
                (EXPECTED["partial_close_position"],),
            ).fetchone()[0]
        assert rows == 1  # one logical trade
        assert deals == 4  # all broker deals preserved

    def test_persisted_trade_financials(self, audit: AuditRepository) -> None:
        _sync_once(audit)
        with sqlite3.connect(audit._db_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT gross_pnl, commission, swap, fee, net_pnl, volume, direction, "
                "entry_time, exit_time, entry_price, exit_price, symbol "
                "FROM audit_broker_trades WHERE position_id = ?",
                (EXPECTED["partial_close_position"],),
            ).fetchone()
        assert row is not None
        assert round(row["gross_pnl"], 2) == round(EXPECTED["partial_close_gross"], 2)
        assert row["symbol"] == "XAUUSD"
        assert row["commission"] == 0.0
        assert row["volume"] == 0.53
        assert row["entry_time"] and row["exit_time"]
        assert row["entry_price"] > 0.0 and row["exit_price"] > 0.0

    def test_identity_uses_broker_tickets_not_uuids(self, audit: AuditRepository) -> None:
        _sync_once(audit)
        with sqlite3.connect(audit._db_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            sample = conn.execute(
                "SELECT trade_id, position_id FROM audit_broker_trades LIMIT 5"
            ).fetchall()
        # trade_id equals the broker position_id string (deterministic identity).
        for trade_id, position_id in sample:
            assert str(trade_id) == str(position_id)
