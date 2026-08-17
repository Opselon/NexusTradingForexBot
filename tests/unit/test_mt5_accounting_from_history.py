"""AccountingCore MUST read the normalized broker history (RED phase).

The core bug being fixed: strategy rows / trade counts existed but financials
were zero because ledger rows carried zero PnL and the deal evidence never
reached accounting. These tests prove the accounting totals come from the
verified `audit_broker_trades` source and that wins/losses/breakeven/best/
worst/expectancy/profit-factor are real broker-derived values.
"""

from __future__ import annotations

import gc
from datetime import UTC, datetime

import pytest

from nexus_scalp.accounting import AccountingCore, PeriodKind
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from tests.helpers.mt5_fixtures import EXPECTED, fixture_objects


@pytest.fixture()
def audit(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'acc.db'}", flush_interval_sec=0.05)
    yield repo
    repo.close()
    gc.collect()


@pytest.fixture()
def core(audit) -> AccountingCore:
    return AccountingCore(audit_repo=audit, adapter=None)


def _seed_history(audit: AuditRepository) -> None:
    audit.sync_broker_history(
        orders=fixture_objects("history_orders"),
        deals=fixture_objects("history_deals"),
        symbol="XAUUSD",
        sync_from=datetime(2026, 8, 17, tzinfo=UTC),
        sync_to=datetime(2026, 8, 17, 23, 59, tzinfo=UTC),
    )


class TestAccountingFromBrokerHistory:
    def test_total_trades_from_broker_history(self, audit, core) -> None:
        _seed_history(audit)
        trades = core.load_trades()
        assert len(trades) == EXPECTED["closed_trades"]

    def test_net_pnl_from_broker_history(self, audit, core) -> None:
        _seed_history(audit)
        closed = [t for t in core.load_trades() if t.closed_at is not None]
        total = sum(t.net_pnl for t in closed)
        assert round(total, 2) == EXPECTED["trades_net_total"]

    def test_win_rate_from_broker_history(self, audit, core) -> None:
        _seed_history(audit)
        closed = [t for t in core.load_trades() if t.closed_at is not None]
        wins = sum(1 for t in closed if t.is_win)
        losses = sum(1 for t in closed if t.outcome.value == "LOSS")
        decided = wins + losses
        assert wins == EXPECTED["wins"]
        assert losses == EXPECTED["losses"]
        assert round(wins / decided * 100.0, 2) == round(37 / EXPECTED["closed_trades"] * 100.0, 2)

    def test_period_report_financials_real(self, audit, core) -> None:
        _seed_history(audit)
        report = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC))
        assert report.has_data is True
        assert report.total_trades == EXPECTED["closed_trades"]
        assert round(report.net_pnl, 2) == EXPECTED["trades_net_total"]
        assert report.win_count == EXPECTED["wins"]
        assert report.loss_count == EXPECTED["losses"]
        assert report.breakeven_count == EXPECTED["breakeven"]
        assert (
            report.best_trade is not None and round(report.best_trade, 2) == EXPECTED["best_trade"]
        )
        assert (
            report.worst_trade is not None
            and round(report.worst_trade, 2) == EXPECTED["worst_trade"]
        )
        assert report.expectancy is not None
        assert round(report.expectancy, 4) == round(
            EXPECTED["trades_net_total"] / EXPECTED["closed_trades"], 4
        )
        # Profit factor: gross profits / |gross losses| over the 37/5 split.
        assert report.profit_factor is not None and report.profit_factor > 1.0

    def test_break_even_is_counted_not_skipped(self, audit, core) -> None:
        _seed_history(audit)
        report = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC))
        assert report.breakeven_count == EXPECTED["breakeven"]
        assert report.total_trades == report.win_count + report.loss_count + report.breakeven_count

    def test_cumulative_pnl_curve_real_points(self, audit, core) -> None:
        _seed_history(audit)
        curve = core.cumulative_pnl_curve(limit=500)
        assert len(curve) == EXPECTED["closed_trades"]
        assert round(curve[-1]["cumulative_pnl"], 2) == EXPECTED["trades_net_total"]
        assert all("timestamp" in p and "net_pnl" in p for p in curve)

    def test_equity_curve_marks_source(self, audit, core) -> None:
        _seed_history(audit)
        # No live snapshots exist for the pure-fixture DB: the curve must be
        # honest about having zero snapshot points until snapshots are seeded.
        curve = core.equity_curve()
        assert curve == []  # no synthetic points
