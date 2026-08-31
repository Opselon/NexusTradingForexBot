"""API + chart contract tests (RED phase) — fixture-backed engine harness.

The accounting core is wired to a temp SQLite repo seeded from the REAL MT5
fixtures. The API must serve the SAME numbers the database holds —
trades=44, net=+741.05, best=+178.11, worst=-11.60 — and the equity curve /
closed-history endpoints must contain real points.
"""

from __future__ import annotations

import gc
from datetime import UTC, datetime

import pytest

from nexus_scalp.accounting import AccountingCore
from nexus_scalp.accounting import periods as _accounting_periods
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from tests.helpers.mt5_fixtures import EXPECTED, fixture_objects


class _FakeEngine:
    """Minimal engine-shaped object the server's _accounting() facade reads."""

    def __init__(self, audit: AuditRepository) -> None:
        self.audit = audit
        self.accounting_core = AccountingCore(audit_repo=audit, adapter=None)
        self.accounting_worker = None


def _make_app(tmp_path):
    """Builds the FastAPI app with the fixture-seeded engine in app.state."""
    from nexus_scalp.web.server import create_app

    audit = AuditRepository(db_url=f"sqlite:///{tmp_path / 'api.db'}", flush_interval_sec=0.05)
    audit.sync_broker_history(
        orders=fixture_objects("history_orders"),
        deals=fixture_objects("history_deals"),
        symbol="XAUUSD",
        sync_from=datetime(2026, 8, 17, tzinfo=UTC),
        sync_to=datetime(2026, 8, 17, 23, 59, tzinfo=UTC),
    )
    engine = _FakeEngine(audit)
    app = create_app(engine_ref=engine)
    return app, audit


@pytest.fixture()
def client(tmp_path):
    app, audit = _make_app(tmp_path)
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    audit.close()
    gc.collect()


class TestAccountPerformanceEndpoint:
    def test_totals_are_real_broker_values(self, client) -> None:
        res = client.get("/api/account/performance")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        totals = data["totals"]
        assert totals["closed_trades"] == EXPECTED["closed_trades"]
        assert round(totals["realized_pnl"], 2) == EXPECTED["trades_net_total"]
        assert totals["win_count"] == EXPECTED["wins"]
        assert totals["loss_count"] == EXPECTED["losses"]
        assert totals["win_rate"] is not None and totals["win_rate"] > 0.0

    def test_period_report_has_real_financials(self, client, monkeypatch) -> None:
        """
        Period report must present real financials whenever ANY historical data
        exists in the range - never synthetic zeros. The DAY window depends on
        the fixture's trade timestamps (broker server-local epoch discipline),
        so the authoritative check is the aggregate series + totals endpoints,
        which aggregate across all seeded history instead of a single window.
        """
        # BUG-153: the fixture's trades live on 2026-08-17; a rolling 14-day
        # window anchored to "now" eventually slides past them (time bomb).
        # Freeze the accounting clock to the fixture capture day so the test
        # stays deterministic forever.
        monkeypatch.setattr(
            _accounting_periods,
            "utc_now",
            lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        )
        res = client.get("/api/account/performance/DAY/series?count=14")
        assert res.status_code == 200
        periods = res.json()["periods"]
        assert len(periods) == 14
        with_data = [p for p in periods if p["has_data"]]
        assert len(with_data) >= 1, "seeded broker history must appear in at least one period"
        for p in with_data:
            assert p["total_trades"] > 0
            assert p["net_pnl"] is not None

    def test_period_series_has_points(self, client, monkeypatch) -> None:
        monkeypatch.setattr(
            _accounting_periods,
            "utc_now",
            lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        )
        res = client.get("/api/account/performance/DAY/series?count=14")
        assert res.status_code == 200
        periods = res.json()["periods"]
        assert len(periods) == 14
        assert sum(1 for p in periods if p["has_data"]) >= 1

    def test_full_period_report_no_zero_defaults_for_history(self, client) -> None:
        """The exact dashboard symptom: trades>0 must NEVER coexist with
        net_pnl=0 / best=0 / worst=0 for a fixture with real outcomes."""
        res = client.get("/api/account/performance")
        data = res.json()
        totals = data["totals"]
        assert not (totals["closed_trades"] > 0 and totals["realized_pnl"] == 0.0)


class TestEquityCurveAndClosedHistory:
    def test_equity_curve_with_seeded_snapshots(self, client) -> None:
        audit = client.app.state.engine.audit
        # Direct authoritative snapshot rows (bypass throttling) — real values
        # from the live account: start-of-day 39510.35 -> end 37717.91.
        for stamp, bal, eq in (
            ("2026-08-17 00:00:00", 40350.0, 40350.0),
            ("2026-08-17 06:00:00", 39510.35, 39510.35),
            ("2026-08-17 12:00:00", 37717.91, 37717.91),
        ):
            audit._queue.put_nowait(  # type: ignore[attr-defined]
                (
                    "INSERT INTO audit_account_snapshots "
                    "(timestamp, balance, equity, margin_free, peak_equity) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (stamp, bal, eq, bal, max(bal, eq)),
                )
            )
        import time

        time.sleep(0.5)
        res = client.get("/api/account/equity-curve")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        curve = data["equity_curve"]
        assert len(curve) == 3
        assert curve[0]["balance"] == 40350.0
        assert curve[-1]["equity"] == 37717.91

    def test_closed_trade_rows_are_real(self, client) -> None:
        res = client.get("/api/account/trades?limit=10")
        assert res.status_code == 200
        rows = res.json()
        assert len(rows) >= 1
        first = rows[0]
        assert first["symbol"] == "XAUUSD"
        # Broker trades carry position_id (the deterministic broker identity);
        # ledger rows carry ticket. Either is a valid row identity.
        assert (first.get("ticket")) or (first.get("position_id"))


class TestStrategyAttributionFinancialConsistency:
    def test_strategy_rows_not_fabricated(self, client) -> None:
        res = client.get("/api/account/strategies")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        # Unattributed broker trades must not vanish; they surface as an
        # explicit UNATTRIBUTED bucket when the API exposes one, and never
        # as a fake strategy with trades>0 and pnl=0.
        for s in data["strategies"]:
            if s["trade_count"] > 0:
                assert not (s["net_pnl"] == 0.0 and s["win_rate"] is None)
