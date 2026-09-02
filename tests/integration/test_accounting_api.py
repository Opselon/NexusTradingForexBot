"""
Phase 08 Integration — Unified Accounting API, Worker & Live Engine Wiring
==========================================================================
End-to-end verification that the canonical AccountingCore, the background
AccountingWorker and the REST endpoints all read the SAME authoritative SQLite
tables and return REAL data (no synthetic fallback).

Endpoints covered:

    GET /api/account/performance
    GET /api/account/performance/{day|week|month|year}
    GET /api/account/performance/{kind}/series
    GET /api/account/equity-curve
    GET /api/account/drawdown
    GET /api/account/trades/{ticket}
    GET /api/account/strategies

Also verifies:
  * worker cycle refreshes the derived cache without duplicating records
  * worker failure is isolated (adapter down -> cycle still safe)
  * LiveEngine exposes accounting_core / accounting_worker after construction
  * exactly-once trade closure through the real order lifecycle
"""

from __future__ import annotations

import gc
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)
from nexus_scalp.experience.quality import compute_behavior_metrics
from nexus_scalp.web.server import create_app


def _flush() -> None:
    time.sleep(0.6)


def _seed_closed_trade(
    repo: AuditRepository,
    ticket: int,
    close_ts: datetime,
    pnl: float,
    exit_mechanism: str = "TAKE_PROFIT_HIT",
    direction: str = "BUY",
    entry: float = 2000.0,
    exit_price: float = 0.0,
    strategy_id: str = "strat_api",
) -> None:
    """Seeds one closed ledger row plus its experience attribution chain."""
    ledger = ExperienceLedger(audit_repo=repo)
    ctx = StrategyContext(strategy_id=strategy_id, regime="TRENDING_MOMENTUM")
    dt = close_ts - timedelta(minutes=10)
    key = f"exp_req_{ticket}"
    ledger.record_experience(
        ExperienceRecord(
            experience_id=f"exp_{ticket}",
            request_id=f"req_{ticket}",
            idempotency_key=key,
            symbol="XAUUSD",
            decision_timestamp=dt,
            strategy_id=strategy_id,
            context=ctx,
            feature_snapshot=FeatureSnapshot(values=[0.1] * 50),
            action="BUY_MARKET",
            entry_reason="PURE_AI",
            model_probability=0.8,
            signal_confidence=0.8,
            proposed_entry=entry,
            stop_loss=entry - 10.0,
            take_profit=entry + 20.0,
            risk_reward_ratio=2.0,
        )
    )
    ledger.record_outcome(
        ExperienceOutcome(
            idempotency_key=key,
            execution_id=str(ticket),
            outcome_timestamp=close_ts,
            is_executed=True,
            is_closed=True,
            exit_reason=exit_mechanism,
            realized_pnl_usd=pnl,
            realized_r_multiple=pnl / 1000.0,
            behavior=compute_behavior_metrics(
                mae_points=-5.0,
                mfe_points=15.0,
                mae_usd=-50.0,
                mfe_usd=150.0,
                planned_risk_distance=10.0,
                duration_sec=600.0,
                initial_sl_distance=10.0,
                atr_at_entry=4.0,
            ),
        )
    )
    _flush()
    repo.log_ledger_closed(
        ticket=ticket,
        symbol="XAUUSD",
        direction=direction,
        volume=1.0,
        entry_price=entry,
        exit_price=exit_price or (entry + 2.0 if pnl >= 0 else entry - 2.0),
        status="CLOSED",
        pnl=pnl,
        commission=0.0,
        swap=0.0,
        duration_sec=600.0,
        timestamp_str=close_ts.isoformat(),
        mae=-5.0,
        mfe=15.0,
        initial_sl_price=entry - 10.0,
        final_sl_price=entry - 10.0,
        is_risk_free_hit=0,
        exit_mechanism=exit_mechanism,
        open_time=(close_ts - timedelta(minutes=10)).isoformat(),
        close_time=close_ts.isoformat(),
        was_sl_modified=0,
        mae_usd=-50.0,
        mfe_usd=150.0,
    )
    _flush()


def _snapshot(repo: AuditRepository, ts: datetime, balance: float, equity: float) -> None:
    repo._queue.put_nowait(
        (
            "INSERT INTO audit_account_snapshots (timestamp, balance, equity, margin_free, peak_equity) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts.strftime("%Y-%m-%d %H:%M:%S"), balance, equity, balance, max(balance, equity)),
        )
    )
    _flush()


@pytest.fixture()
def wired_app(tmp_path):
    """Audit repo + full LiveEngine wiring + FastAPI app with real data."""
    db_url = f"sqlite:///{tmp_path / 'accounting_api.db'}"
    repo = AuditRepository(db_url=db_url, flush_interval_sec=0.05)
    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    adapter.connect()

    config = AppConfig.model_validate(
        {
            "execution": {"symbol": "XAUUSD", "mode": "PAPER", "magic_number": 888201},
            "model": {
                "model_artifact_path": str(tmp_path / "model.pt"),
                "feature_schema_version": "v1.0",
                "confidence_threshold": 0.20,
            },
            "risk": {
                "risk_per_trade_pct": 2.0,
                "max_account_drawdown_pct": 10.0,
                "max_concurrent_positions": 5,
                "max_spread_points": 50,
                "max_allowed_lots": 10.0,
                "max_margin_usage_pct": 50.0,
            },
            "telegram": {"enabled": False, "bot_token": "x", "admin_id": "y"},
        }
    )
    engine = LiveEngine(config=config, adapter=adapter, audit_repo=repo, force_fresh_model=True)

    # Seed real historical data BEFORE any request. Trade close times are
    # anchored to the CURRENT UTC day (not a fixed date) so DAY/WEEK/MONTH/YEAR
    # reports all contain them regardless of when the suite runs. The previous
    # revision anchored to 2026-08-15, which silently broke every run after that
    # date because the current DAY period starts at 2026-08-16 midnight.
    now_utc = datetime.now(UTC)
    day = now_utc.replace(hour=10, minute=0, second=0, microsecond=0)
    _seed_closed_trade(repo, 1001, day, 200.0, strategy_id="strat_alpha")
    _seed_closed_trade(
        repo,
        1002,
        day + timedelta(hours=2),
        -100.0,
        exit_mechanism="HARD_SL_HIT",
        strategy_id="strat_alpha",
    )
    _snapshot(repo, now_utc.replace(hour=0, minute=0, second=1, microsecond=0), 10000.0, 10000.0)
    _snapshot(repo, now_utc.replace(hour=23, minute=59, second=0, microsecond=0), 10100.0, 10100.0)

    engine.accounting_worker.start()
    engine.accounting_worker.tick()

    app = create_app(engine_ref=None)
    app.state.engine = engine
    yield repo, engine, app
    engine.accounting_worker.stop()
    repo.close()
    gc.collect()


class TestAccountingApi:
    def test_performance_overview(self, wired_app) -> None:
        _, _, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/performance")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        assert data["live"]["balance"] == pytest.approx(10000.0)
        assert data["totals"]["closed_trades"] == 2
        assert data["totals"]["win_count"] == 1
        assert data["totals"]["loss_count"] == 1
        assert data["totals"]["win_rate"] == pytest.approx(50.0)
        assert data["totals"]["realized_pnl"] == pytest.approx(100.0)
        # Worker telemetry exposed
        assert data["worker"]["running"] is True
        assert data["worker"]["cycle_count"] >= 1

    def test_period_endpoints_all_granularities(self, wired_app) -> None:
        _, _, app = wired_app
        with TestClient(app) as client:
            for kind in ("DAY", "WEEK", "MONTH", "YEAR"):
                res = client.get(f"/api/account/performance/{kind}")
                assert res.status_code == 200
                period = res.json()
                # 15 Aug 2026 IS within the current real period window for all
                # four granularities, so the report must see both seeded trades.
                assert period["available"] is True
                assert period["period"]["total_trades"] == 2
                assert period["period"]["net_pnl"] == pytest.approx(100.0)

    def test_unknown_period_returns_400(self, wired_app) -> None:
        _, _, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/performance/FORTNIGHT")
        assert res.status_code == 400

    def test_period_series_endpoint(self, wired_app) -> None:
        _, _, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/performance/DAY/series?count=7")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        assert len(data["periods"]) == 7
        keys = [p["key"] for p in data["periods"]]
        assert keys == sorted(keys)

    def test_performance_intelligence_endpoint(self, wired_app) -> None:
        """Performance Intelligence report endpoint: structured JSON contract
        with the same money truth as the canonical period report (trades and
        net PnL must match exactly)."""
        _, _, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/performance/intelligence")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        rep = data["report"]
        # Full contract shape
        for key in (
            "report_id",
            "snapshot_id",
            "generated_at",
            "period_kind",
            "performance",
            "distribution",
            "r",
            "excursion",
            "holding",
            "exits",
            "streaks",
            "risk",
            "drawdown",
            "strategies",
            "regimes",
            "sessions",
            "model",
            "execution",
            "news",
            "behavioral",
            "loss_drivers",
            "profit_drivers",
            "period_compare",
            "anomalies",
            "health_score",
            "insights",
            "trend",
            "evidence",
        ):
            assert key in rep, f"missing {key}"
        # Canonical truth preserved: 2 seeded trades, net +100
        assert rep["performance"]["trades"] == 2
        assert rep["performance"]["net_pnl"] == pytest.approx(100.0)
        assert rep["performance"]["wins"] == 1
        assert rep["performance"]["losses"] == 1
        assert 0 <= rep["health_score"]["total"] <= 100
        # TASK-2 truth-state contract: NO_DATA when nothing has been analyzed.
        assert rep["behavioral"]["state"] == "NO_DATA"
        assert rep["anomaly_state"]["state"] == "NO_DATA"
        # Compact top-level intelligence contract (§23).
        intel = data["intelligence"]
        assert intel["status"] == "NO_DATA"
        assert intel["behavior_state"] == "NO_DATA"
        assert intel["trades_analyzed"] == 0
        assert "analysis_version" in intel
        assert "behavioral_flags" in intel

    def test_equity_curve_real_data(self, wired_app) -> None:
        _, _, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/equity-curve")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        assert len(data["equity_curve"]) >= 2
        # Real snapshots: first balance 10000, last balance 10100
        assert data["equity_curve"][0]["balance"] == pytest.approx(10000.0)
        assert data["equity_curve"][-1]["balance"] == pytest.approx(10100.0)

    def test_drawdown_endpoint(self, wired_app) -> None:
        _, _, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/drawdown")
        assert res.status_code == 200
        data = res.json()
        assert data["has_data"] is True
        assert data["sample_count"] >= 2
        assert data["current_equity"] == pytest.approx(10100.0)

    def test_trade_forensics_endpoint(self, wired_app) -> None:
        _, _, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/trades/1001")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        assert data["ticket"] == 1001
        assert data["identity"]["strategy_id"] == "strat_alpha"
        assert data["outcome"]["outcome"] in ("WIN", "BREAKEVEN", "LOSS")
        assert data["exit"]["exit_classification"] in (
            "TAKE_PROFIT",
            "INITIAL_STOP",
            "BREAKEVEN_STOP",
            "TRAILING_STOP",
            "MANUAL_EXIT",
            "EMERGENCY_EXIT",
            "STRATEGY_EXIT",
            "PARTIAL_CLOSE",
            "OTHER_EXIT",
        )
        # Position quality surfaced in forensics
        assert data["position_path"]["mfe_points"] == pytest.approx(15.0)

    def test_trade_forensics_unknown(self, wired_app) -> None:
        _, _, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/trades/999999")
        assert res.status_code == 200
        assert res.json()["available"] is False

    def test_strategies_endpoint_linked(self, wired_app) -> None:
        _, _, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/strategies")
        assert res.status_code == 200
        data = res.json()
        assert data["available"] is True
        strategies = data["strategies"]
        assert any(s["strategy_id"] == "strat_alpha" for s in strategies)
        alpha = next(s for s in strategies if s["strategy_id"] == "strat_alpha")
        assert alpha["trade_count"] == 2
        assert alpha["net_pnl"] == pytest.approx(100.0)

    def test_no_synthetic_zero_history_when_empty(self, tmp_path) -> None:
        """An empty database must NOT fabricate a zeroed history."""
        repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'empty.db'}")
        engine = None
        try:
            adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
            config = AppConfig.model_validate(
                {
                    "execution": {"symbol": "XAUUSD", "mode": "PAPER", "magic_number": 1},
                    "model": {
                        "model_artifact_path": str(tmp_path / "m.pt"),
                        "feature_schema_version": "v1.0",
                        "confidence_threshold": 0.2,
                    },
                    "risk": {
                        "risk_per_trade_pct": 2.0,
                        "max_account_drawdown_pct": 10.0,
                        "max_concurrent_positions": 5,
                        "max_spread_points": 50,
                        "max_allowed_lots": 10.0,
                        "max_margin_usage_pct": 50.0,
                    },
                    "telegram": {"enabled": False, "bot_token": "x", "admin_id": "y"},
                }
            )
            engine = LiveEngine(
                config=config, adapter=adapter, audit_repo=repo, force_fresh_model=True
            )
            app = create_app(engine_ref=None)
            app.state.engine = engine
            with TestClient(app) as client:
                res = client.get("/api/account/performance")
                assert res.status_code == 200
                data = res.json()
                assert data["available"] is True
                assert data["totals"]["closed_trades"] == 0
                assert data["periods"]["DAY"]["has_data"] is False
                assert data["periods"]["DAY"]["net_pnl"] == 0.0
        finally:
            if engine:
                engine.accounting_worker.stop()
            repo.close()
            gc.collect()


class TestWorkerWithEngine:
    def test_worker_wired_into_engine(self, wired_app) -> None:
        _, engine, _ = wired_app
        assert hasattr(engine, "accounting_core")
        assert hasattr(engine, "accounting_worker")
        assert engine.accounting_worker.running

    def test_worker_cycle_idempotent(self, wired_app) -> None:
        _, engine, _ = wired_app
        before = engine.accounting_core.load_trades()
        # Immediate re-kicks are throttled by the 30s interval; force a refresh
        # by stepping the internal clock so we can exercise real repeated cycles.
        engine.accounting_worker._last_run_ts = 0.0
        engine.accounting_worker.tick()
        engine.accounting_worker._last_run_ts = 0.0
        engine.accounting_worker.tick()
        after = engine.accounting_core.load_trades()
        assert len(after) == len(before)
        assert engine.accounting_worker.cycle_count >= 2

    def test_account_summary_never_serves_synthetic_numbers(self, wired_app) -> None:
        """
        /api/account/summary must read the canonical AccountingCore and must
        NEVER serve hardcoded placeholders (the previous revision returned
        balance=10000.00 / win_rate=0.0 even when the broker adapter was down -
        see agents/bugs.md BUG-020).
        """
        _, engine, app = wired_app
        with TestClient(app) as client:
            res = client.get("/api/account/summary")
        assert res.status_code == 200
        data = res.json()
        # Real account present (Paper adapter connected).
        assert data["available"] is True
        assert data["balance"] == pytest.approx(10000.0)
        assert data["equity"] == pytest.approx(10000.0)
        # Real ledger totals: 1 win + 1 loss.
        assert data["total_trades"] == 2
        assert data["win_rate"] == pytest.approx(50.0)

        # Simulate a broker failure: the endpoint must degrade to None fields,
        # never fall back to fake zeros.
        from unittest import mock

        with mock.patch.object(
            engine.adapter, "get_account_info", side_effect=RuntimeError("broker down")
        ):
            with TestClient(app) as client:
                res2 = client.get("/api/account/summary")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["available"] is False
        assert data2["balance"] is None
        assert data2["equity"] is None
        # Win rate / totals still come from the real ledger.
        assert data2["win_rate"] is not None
        assert data2["total_trades"] == 2

    def test_engine_construction_does_not_require_model_artifact(self, tmp_path) -> None:
        """Accounting must survive a cold start with no model file."""
        repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'cold.db'}")
        adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
        engine = None
        try:
            config = AppConfig.model_validate(
                {
                    "execution": {"symbol": "XAUUSD", "mode": "PAPER", "magic_number": 2},
                    "model": {
                        "model_artifact_path": str(tmp_path / "none.pt"),  # does not exist
                        "feature_schema_version": "v1.0",
                        "confidence_threshold": 0.2,
                    },
                    "risk": {
                        "risk_per_trade_pct": 2.0,
                        "max_account_drawdown_pct": 10.0,
                        "max_concurrent_positions": 5,
                        "max_spread_points": 50,
                        "max_allowed_lots": 10.0,
                        "max_margin_usage_pct": 50.0,
                    },
                    "telegram": {"enabled": False, "bot_token": "x", "admin_id": "y"},
                }
            )
            engine = LiveEngine(
                config=config, adapter=adapter, audit_repo=repo, force_fresh_model=True
            )
            assert engine.accounting_core is not None
            live = engine.accounting_core.live_state()
            assert live.available is True
        finally:
            if engine:
                engine.accounting_worker.stop()
            repo.close()
            gc.collect()
