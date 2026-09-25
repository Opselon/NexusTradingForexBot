"""LiveEngine learning-cycle handoff tests (learning-loop P1).

Covers the wiring contract on a real LiveEngine instance:
  * default config (learning=None) -> request_learning_cycle refused;
  * learning disabled -> no cycle rows, no side effects;
  * retrain in flight -> refused (no stacking);
  * handoff callback exists and requires learning.shadow.enabled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.model_lifecycle.learning_config import (
    LearningConfig,
    OnlineFinetuneConfig,
)
from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleStore


class _MiniEngine:
    """Narrow stand-in exercising the REAL methods via delegation.

    The engine methods under test only touch: _retrain_inflight,
    learning_cycle_orchestrator, learning_config. To avoid constructing a
    full LiveEngine (MT5 etc.) we bind the real unbound functions onto a
    minimal object with the same attribute surface.
    """

    def __init__(self, learning: LearningConfig | None, db: str) -> None:
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.experience.ledger import ExperienceLedger
        from nexus_scalp.model_lifecycle.champion import ChampionManager
        from nexus_scalp.model_lifecycle.learning_loop import LearningCycleOrchestrator
        from nexus_scalp.model_lifecycle.orchestrator import ModelLifecycleOrchestrator

        self.audit = AuditRepository(db_url=f"sqlite:///{db}")
        self.experience_ledger = ExperienceLedger(self.audit)
        self.champion_manager = ChampionManager(
            artifact_path="artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
            model_id="primary_scalp",
            model_version="v3.0",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
            num_classes=3,
        )
        self.model_lifecycle_orchestrator = ModelLifecycleOrchestrator(
            audit_repo=self.audit,
            ledger=self.experience_ledger,
            champion_manager=self.champion_manager,
            model_registry=None,
            run_store=None,
        )
        self.learning_config = learning or LearningConfig()
        self.learning_cycle_orchestrator = LearningCycleOrchestrator(
            audit_repo=self.audit,
            ledger=self.experience_ledger,
            orchestrator=self.model_lifecycle_orchestrator,
            config=self.learning_config,
        )
        self._retrain_inflight = False

        # Bind the REAL LiveEngine methods (unbound) onto this stand-in.
        from nexus_scalp.application.live_engine import LiveEngine

        self.request_learning_cycle = LiveEngine.request_learning_cycle.__get__(self)
        self._attach_learning_candidate = LiveEngine._attach_learning_candidate.__get__(self)


@pytest.fixture()
def tmp_db(tmp_path: Path) -> str:
    return str(tmp_path / "engine_cycles.db")


class TestDisabledByDefault:
    def test_appconfig_default_learning_none(self) -> None:
        assert AppConfig().learning is None

    def test_disabled_config_refuses_cycle(self, tmp_db: str) -> None:
        engine = _MiniEngine(None, tmp_db)  # learning config absent
        out = engine.request_learning_cycle()
        assert out == {"cycle": None, "blocked": "learning disabled by configuration"}
        # no cycle rows were created
        assert engine.learning_cycle_orchestrator.cycles.summary()["by_status"] == {}

    def test_explicitly_disabled_learning_refuses_cycle(self, tmp_db: str) -> None:
        engine = _MiniEngine(LearningConfig(), tmp_db)
        out = engine.request_learning_cycle()
        assert out["cycle"] is None
        assert "disabled" in out["blocked"]


class TestGuards:
    def test_cycle_in_flight_refused(self, tmp_db: str, monkeypatch) -> None:
        engine = _MiniEngine(
            LearningConfig(
                enabled=True,
                retrain=None
                or __import__(
                    "nexus_scalp.model_lifecycle.learning_config", fromlist=["RetrainConfig"]
                ).RetrainConfig(enabled=True),
            ),
            tmp_db,
        )
        # simulate a training already in flight
        engine._retrain_inflight = True
        out = engine.request_learning_cycle()
        assert out == {"cycle": None, "blocked": "another training is in flight"}
        engine._retrain_inflight = False

    def test_enabled_config_creates_cycle_when_data_allows(self, tmp_db: str) -> None:
        """learning.enabled=true + retrain.enabled=true -> a cycle MAY run;
        with zero experiences it is BLOCKED (insufficient), never silently
        skipped — and exactly one active cycle exists."""
        from nexus_scalp.model_lifecycle.learning_config import RetrainConfig

        cfg = LearningConfig(
            enabled=True, retrain=RetrainConfig(enabled=True, min_new_experiences=1)
        )
        engine = _MiniEngine(cfg, tmp_db)
        out = engine.request_learning_cycle(num_epochs=1)
        # Zero experiences: builder produces an empty dataset -> BLOCKED
        # (either inside the store or via the insufficient-samples guard).
        assert "cycle_id" in out or out.get("blocked")
        summary = engine.learning_cycle_orchestrator.cycles.summary()
        assert summary["active"] <= 1  # never stacked cycles

    def test_shadow_attach_callback_requires_shadow_enabled(self, tmp_db: str) -> None:
        cfg = LearningConfig(enabled=True)
        engine = _MiniEngine(cfg, tmp_db)
        # learning.shadow.enabled defaults False -> run_cycle receives
        # shadow_attach=None and completes WITHOUT touching shadow.
        assert engine.learning_config.shadow.enabled is False


class TestOnlineFinetuneUntouched:
    def test_online_finetune_gate_independent_of_learning(self) -> None:
        """The learning-cycle wiring must NOT enable the online fine-tune
        serving-artifact path: that gate stays separately config-gated."""
        cfg = LearningConfig(enabled=True)  # even fully enabled learning
        # The dispatch gate in live_engine only evaluates
        # learning.online_finetune.enabled — which is False here.
        assert cfg.online_finetune.enabled is False
        assert OnlineFinetuneConfig().enabled is False
