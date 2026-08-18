"""
PHASE 09B Integration — Strategy Research Engine API, Worker & LiveEngine Wiring
===============================================================================
End-to-end verification that the Strategy Research / Backtest / Validation
engine (dataset builder, candidate discovery, gates, registry, worker) is wired
into LiveEngine and exposed through the REST API, while remaining structurally
incapable of executing an order.

Endpoints covered:
    GET  /api/research/summary
    GET  /api/research/registry
    GET  /api/research/registry/{strategy_id}
    GET  /api/research/runs
    POST /api/research/discover
    POST /api/research/validate
    POST /api/research/self-heal

Also verifies:
    * LiveEngine constructs the full research subsystem
    * the research worker start/stop is restart-safe and failure-isolated
    * a seeded experience ledger flows through dataset -> discovery -> validation
    * the research package never exposes MT5 / RiskEngine / OrderManager
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExecutionContext,
    ExperienceOutcome,
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
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'research_api.db'}")
    cfg = AppConfig(
        execution={
            "symbol": "XAUUSD",
            "mode": "PAPER",
        },
        model={"model_artifact_path": str(tmp_path / "model.pt")},
    )
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    engine = LiveEngine(config=cfg, adapter=adapter, audit_repo=repo)
    yield repo, engine
    repo.close()


def seed_strategy_experiences(ledger: ExperienceLedger, repo, count: int = 40) -> None:
    """Seeds N winning closed experiences for one strategy family."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(count):
        rec = ExperienceRecord(
            experience_id=f"exp_res_{i}",
            request_id=f"req_res_{i}",
            idempotency_key=f"int_res_{i}",
            symbol="XAUUSD",
            timeframe="M1",
            decision_timestamp=base + timedelta(minutes=30 * i),
            strategy_id="strat_research_api",
            strategy_version="1.0.0",
            context=StrategyContext(
                strategy_id="strat_research_api",
                symbol="XAUUSD",
                session="LONDON",
                regime="TRENDING",
                volatility_regime="HIGH",
                trend_state="BULLISH",
            ),
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
            realized_r_multiple=0.4,
        )
        ledger.record_experience(rec)
        outcome = ExperienceOutcome(
            idempotency_key=rec.idempotency_key,
            execution_id=f"ticket_res_{i}",
            outcome_timestamp=rec.decision_timestamp + timedelta(minutes=5),
            is_executed=True,
            is_closed=True,
            exit_reason="TP",
            realized_pnl_usd=20.0,
            realized_r_multiple=0.4,
            approved_volume=0.1,
            execution=ExecutionContext(),
        )
        ledger.record_outcome(outcome)
    _flush(repo)


class TestResearchAPI:
    def test_engine_exposes_research_subsystem(self, wired_engine):
        repo, engine = wired_engine
        for attr in (
            "strategy_registry",
            "research_dataset_builder",
            "research_pipeline",
            "research_worker",
        ):
            assert hasattr(engine, attr), f"LiveEngine missing {attr}"

    def test_research_summary_endpoint(self, wired_engine):
        repo, engine = wired_engine
        engine._start_research_worker()
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/research/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "summary" in body
        assert body["summary"]["available"] is True

    def test_discovery_and_validation_via_api(self, wired_engine):
        repo, engine = wired_engine
        seed_strategy_experiences(engine.experience_ledger, repo, count=40)
        app = create_app(engine)
        client = TestClient(app)

        # Discovery: build dataset + candidates.
        disc = client.post("/api/research/discover")
        assert disc.status_code == 200
        body = disc.json()
        assert body["available"] is True
        assert body["samples"] >= 40

        # Find the discovered candidate strategy_id and validate it end-to-end.
        strategy_ids = [c["strategy_id"] for c in body.get("candidates", [])]
        if strategy_ids:
            sid = strategy_ids[0]
            val = client.post(f"/api/research/validate?strategy_id={sid}")
            assert val.status_code == 200
            vbody = val.json()
            assert vbody["available"] is True
            assert vbody["result"]["lifecycle"] in ("VALIDATED", "REJECTED")

    def test_registry_endpoint(self, wired_engine):
        repo, engine = wired_engine
        seed_strategy_experiences(engine.experience_ledger, repo, count=40)
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/research/registry")
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    def test_worker_restart_safe(self, wired_engine):
        repo, engine = wired_engine
        engine._start_research_worker()
        assert engine._research_worker_started is True
        engine.research_worker.tick()
        import asyncio

        asyncio.run(engine._stop_research_worker())
        assert engine._research_worker_started is False
        engine._start_research_worker()
        assert engine._research_worker_started is True
        asyncio.run(engine._stop_research_worker())

    def test_research_never_exposes_mt5_or_risk(self):
        import nexus_scalp.research

        assert not hasattr(nexus_scalp.research, "mt5")
        assert not hasattr(nexus_scalp.research, "MetaTrader5")
        assert not hasattr(nexus_scalp.research, "RiskEngine")
        assert not hasattr(nexus_scalp.research, "OrderManager")
        assert not hasattr(nexus_scalp.research, "OrderLifecycleManager")

    def test_research_health_endpoint(self, wired_engine):
        """TASK-4: /api/research/health explains WHY the registry is empty."""
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/research/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        health = body["health"]
        for key in (
            "source_experiences",
            "canonical_outcomes",
            "eligible_samples",
            "rejected_samples",
            "rejection_reasons",
            "families",
            "candidates_discovered",
            "registry_count",
        ):
            assert key in health, f"missing health key {key}"
        assert isinstance(health["rejection_reasons"], dict)
        assert isinstance(health["eligible_samples"], int)

    def test_self_heal_endpoint(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.post("/api/research/self-heal")
        assert resp.status_code == 200
        assert resp.json()["available"] is True
