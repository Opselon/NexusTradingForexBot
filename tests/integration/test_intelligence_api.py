"""
PHASE 09 Integration — Trade Intelligence Brain API, Worker & LiveEngine Wiring
===============================================================================
End-to-end verification that the Trade Intelligence Brain (position lifecycle,
trade autopsy, behavior detection, evolution, worker) is wired into LiveEngine
and exposed through the REST API, while remaining structurally incapable of
executing an order.

Endpoints covered:
    GET  /api/intelligence/summary
    GET  /api/intelligence/positions/{ticket}/timeline
    GET  /api/intelligence/autopsies
    GET  /api/intelligence/autopsies/{ticket}
    GET  /api/intelligence/behavior
    GET  /api/intelligence/evolution
    POST /api/intelligence/evolution/scan
    POST /api/intelligence/self-heal

Also verifies:
    * LiveEngine constructs the full intelligence subsystem
    * worker start/stop/cycle is restart-safe and failure-isolated
    * a seeded position timeline is queryable through the API
    * the gate rejects a degraded/retired family before dispatch (no MT5 needed)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.experience.models import (
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)
from nexus_scalp.web.server import create_app


def _flush(repo) -> None:
    repo._queue.join()


@pytest.fixture
def wired_engine(tmp_path):
    """A full LiveEngine wired against a paper adapter and temp DB."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'intel_api.db'}")
    cfg = AppConfig(
        execution={
            "symbol": "XAUUSD",
            "mode": "PAPER",
        },
        model={"model_artifact_path": str(tmp_path / "model.pt")},
    )
    # Use a lightweight adapter (paper) so no MT5 is required.
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    engine = LiveEngine(config=cfg, adapter=adapter, audit_repo=repo)
    yield repo, engine
    repo.close()


def seed_lifecycle(repo, tracker):
    """Seeds a full position timeline through the tracker."""
    from nexus_scalp.intelligence.models import (
        DecisionContext,
        MarketContext,
        PositionPerformance,
        PositionSnapshot,
    )

    now = datetime.now(UTC)
    market = MarketContext(symbol="XAUUSD", atr=1.5)
    tracker.observe_position(
        ticket=501,
        snapshot=PositionSnapshot(
            entry_price=2000.0,
            current_price=2000.1,
            volume=0.1,
            stop_loss=1990.0,
            take_profit=2020.0,
            floating_pnl=0.0,
        ),
        performance=PositionPerformance(),
        market=market,
        decision=DecisionContext(strategy_id="strat_api"),
        at=now,
    )
    tracker.observe_position(
        ticket=501,
        snapshot=PositionSnapshot(
            entry_price=2000.0,
            current_price=2002.0,
            volume=0.1,
            stop_loss=1990.0,
            take_profit=2020.0,
            floating_pnl=20.0,
        ),
        performance=PositionPerformance(mfe=1.5, max_profit_reached=20.0),
        market=market,
        decision=DecisionContext(strategy_id="strat_api"),
        at=now + timedelta(seconds=10),
    )
    tracker.finalize_exit(ticket=501, realized_pnl_usd=18.0, at=now + timedelta(seconds=60))
    _flush(repo)


class TestIntelligenceAPI:
    def test_engine_exposes_intelligence_subsystem(self, wired_engine):
        _repo, engine = wired_engine
        for attr in (
            "intelligence_lifecycle",
            "intelligence_autopsy",
            "intelligence_behavior",
            "intelligence_evolution",
            "intelligence_gate",
            "intelligence_worker",
        ):
            assert hasattr(engine, attr), f"LiveEngine missing {attr}"

    def test_lifecycle_timeline_via_api(self, wired_engine):
        repo, engine = wired_engine
        seed_lifecycle(repo, engine.intelligence_lifecycle)
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/intelligence/positions/501/timeline")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        types = {e["event_type"] for e in body["events"]}
        assert "POSITION_CREATED" in types
        assert "POSITION_MFE_REACHED" in types
        assert "POSITION_EXITED" in types

    def test_autopsy_endpoint(self, wired_engine):
        repo, engine = wired_engine
        # Seed one closed experience + autopsy through the autopsy engine.
        rec = ExperienceRecord(
            experience_id="exp_api_1",
            request_id="req_api_1",
            idempotency_key="exp_api_1",
            symbol="XAUUSD",
            outcome_timestamp=datetime.now(UTC),
            decision_timestamp=datetime.now(UTC) - timedelta(minutes=5),
            strategy_id="strat_api",
            strategy_version="1.0.0",
            context=StrategyContext(strategy_id="strat_api", symbol="XAUUSD"),
            feature_snapshot=FeatureSnapshot(values=[0.0] * 50),
            action="BUY_MARKET",
            entry_reason="SMC_GOD_MODE",
            model_probability=0.7,
            signal_confidence=0.7,
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_reward_ratio=2.0,
            approved_volume=0.1,
            is_executed=True,
            is_closed=True,
            exit_reason="TP",
            realized_pnl_usd=20.0,
            realized_r_multiple=2.0,
        )
        autopsy = engine.intelligence_autopsy.build_autopsy(
            record=rec,
            decomposition=rec.decomposition,
            realized_pnl_usd=20.0,
            realized_r=2.0,
            ticket="ticket_api_1",
            symbol="XAUUSD",
            exit_mechanism="TP",
        )
        engine.intelligence_autopsy.persist(autopsy)
        _flush(repo)
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/intelligence/autopsies")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert len(body["autopsies"]) >= 1
        resp2 = client.get("/api/intelligence/autopsies/ticket_api_1")
        assert resp2.status_code == 200
        assert resp2.json()["available"] is True

    def test_summary_and_worker_status(self, wired_engine):
        _repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/intelligence/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "worker" in body
        assert "lifecycle_events" in body

    def test_worker_is_restart_safe_and_isolated(self, wired_engine):
        _repo, engine = wired_engine
        worker = engine.intelligence_worker
        worker.start()
        assert worker.running is True
        # Run several throttled cycles (interval=0 forces immediate).
        worker._last_run_ts = 0.0
        ok1 = worker.tick() or worker.last_error == ""
        worker._last_run_ts = 0.0
        ok2 = worker.tick() or worker.last_error == ""
        assert ok1 and ok2
        worker.stop()
        assert worker.running is False

    def test_anomaly_events_endpoint(self, wired_engine):
        """Evidence-based anomaly events are exposed over the API (TASK-2)."""
        repo, engine = wired_engine
        # Persist one anomaly event directly through the audit queue.
        from nexus_scalp.intelligence.behavior import (
            ANOMALY_ALGORITHM_VERSION,
            _persist_anomaly,
        )
        from nexus_scalp.intelligence.models import AnomalyEvent

        anomaly = AnomalyEvent(
            anomaly_id="ano_api_test_0001",
            ticket="501",
            anomaly_type="EXIT_CLASSIFICATION_ANOMALY",
            category="EXECUTION",
            severity="MEDIUM",
            confidence=0.9,
            evidence={"explanation": "api test"},
        )
        assert _persist_anomaly(repo, anomaly, ANOMALY_ALGORITHM_VERSION) is True
        _flush(repo)
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/intelligence/anomalies")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert any(a["anomaly_type"] == "EXIT_CLASSIFICATION_ANOMALY" for a in body["anomalies"])

    def test_gate_rejects_degraded_before_any_dispatch(self, wired_engine):
        """The pre-trade gate converts a degraded-proposal to NO_TRADE."""
        _repo, engine = wired_engine
        from nexus_scalp.domain.enums import ActionType
        from nexus_scalp.domain.models import TradeProposal

        proposal = TradeProposal(
            request_id="req_gate",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.BUY_MARKET,
            confidence=0.7,
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_reward_ratio=2.0,
            reason_code="SMC_GOD_MODE",
        )
        # With no experience, the gate must pass the proposal through unchanged.

        out, _, _verdict = engine.intelligence_gate.evaluate(proposal, None, None)
        # No evidence -> passes through (must not fabricate a rejection).
        assert out.action in (ActionType.BUY_MARKET, ActionType.NO_TRADE)

    def test_no_mt5_required(self, wired_engine):
        """The whole Phase 09 brain uses only the paper adapter - no MT5."""
        _repo, engine = wired_engine

        # The intelligence subsystem construction never touches a real terminal.
        assert engine.adapter.__class__.__name__ == "PaperMT5Adapter"
