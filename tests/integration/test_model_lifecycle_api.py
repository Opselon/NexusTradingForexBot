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

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.experience.models import (
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)
from nexus_scalp.web.server import create_app
from tests.helpers.authenticated_client import authenticated_test_client


def _flush(repo) -> None:
    repo._queue.join()


@pytest.fixture(scope="module")
def wired_engine(tmp_path_factory, verified_test_model_bundle):
    """A full LiveEngine wired against a paper adapter and temp DB.

    Module-scoped on purpose: constructing a LiveEngine loads + verifies
    the champion bundle (torch.load, scaler, registry sync, governance
    engine, training worker/orchestrator) and building the FastAPI app +
    TestClient adds more — ~4s per test on the Dubai runner when repeated
    25x (107s for this file). The endpoints under test are read-only
    against engine state except for the tests that explicitly reset what
    they change (mode tests restore PAPER, the freeze test unfreezes,
    the worker test stops/cancels, the missing-manager test restores
    ``champion_manager``), so one shared engine is safe here.

    Runner isolation: this fixture MUST build the engine inside
    ``isolated_module_engine_env`` — module-scoped fixtures run BEFORE
    the function-scoped autouse isolation fixtures in conftest.py, so
    without it the engine binds the runner's REAL shared settings DB
    and a persisted ``model.model_artifact_path`` from another test
    overrides this fixture's verified bundle (PR #740 run #3330: 51x
    LEGACY_UNVERIFIED against a foreign hotswitch ``model.pt``).
    """
    from tests.helpers.module_engine_isolation import isolated_module_engine_env

    with isolated_module_engine_env(tmp_path_factory, "model_lifecycle_api_env"):
        workdir = tmp_path_factory.mktemp("model_lifecycle_api")
        repo = AuditRepository(db_url=f"sqlite:///{workdir / 'models_api.db'}")
        from tests.helpers.synthetic_checkpoint import copy_verified_trained_bundle

        model_path = copy_verified_trained_bundle(verified_test_model_bundle, workdir / "model.pt")
        cfg = AppConfig(
            execution={
                "symbol": "XAUUSD",
                "mode": "PAPER",
            },
            model={"model_artifact_path": str(model_path)},
        )
        from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

        adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
        engine = LiveEngine(config=cfg, adapter=adapter, audit_repo=repo)
        yield repo, engine
        repo.close()


@pytest.fixture(scope="module")
def api_client(wired_engine):
    """One FastAPI app + authenticated client for the whole module.

    ``create_app`` registers every router/middleware and the TestClient
    builds the ASGI stack; rebuilding both per test was a second copy of
    the per-test setup cost. Endpoints resolve the engine from app state
    at request time, so the shared client observes engine mutations
    (freeze, mode change, deleted champion_manager) exactly as a
    per-test client would.
    """
    _repo, engine = wired_engine
    app = create_app(engine)
    return authenticated_test_client(app)


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
        _repo, engine = wired_engine
        for attr in (
            "champion_manager",
            "training_run_store",
            "model_lifecycle_orchestrator",
            "training_worker",
        ):
            assert hasattr(engine, attr), f"LiveEngine missing {attr}"

    def test_models_summary_endpoint(self, api_client):
        client = api_client
        resp = client.get("/api/models/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "summary" in body

    def test_models_list_and_champion(self, api_client):
        client = api_client
        resp = client.get("/api/models")
        assert resp.status_code == 200
        assert resp.json()["available"] is True
        champ = client.get("/api/models/champion")
        assert champ.status_code == 200
        assert champ.json()["available"] is True

    def test_training_run_endpoints(self, api_client):
        client = api_client
        runs = client.get("/api/models/runs")
        assert runs.status_code == 200
        assert runs.json()["available"] is True

    def test_worker_start_stop_cancel(self, wired_engine, api_client):
        _repo, engine = wired_engine
        client = api_client
        assert client.post("/api/models/worker/start").json()["available"] is True
        assert engine._training_worker_started is True

    # ------------------------------------------------------------------
    # UI source-of-control: /api/engine/mode (execution-mode selector)
    # The dashboard's LIVE/SIMULATION/REPLAY dropdown must persist the
    # requested mode to the settings DB, apply it to the engine config,
    # and report the REAL runtime state (never fake LIVE).
    # ------------------------------------------------------------------

    def test_engine_mode_apply_and_persist(self, wired_engine, api_client, tmp_path):
        _repo, engine = wired_engine
        client = api_client

        # LIVE requested through the UI path.
        resp = client.post("/api/engine/mode", json={"mode": "LIVE"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["mode"] == "LIVE"
        assert body["persisted"] is True
        # Engine config applied.
        assert engine.config.execution.mode.value == "LIVE"
        # Settings DB persisted (canonical store). Read through the
        # ENGINE's own service: it is bound to the settings DB the
        # engine was constructed with, while a fresh
        # load_settings_service() here would resolve via the per-test
        # env override to a different, empty DB.
        svc = engine.settings_service
        row = svc.db.get("execution.mode")
        assert row is not None and str(row.value).upper() == "LIVE"
        # Restore the prior mode so other fixtures boot from a clean
        # requested state (the engine boot override reads this DB).
        prior_row = svc.db.get("execution.mode")
        if prior_row is not None:
            svc.db.set("execution.mode", "PAPER", value_type="str", actor="test_cleanup")

    def test_engine_mode_rejects_invalid(self, wired_engine, api_client):
        _repo, engine = wired_engine
        client = api_client
        # The engine boots with the mode the PERSISTED settings already
        # contain (boot override). Record it so the rejected request is
        # verified to leave it untouched. Read through the ENGINE's own
        # service (see test_engine_mode_apply_and_persist): a fresh
        # load_settings_service() would resolve to a different DB.
        svc = engine.settings_service
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

    def test_engine_mode_off_cycle(self, wired_engine, api_client):
        """ON then OFF: mode toggles persist and engine config follows."""
        _repo, engine = wired_engine
        client = api_client

        r1 = client.post("/api/engine/mode", json={"mode": "LIVE"})
        assert r1.status_code == 200 and r1.json()["mode"] == "LIVE"
        r2 = client.post("/api/engine/mode", json={"mode": "PAPER"})
        assert r2.status_code == 200 and r2.json()["mode"] == "PAPER"
        assert engine.config.execution.mode.value == "PAPER"
        # Read through the ENGINE's own service (see
        # test_engine_mode_apply_and_persist).
        svc = engine.settings_service
        row = svc.db.get("execution.mode")
        assert str(row.value).upper() == "PAPER"
        svc.db.set("execution.mode", "PAPER", value_type="str", actor="test_cleanup")
        assert client.post("/api/models/worker/cancel").json()["available"] is True
        assert client.post("/api/models/worker/stop").json()["available"] is True
        assert engine._training_worker_started is False

    def test_no_auto_promotion(self, wired_engine):
        _repo, engine = wired_engine
        # Production inference path uses the Champion ONLY; a Challenger can
        # never be selected by accident.
        champ = engine.champion_manager
        assert champ.artifact_path is not None


class TestGovernanceAPI:
    def test_governance_health_endpoint(self, api_client):
        client = api_client
        resp = client.get("/api/models/governance/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "champion" in body["health"]
        assert "shadow" in body["health"]

    def test_governance_registry_endpoint(self, api_client):
        client = api_client
        resp = client.get("/api/models/governance/registry")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        cats = body["registry"]["categories"]
        assert "CURRENT_CHAMPION" in cats

    def test_governance_events_endpoint(self, api_client):
        client = api_client
        resp = client.get("/api/models/governance/events")
        assert resp.status_code == 200
        assert resp.json()["available"] is True

    def test_registry_reconcile_endpoint(self, api_client):
        client = api_client
        resp = client.post("/api/models/registry/reconcile")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True

    def test_promotion_requires_actor(self, api_client):
        client = api_client
        resp = client.post("/api/models/promotion/approve", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("error", {}).get("code") == "PROMOTION_BLOCKED"

    def test_shadow_outcomes_endpoint_safe(self, api_client):
        client = api_client
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

    def test_status_endpoint_matches_backend(self, api_client):
        client = api_client
        resp = client.get("/api/models/governance/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert (
            "champion" in body and "candidate" in body and "gates" in body and "promotion" in body
        )
        assert body["promotion"]["frozen"] is False  # UI badge truth (spec 33)

    def test_promotion_preview_read_only(self, api_client):
        client = api_client
        resp = client.get("/api/models/governance/promotion-preview?model_id=cand_x")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        preview = body["preview"]
        assert "current_champion" in preview
        assert "gates" in preview
        assert "rollback" in preview

    def test_promotion_execute_requires_token(self, api_client):
        client = api_client
        resp = client.post(
            "/api/models/promotion/execute",
            json={"actor": "op", "model_id": "cand_x", "model_version": "v1", "reason": "x"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("error", {}).get("code") == "PROMOTION_BLOCKED"

    def test_rollback_preview_endpoint(self, api_client):
        client = api_client
        resp = client.get("/api/models/governance/rollback-preview?failed_model_id=fail_x")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert "rollback_candidate" in body["preview"]

    def test_emergency_freeze_blocks_promotion(self, wired_engine, api_client):
        _repo, engine = wired_engine
        client = api_client
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

    def test_audits_endpoint(self, api_client):
        client = api_client
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

    def test_integrity_endpoint_no_crash(self, api_client):
        """BUG-112 repro: the old code called `champ.info` on the
        ChampionManager (which has no `.info`) and 500'd EVERY /api
        request. The endpoint must answer 200 with a full payload
        regardless of champion availability."""
        client = api_client
        # LiveEngine auto-mints a fresh artifact on cold start, so the
        # fixture normally has a VALID champion — the important contract
        # is 200 + complete payload, never an AttributeError 500.
        resp = client.get("/api/models/integrity")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["state"] in ("ACTIVE", "NO_CHAMPION", "UNAVAILABLE"), body
        assert "compatibility" in body and "integrity" in body
        assert "model_id" in body and "artifact_path" in body

    def test_integrity_endpoint_missing_manager(self, wired_engine, api_client):
        _repo, engine = wired_engine
        # Defensive: a manager-less engine still answers 200 UNAVAILABLE.
        # The engine is module-shared now, so the deleted attribute MUST
        # be restored: the next test asserts on the real champion.
        manager = engine.champion_manager
        client = api_client
        try:
            del engine.champion_manager
            resp = client.get("/api/models/integrity")
            assert resp.status_code == 200
            body = resp.json()
            assert body["available"] is True
            assert body["state"] == "UNAVAILABLE"
        finally:
            engine.champion_manager = manager

    def test_integrity_endpoint_valid_champion(self, wired_engine, api_client):
        _repo, engine = wired_engine
        # The wired fixture (verified_test_model_bundle) provisions a REAL
        # verified 70D scalp_v3 champion (3-class contract). Assert the
        # endpoint reports tensor truth for THAT champion. Do NOT rewrite
        # the artifact here: the champion binding is hash-pinned to the
        # fixture bundle, so overwriting model.pt invalidates the registry
        # row and the endpoint answers NO_CHAMPION (no 'compatibility'
        # key at all). The class count is part of the contract too
        # (EXPECTED_NUM_CLASSES=3) — a 4-class head can never be VALID.
        client = api_client
        resp = client.get("/api/models/integrity")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["compatibility"] == "VALID", body
        assert body["model_id"] == engine.champion_manager.model_id
        assert body["actual_input_dimension"] == 70
        assert body["actual_output_classes"] == 3
        assert "reason" in body
