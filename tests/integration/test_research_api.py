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
    POST /api/research/promote   (RC4 repair: explicit operator lifecycle promotion)

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
from nexus_scalp.research.models import CandidateLifecycle, StrategyRegistryEntry
from nexus_scalp.research.models import StrategyScore
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
        _repo, engine = wired_engine
        for attr in (
            "strategy_registry",
            "research_dataset_builder",
            "research_pipeline",
            "research_worker",
        ):
            assert hasattr(engine, attr), f"LiveEngine missing {attr}"

    def test_research_summary_endpoint(self, wired_engine):
        _repo, engine = wired_engine
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
        _repo, engine = wired_engine
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
        _repo, engine = wired_engine
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

    def test_research_health_error_is_generic(self, wired_engine, monkeypatch):
        """CodeQL py/stack-trace-exposure (#66) regression: when the health
        summary raises, the API response carries a STABLE generic error -
        exception text/traceback stays server-side only."""
        from nexus_scalp.research import store as research_store

        def _boom(repo, **kw):
            raise RuntimeError("SECRET_INTERNAL_RESEARCH_PATH")

        monkeypatch.setattr(research_store, "research_health_summary", _boom)
        _repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/research/health")
        assert resp.status_code == 200
        body_text = resp.text
        assert "SECRET_INTERNAL_RESEARCH_PATH" not in body_text
        assert "RuntimeError" not in body_text
        assert "Traceback" not in body_text

    def test_self_heal_endpoint(self, wired_engine):
        _repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.post("/api/research/self-heal")
        assert resp.status_code == 200
        assert resp.json()["available"] is True


# =============================================================================
# RC4 REPAIR: explicit operator-driven promotion lifecycle (VALIDATED ->
# SHADOW -> ACTIVE). The state machine + registry always existed but had NO
# production caller; POST /api/research/promote is the operator workflow.
# =============================================================================


def _validated_entry(strategy_id: str = "strat_promo"):
    """A registry entry carrying COMPLETE validation truth (passes
    StrategyRegistry.invariant_check for VALIDATED)."""
    from nexus_scalp.research.models import (
        BacktestResult,
        OOSResult,
        RobustnessResult,
        StrategyRegistryEntry,
        StrategyScore,
        WalkForwardResult,
    )

    return StrategyRegistryEntry(
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.VALIDATED,
        backtest=BacktestResult(
            strategy_id=strategy_id,
            strategy_version="1.0.0",
            dataset_id="ds_test",
            total_trades=40,
            wins=24,
            losses=16,
            expectancy_r=0.3,
        ),
        walkforward=WalkForwardResult(
            strategy_id=strategy_id,
            strategy_version="1.0.0",
            dataset_id="ds_test",
            passed=True,
        ),
        oos=OOSResult(
            strategy_id=strategy_id,
            strategy_version="1.0.0",
            dataset_id="ds_test",
            status="PASS",
            oos_expectancy_r=0.2,
            oos_samples=12,
        ),
        robustness=RobustnessResult(
            strategy_id=strategy_id,
            strategy_version="1.0.0",
            status="PASS",
        ),
        score=StrategyScore(verdict="VALIDATED", final_score=0.7),
        sample_count=40,
    )


class TestStrategyPromotionAPI:
    """POST /api/research/promote — operator-gated lifecycle advancement."""

    def _client(self, engine):
        app = create_app(engine)
        return TestClient(app)

    def _seed_validated(self, repo, engine, strategy_id="strat_promo"):
        from nexus_scalp.research.models import CandidateLifecycle as CL

        entry = _validated_entry(strategy_id).model_copy(
            update={"lifecycle": CL.VALIDATED}
        )
        assert engine.strategy_registry.upsert(entry) is True
        _flush(repo)

    def test_validated_to_shadow_then_active(self, wired_engine):
        repo, engine = wired_engine
        self._seed_validated(repo, engine)
        client = self._client(engine)

        # Step 1: VALIDATED -> SHADOW (explicit operator call).
        r1 = client.post(
            "/api/research/promote",
            json={
                "strategy_id": "strat_promo",
                "target_lifecycle": "SHADOW",
                "actor": "ops_tester",
                "reason": "shadow evaluation start",
            },
        )
        assert r1.status_code == 200
        b1 = r1.json()
        assert b1["available"] is True and b1["promoted"] is True
        assert b1["lifecycle"] == "SHADOW"

        # Persisted? (registry writes are queued on the audit worker — flush)
        _flush(repo)
        persisted = engine.strategy_registry.get("strat_promo")
        assert persisted is not None and persisted.lifecycle.value == "SHADOW"
        # Audit trail: lineage records the operator actor.
        lineage = persisted.validation_lineage[-1]
        assert "operator_promotion:actor=ops_tester" in lineage

        # Step 2: SHADOW -> ACTIVE (second explicit operator call).
        r2 = client.post(
            "/api/research/promote",
            json={
                "strategy_id": "strat_promo",
                "target_lifecycle": "ACTIVE",
                "actor": "ops_tester",
                "reason": "shadow metrics accepted",
            },
        )
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["available"] is True and b2["promoted"] is True
        assert b2["lifecycle"] == "ACTIVE"
        _flush(repo)
        persisted = engine.strategy_registry.get("strat_promo")
        assert persisted.lifecycle.value == "ACTIVE"

    def test_skip_shadow_blocked(self, wired_engine):
        """VALIDATED -> ACTIVE directly is ILLEGAL — must pass through SHADOW."""
        repo, engine = wired_engine
        self._seed_validated(repo, engine)
        client = self._client(engine)

        resp = client.post(
            "/api/research/promote",
            json={
                "strategy_id": "strat_promo",
                "target_lifecycle": "ACTIVE",
                "actor": "ops_tester",
            },
        )
        body = resp.json()
        assert body.get("error", {}).get("code") == "PROMOTION_BLOCKED"
        # Nothing mutated.
        persisted = engine.strategy_registry.get("strat_promo")
        assert persisted.lifecycle.value == "VALIDATED"

    def test_unknown_strategy_blocked(self, wired_engine):
        _repo, engine = wired_engine
        client = self._client(engine)
        resp = client.post(
            "/api/research/promote",
            json={
                "strategy_id": "does_not_exist",
                "target_lifecycle": "SHADOW",
                "actor": "ops_tester",
            },
        )
        body = resp.json()
        assert body.get("error", {}).get("code") == "PROMOTION_BLOCKED"
        assert "not found" in str(body)

    def test_missing_actor_blocked_no_implicit_promotion(self, wired_engine):
        """No actor => no promotion. Auto/system promotion must be impossible."""
        repo, engine = wired_engine
        self._seed_validated(repo, engine)
        client = self._client(engine)

        resp = client.post(
            "/api/research/promote",
            json={
                "strategy_id": "strat_promo",
                "target_lifecycle": "SHADOW",
            },
        )
        body = resp.json()
        assert body.get("error", {}).get("code") == "PROMOTION_BLOCKED"
        persisted = engine.strategy_registry.get("strat_promo")
        assert persisted.lifecycle.value == "VALIDATED"

    def test_invalid_target_blocked(self, wired_engine):
        repo, engine = wired_engine
        self._seed_validated(repo, engine)
        client = self._client(engine)

        # Only SHADOW / ACTIVE are operator targets.
        for bad in ("VALIDATED", "RETIRED", "BOGUS_STATE"):
            resp = client.post(
                "/api/research/promote",
                json={
                    "strategy_id": "strat_promo",
                    "target_lifecycle": bad,
                    "actor": "ops_tester",
                },
            )
            assert resp.json().get("error", {}).get("code") == "PROMOTION_BLOCKED"

    def test_unvalidated_strategy_cannot_be_activated(self, wired_engine):
        """A DISCOVERED row (no gate evidence) can never advance."""
        from nexus_scalp.research.models import CandidateLifecycle as CL

        repo, engine = wired_engine
        bare = StrategyRegistryEntry(
            strategy_id="strat_bare",
            strategy_version="1.0.0",
            lifecycle=CL.DISCOVERED,
        )
        assert engine.strategy_registry.upsert(bare) is True
        _flush(repo)
        client = self._client(engine)
        resp = client.post(
            "/api/research/promote",
            json={
                "strategy_id": "strat_bare",
                "target_lifecycle": "SHADOW",
                "actor": "ops_tester",
            },
        )
        body = resp.json()
        code = body.get("error", {}).get("code")
        assert code == "PROMOTION_BLOCKED"
        # Either the invariant or the state machine refused it.
        persisted = engine.strategy_registry.get("strat_bare")
        assert persisted.lifecycle.value == "DISCOVERED"

    def test_rejected_strategy_never_reaches_shadow_or_active(self, wired_engine):
        from nexus_scalp.research.models import CandidateLifecycle as CL

        repo, engine = wired_engine
        rejected = StrategyRegistryEntry(
            strategy_id="strat_rejected",
            strategy_version="1.0.0",
            lifecycle=CL.REJECTED,
            score=StrategyScore(verdict="REJECTED"),
        )
        assert engine.strategy_registry.upsert(rejected) is True
        _flush(repo)
        client = self._client(engine)
        for target in ("SHADOW", "ACTIVE"):
            resp = client.post(
                "/api/research/promote",
                json={
                    "strategy_id": "strat_rejected",
                    "target_lifecycle": target,
                    "actor": "ops_tester",
                },
            )
            assert resp.json().get("error", {}).get("code") == "PROMOTION_BLOCKED"
        persisted = engine.strategy_registry.get("strat_rejected")
        assert persisted.lifecycle.value == "REJECTED"

    def test_activation_with_broken_validation_truth_blocked(self, wired_engine):
        """A SHADOW row whose underlying gate truth is missing/broken can
        never reach ACTIVE (activation re-proves VALIDATED-truth)."""
        from nexus_scalp.research.models import CandidateLifecycle as CL

        repo, engine = wired_engine
        hollow_shadow = StrategyRegistryEntry(
            strategy_id="strat_hollow",
            strategy_version="1.0.0",
            lifecycle=CL.SHADOW,
            score=StrategyScore(verdict="REJECTED"),
        )
        assert engine.strategy_registry.upsert(hollow_shadow) is True
        _flush(repo)
        client = self._client(engine)
        resp = client.post(
            "/api/research/promote",
            json={
                "strategy_id": "strat_hollow",
                "target_lifecycle": "ACTIVE",
                "actor": "ops_tester",
            },
        )
        body = resp.json()
        assert body.get("error", {}).get("code") == "PROMOTION_BLOCKED"
        assert "problems" in str(body)
        persisted = engine.strategy_registry.get("strat_hollow")
        assert persisted.lifecycle.value == "SHADOW"
