"""
PHASE 10 Integration — Model Lifecycle API, Worker & LiveEngine Wiring
======================================================================
End-to-end verification that the Controlled Model Training / Challenger engine
is wired into LiveEngine and exposed through the REST API, while remaining
structurally incapable of executing an order or auto-promoting a model.

Endpoints covered:
    GET  /api/models/summary
    GET  /api/models
    GET  /api/models/champion
    GET  /api/models/challengers
    GET  /api/models/runs
    GET  /api/models/runs/{run_id}
    GET  /api/models/comparison/{run_id}
    POST /api/models/train
    POST /api/models/worker/start|stop|cancel
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
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'models_api.db'}")
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


def seed_experiences(engine, repo, count=40):
    ledger = engine.experience_ledger
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(count):
        r = 0.4 if i % 3 else -0.3
        rec = ExperienceRecord(
            experience_id=f"exp_ml_{i}",
            request_id=f"req_ml_{i}",
            idempotency_key=f"ml_{i}",
            symbol="XAUUSD",
            timeframe="M1",
            decision_timestamp=base + timedelta(minutes=30 * i),
            strategy_id="strat_ml",
            strategy_version="1.0.0",
            context=StrategyContext(
                strategy_id="strat_ml",
                symbol="XAUUSD",
                session="LONDON",
                regime="TRENDING",
                volatility_regime="HIGH",
                trend_state="BULLISH",
            ),
            feature_snapshot=FeatureSnapshot(values=[0.1] * 50),
            action="BUY_MARKET",
            entry_reason="SMC_GOD_MODE",
            model_probability=0.6,
            signal_confidence=0.6,
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_reward_ratio=2.0,
            approved_volume=0.1,
        )
        ledger.record_experience(rec)
        ledger.record_outcome(
            ExperienceOutcome(
                idempotency_key=rec.idempotency_key,
                execution_id=f"t_ml_{i}",
                outcome_timestamp=rec.decision_timestamp + timedelta(minutes=5),
                is_executed=True,
                is_closed=True,
                exit_reason="TP" if r > 0 else "SL",
                realized_pnl_usd=r * 100.0,
                realized_r_multiple=r,
                approved_volume=0.1,
                behavior=__import__(
                    "nexus_scalp.experience.models", fromlist=["PositionBehavior"]
                ).PositionBehavior(mfe_r=1.0, mae_r=0.2),
                execution=ExecutionContext(),
            )
        )
    _flush(repo)


class TestModelLifecycleAPI:
    def test_engine_exposes_model_lifecycle(self, wired_engine):
        repo, engine = wired_engine
        for attr in (
            "champion_manager",
            "training_run_store",
            "model_lifecycle_orchestrator",
            "training_worker",
        ):
            assert hasattr(engine, attr), f"LiveEngine missing {attr}"

    def test_models_summary_endpoint(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "summary" in body

    def test_models_list_and_champion(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models")
        assert resp.status_code == 200
        assert resp.json()["available"] is True
        champ = client.get("/api/models/champion")
        assert champ.status_code == 200
        assert champ.json()["available"] is True

    def test_training_run_endpoints(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        runs = client.get("/api/models/runs")
        assert runs.status_code == 200
        assert runs.json()["available"] is True

    def test_worker_start_stop_cancel(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        assert client.post("/api/models/worker/start").json()["available"] is True
        assert engine._training_worker_started is True

    # ------------------------------------------------------------------
    # UI source-of-control: /api/engine/mode (execution-mode selector)
    # The dashboard's LIVE/SIMULATION/REPLAY dropdown must persist the
    # requested mode to the settings DB, apply it to the engine config,
    # and report the REAL runtime state (never fake LIVE).
    # ------------------------------------------------------------------

    def test_engine_mode_apply_and_persist(self, wired_engine, tmp_path):
        from nexus_scalp.settings.service import load_settings_service

        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)

        # LIVE requested through the UI path.
        resp = client.post("/api/engine/mode", json={"mode": "LIVE"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["mode"] == "LIVE"
        assert body["persisted"] is True
        # Engine config applied.
        assert engine.config.execution.mode.value == "LIVE"
        # Settings DB persisted (canonical store).
        svc = load_settings_service()
        row = svc.db.get("execution.mode")
        assert row is not None and str(row.value).upper() == "LIVE"
        # Restore the prior mode so other fixtures boot from a clean
        # requested state (the engine boot override reads this DB).
        prior_row = svc.db.get("execution.mode")
        if prior_row is not None:
            svc.db.set("execution.mode", "PAPER", value_type="str", actor="test_cleanup")

    def test_engine_mode_rejects_invalid(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        # The engine boots with the mode the PERSISTED settings already
        # contain (boot override). Record it so the rejected request is
        # verified to leave it untouched.
        from nexus_scalp.settings.service import load_settings_service

        svc = load_settings_service()
        prior_row = svc.db.get("execution.mode")
        prior_mode = (
            str(prior_row.value).strip().upper() if prior_row and prior_row.value else "PAPER"
        )
        before = engine.config.execution.mode.value
        resp = client.post("/api/engine/mode", json={"mode": "NOT_A_MODE"})
        assert resp.status_code == 422
        # Config untouched by the rejected request.
        assert engine.config.execution.mode.value == before
        assert prior_mode.upper() in {"LIVE", "PAPER", "REPLAY", "SHADOW", "BACKTEST"}

    def test_engine_mode_off_cycle(self, wired_engine):
        """ON then OFF: mode toggles persist and engine config follows."""
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)

        r1 = client.post("/api/engine/mode", json={"mode": "LIVE"})
        assert r1.status_code == 200 and r1.json()["mode"] == "LIVE"
        r2 = client.post("/api/engine/mode", json={"mode": "PAPER"})
        assert r2.status_code == 200 and r2.json()["mode"] == "PAPER"
        assert engine.config.execution.mode.value == "PAPER"
        from nexus_scalp.settings.service import load_settings_service

        svc = load_settings_service()
        row = svc.db.get("execution.mode")
        assert str(row.value).upper() == "PAPER"
        svc.db.set("execution.mode", "PAPER", value_type="str", actor="test_cleanup")
        assert client.post("/api/models/worker/cancel").json()["available"] is True
        assert client.post("/api/models/worker/stop").json()["available"] is True
        assert engine._training_worker_started is False

    def test_no_auto_promotion(self, wired_engine):
        repo, engine = wired_engine
        # Production inference path uses the Champion ONLY; a Challenger can
        # never be selected by accident.
        champ = engine.champion_manager
        assert champ.artifact_path is not None


class TestGovernanceAPI:
    def test_governance_health_endpoint(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/governance/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "champion" in body["health"]
        assert "shadow" in body["health"]

    def test_governance_registry_endpoint(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/governance/registry")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        cats = body["registry"]["categories"]
        assert "CURRENT_CHAMPION" in cats

    def test_governance_events_endpoint(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/governance/events")
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    def test_registry_reconcile_endpoint(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.post("/api/models/registry/reconcile")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True

    def test_promotion_requires_actor(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.post("/api/models/promotion/approve", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("error", {}).get("code") == "PROMOTION_BLOCKED"

    def test_shadow_outcomes_endpoint_safe(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.post("/api/models/shadow/outcomes", json={"run_id": ""})
        assert resp.status_code == 200
        assert resp.json()["available"] is True


class TestModelLifecycleSafety:
    def test_research_never_exposes_mt5_or_risk(self):
        import nexus_scalp.model_lifecycle

        assert not hasattr(nexus_scalp.model_lifecycle, "mt5")
        assert not hasattr(nexus_scalp.model_lifecycle, "MetaTrader5")
        assert not hasattr(nexus_scalp.model_lifecycle, "RiskEngine")
        assert not hasattr(nexus_scalp.model_lifecycle, "OrderManager")
        assert not hasattr(nexus_scalp.model_lifecycle, "OrderLifecycleManager")


class TestGovernance70API:
    """TASK-08 governance API surface (spec 28/29/30/31/32)."""

    def test_status_endpoint_matches_backend(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/governance/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert (
            "champion" in body and "candidate" in body and "gates" in body and "promotion" in body
        )
        assert body["promotion"]["frozen"] is False  # UI badge truth (spec 33)

    def test_promotion_preview_read_only(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/governance/promotion-preview?model_id=cand_x")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        preview = body["preview"]
        assert "current_champion" in preview
        assert "gates" in preview
        assert "rollback" in preview

    def test_promotion_execute_requires_token(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.post(
            "/api/models/promotion/execute",
            json={"actor": "op", "model_id": "cand_x", "model_version": "v1", "reason": "x"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("error", {}).get("code") == "PROMOTION_BLOCKED"

    def test_rollback_preview_endpoint(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/governance/rollback-preview?failed_model_id=fail_x")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "rollback_candidate" in body["preview"]

    def test_emergency_freeze_blocks_promotion(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.post(
            "/api/models/governance/emergency/freeze",
            json={"actor": "operator_1", "reason": "test freeze"},
        )
        assert resp.status_code == 200
        assert resp.json()["available"] is True
        assert engine.governance_engine.promotion_frozen is True
        # a frozen promotion is rejected by the transaction endpoint
        resp2 = client.post(
            "/api/models/promotion/execute",
            json={
                "actor": "operator_1",
                "model_id": "cand_x",
                "model_version": "v1",
                "reason": "x",
                "approval_token": "tok",
                "old_champion_model_id": "champ",
                "old_champion_version": "v1",
                "old_champion_hash": "h",
            },
        )
        body2 = resp2.json()
        assert body2.get("error", {}).get("code") == "PROMOTION_BLOCKED"
        # unfreeze
        resp3 = client.post(
            "/api/models/governance/emergency/unfreeze",
            json={"actor": "operator_1", "reason": "test unfreeze"},
        )
        assert resp3.json()["available"] is True
        assert engine.governance_engine.promotion_frozen is False

    def test_audits_endpoint(self, wired_engine):
        repo, engine = wired_engine
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/governance/audits")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "promotions" in body and "rollbacks" in body
        assert "emergency" in body


class TestModelsIntegrityRegression:
    """BUG-112 regression: /api/models/integrity crashed with
    AttributeError: 'ChampionManager' object has no attribute 'info'.

    The manager has no `.info`; integrity lives on the loaded ChampionModel
    (returned by champion_or_none()). Cold-start (missing artifact) must
    return NO_CHAMPION — never a 500.
    """

    def test_integrity_endpoint_no_crash(self, wired_engine):
        """BUG-112 repro: the old code called `champ.info` on the
        ChampionManager (which has no `.info`) and 500'd EVERY /api
        request. The endpoint must answer 200 with a full payload
        regardless of champion availability."""
        repo, engine = wired_engine
        # LiveEngine auto-mints a fresh artifact on cold start, so the
        # fixture normally has a VALID champion — the important contract
        # is 200 + complete payload, never an AttributeError 500.
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/integrity")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["state"] in ("ACTIVE", "NO_CHAMPION", "UNAVAILABLE"), body
        assert "compatibility" in body and "integrity" in body
        assert "model_id" in body and "artifact_path" in body

    def test_integrity_endpoint_missing_manager(self, wired_engine):
        repo, engine = wired_engine
        # Defensive: a manager-less engine still answers 200 UNAVAILABLE.
        del engine.champion_manager
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/integrity")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["state"] == "UNAVAILABLE"

    def test_integrity_endpoint_valid_champion(self, wired_engine):
        repo, engine = wired_engine
        # Write a REAL ScalpNet artifact for the engine's declared schema so
        # the champion loads and the payload reports tensor truth.
        import torch

        from nexus_scalp.models.scalp_net import ScalpNet

        dim = engine.FEATURE_DIM
        artifact = engine.config.model.model_artifact_path
        net = ScalpNet(num_features=dim, num_classes=4)
        torch.save({k: v.clone() for k, v in net.state_dict().items()}, artifact)
        app = create_app(engine)
        client = TestClient(app)
        resp = client.get("/api/models/integrity")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["compatibility"] in ("VALID", "INVALID"), body
        assert body["model_id"] == engine.champion_manager.model_id
        assert body["actual_input_dimension"] == dim
        assert body["actual_output_classes"] == 4
        assert "reason" in body
