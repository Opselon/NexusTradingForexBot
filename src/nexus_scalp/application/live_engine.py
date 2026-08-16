"""
Live Execution Engine (v6 Enterprise Rewrite)
============================================

Key Improvements
----------------
- Pre-flight validation (config, artifacts, scaler) before LIVE connection.
- Atomic model+scaler bundle loading and swapping under RLock.
- Strict hot-path: no heavy allocations/IO on tick.
- Async retrain worker with backpressure and cancel-safe lifecycle.
- Regime classifier init backward-compatibility.
- Telegram secrets hardening: supports env override; avoids logging tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import signal
import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import torch

from nexus_scalp.accounting import AccountingCore, AccountingWorker
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
    TradeProposal,
)
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import PreTradeExperienceDecision
from nexus_scalp.experience.provenance import ModelRegistry
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.regime_classifier import MarketRegimeClassifier, MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector, ScalpFeatureEngine
from nexus_scalp.features.schema import active_columns, active_dimension, active_schema
from nexus_scalp.intelligence import (
    BehaviorDetectionEngine,
    DecisionContext,
    IntelligenceWorker,
    MarketContext,
    PositionLifecycleTracker,
    PositionPerformance,
    PositionSnapshot,
    PreTradeIntelligenceGate,
    StrategyEvolutionEngine,
    TradeAutopsyEngine,
)
from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.market_data.bar_aggregator import BarAggregator
from nexus_scalp.model_lifecycle.champion import ChampionManager
from nexus_scalp.model_lifecycle.orchestrator import ModelLifecycleOrchestrator
from nexus_scalp.model_lifecycle.store import TrainingRunStore
from nexus_scalp.model_lifecycle.worker import TrainingWorker
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import configure_logging, get_logger
from nexus_scalp.observability.telegram_notifier import TelegramNotifier
from nexus_scalp.ports.mt5_port import IMT5Port
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.worker import ResearchWorker
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.shadow.challenger import ChallengerRuntime
from nexus_scalp.shadow.comparison import ShadowComparer
from nexus_scalp.shadow.engine import ShadowEngine
from nexus_scalp.shadow.store import ShadowStore
from nexus_scalp.shadow.worker import ShadowWorker
from nexus_scalp.signals.policy import SignalPolicy
from nexus_scalp.signals.rule_matrix import RuleMatrixEngine
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

logger = get_logger("nexus_scalp.application.live_engine")


# -----------------------------
# Small supporting structs
# -----------------------------


@dataclass(frozen=True)
class ScalerBundle:
    mean: np.ndarray | None
    std: np.ndarray | None

    def is_ready(self) -> bool:
        return self.mean is not None and self.std is not None

    def transform_50d(self, x_1x50: np.ndarray) -> np.ndarray:
        if not self.is_ready():
            return x_1x50
        # clip to avoid tail explosion
        return np.clip((x_1x50 - self.mean) / self.std, -5.0, 5.0)


@dataclass(frozen=True)
class ModelBundle:
    model: ScalpNet
    scaler: ScalerBundle
    artifact_path: Path


# -----------------------------
# Live Engine
# -----------------------------


class LiveEngine:
    """
    Production Live Orchestrator for XAUUSD scalping.
    """

    #: Live feature contract, resolved from the single schema registry rather than
    #: hard-coded, so a future 60D/350D schema needs no change in this class.
    FEATURE_DIM: int = active_dimension()
    FEATURE_COLS: tuple[str, ...] = active_columns()
    FEATURE_SCHEMA_ID: str = active_schema().schema_id

    def __init__(
        self,
        config: AppConfig,
        adapter: IMT5Port,
        audit_repo: AuditRepository | None = None,
        force_fresh_model: bool = False,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.audit = audit_repo or AuditRepository()
        self.force_fresh_model = bool(force_fresh_model)

        self._running: bool = False
        self.server_state: Any = None

        # Thread-safe model bundle swaps (model+scaler together)
        self._bundle_lock = threading.RLock()
        self._bundle: ModelBundle | None = None

        # Trading runtime state
        self._symbol_info: SymbolInfo | None = None
        self._peak_equity: float = 0.0
        self._last_balance: float = 0.0
        self._last_active_position_count: int = 0

        self._consecutive_losses: int = 0
        self._survival_mode_active: bool = False

        # HTF Warmup State Machine
        self.warmup_state: str = "WARMING_UP"
        self._inference_enabled: bool = False
        self._warmup_attempt: int = 0
        self._last_inference_blocked_log: float = 0.0
        self._last_waiting_log: float = 0.0
        self.H1_REQUIRED_BARS: int = 14
        self.H4_REQUIRED_BARS: int = 14

        # Diagnostics & Heartbeat
        self._last_radar_log_time: float = 0.0

        # Buffers / engines
        symbol = config.execution.symbol
        self.aggregator = BarAggregator(symbol=symbol, timeframe_minutes=1)
        self.feature_engine = ScalpFeatureEngine(symbol=symbol)

        # Module 1: Market Regime Engine (init hardening)
        self.regime_classifier = self._init_regime_classifier(symbol=symbol)

        # Telegram (do NOT trust logged token; allow env override)
        bot_token = os.getenv("NEXUS_TELEGRAM_BOT_TOKEN", config.telegram.bot_token)
        admin_id = os.getenv("NEXUS_TELEGRAM_ADMIN_ID", config.telegram.admin_id)

        self.notifier = TelegramNotifier(
            bot_token=bot_token,
            admin_id=admin_id,
            enabled=config.telegram.enabled,
        )

        # Module 1: Rule Matrix Engine
        self.rule_matrix = RuleMatrixEngine(audit_repo=self.audit)

        # =====================================================================
        # PHASE 08: EXPERIENCE INTELLIGENCE SUBSYSTEM
        # ---------------------------------------------------------------------
        # Constructed BEFORE the model bundle so that experience memory exists
        # independently of any model artifact. The model is registered into the
        # provenance registry afterwards; deleting/retraining/hot-swapping the
        # artifact never touches the ledger.
        # =====================================================================
        self.experience_ledger = ExperienceLedger(audit_repo=self.audit)
        self.experience_evaluator = StrategyEvaluator(audit_repo=self.audit)
        self.experience_retriever = ExperienceRetriever(ledger=self.experience_ledger)
        self.model_registry = ModelRegistry(audit_repo=self.audit)
        self.experience_engine = ExperienceIntelligenceEngine(
            ledger=self.experience_ledger,
            evaluator=self.experience_evaluator,
            retriever=self.experience_retriever,
            enabled=True,
            provenance=self.model_registry.current,
        )

        # =====================================================================
        # PHASE 08: UNIFIED ACCOUNTING & PERFORMANCE INTELLIGENCE CORE
        # ---------------------------------------------------------------------
        # Constructed after the experience subsystem so trade attribution can be
        # joined to Experience identity. The AccountingCore is a READ facade over
        # the authoritative audit tables; it writes no raw financial rows. The
        # AccountingWorker refreshes the derived report cache off the event loop.
        # =====================================================================
        self.accounting_core = AccountingCore(
            audit_repo=self.audit,
            adapter=adapter,
            experience_ledger=self.experience_ledger,
            strategy_evaluator=self.experience_evaluator,
        )
        self.accounting_worker = AccountingWorker(
            core=self.accounting_core,
            interval_sec=30.0,
            lookback_days=90,
        )
        self._accounting_task: asyncio.Task | None = None
        self._accounting_worker_started: bool = False

        # =====================================================================
        # PHASE 09: TRADE INTELLIGENCE BRAIN
        # ---------------------------------------------------------------------
        # Constructed AFTER the Phase 08 experience subsystem and model bundle.
        # Everything here is DERIVED intelligence: it reads the ledger and the
        # live tick path, and it never owns an execution capability.
        # =====================================================================
        self.intelligence_lifecycle = PositionLifecycleTracker(audit_repo=self.audit)
        self.intelligence_autopsy = TradeAutopsyEngine(audit_repo=self.audit)
        self.intelligence_behavior = BehaviorDetectionEngine(audit_repo=self.audit)
        self.intelligence_evolution = StrategyEvolutionEngine(
            audit_repo=self.audit, ledger=self.experience_ledger
        )
        self.intelligence_gate = PreTradeIntelligenceGate(experience_engine=self.experience_engine)
        self.intelligence_worker = IntelligenceWorker(
            audit_repo=self.audit,
            ledger=self.experience_ledger,
            interval_sec=30.0,
            lifecycle=self.intelligence_lifecycle,
            autopsy=self.intelligence_autopsy,
            behavior=self.intelligence_behavior,
            evolution=self.intelligence_evolution,
        )
        self._intelligence_worker_started: bool = False
        #: Most recent Phase 09 suitability verdict, surfaced by the REST API.
        self._last_suitability_verdict: Any = None

        # =====================================================================
        # PHASE 09B: STRATEGY RESEARCH, BACKTEST & VALIDATION ENGINE
        # ---------------------------------------------------------------------
        # Consumes the immutable experience ledger ONLY. Research is OFFLINE /
        # BACKGROUND; it can never place, modify or close an order, and it can
        # never promote a candidate to live automatically.
        # =====================================================================
        self.strategy_registry = StrategyRegistry(audit_repo=self.audit)
        self.research_dataset_builder = ResearchDatasetBuilder(ledger=self.experience_ledger)
        self.research_pipeline = ResearchPipeline(
            dataset_builder=self.research_dataset_builder,
            registry=self.strategy_registry,
        )
        self.research_worker = ResearchWorker(
            audit_repo=self.audit,
            ledger=self.experience_ledger,
            pipeline=self.research_pipeline,
            interval_sec=60.0,
        )
        self._research_worker_started: bool = False

        # =====================================================================
        # PHASE 10: CONTROLLED MODEL TRAINING & CHALLENGER ENGINE
        # ---------------------------------------------------------------------
        # Trains candidate models OFFLINE from verified experience. The
        # production Champion is NEVER touched by candidate training; a
        # Challenger is validated and compared but never auto-promoted.
        # =====================================================================
        self.champion_manager = ChampionManager(
            artifact_path=self.config.model.model_artifact_path,
            model_id="primary_scalp",
            model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
            feature_schema_id=self.FEATURE_SCHEMA_ID,
            feature_dimension=self.FEATURE_DIM,
            num_classes=4,
        )
        self.training_run_store = TrainingRunStore(audit_repo=self.audit)
        self.model_lifecycle_orchestrator = ModelLifecycleOrchestrator(
            audit_repo=self.audit,
            ledger=self.experience_ledger,
            champion_manager=self.champion_manager,
            model_registry=self.model_registry,
            run_store=self.training_run_store,
        )
        self.training_worker = TrainingWorker(
            audit_repo=self.audit,
            ledger=self.experience_ledger,
            orchestrator=self.model_lifecycle_orchestrator,
            interval_sec=300.0,
            max_concurrent_trainings=1,
            auto_train_enabled=False,  # conservative default: operator-triggered
        )
        self._training_worker_started: bool = False

        # =====================================================================
        # PHASE 11: CHALLENGER SHADOW TRADING & CHAMPION EVALUATION
        # ---------------------------------------------------------------------
        # Evaluates a validated Challenger under the SAME live market state as
        # the production Champion. Shadow=ONLY: zero order authority, marked
        # SHADOW/SIMULATED, isolated worker, never blocks the tick path.
        # =====================================================================
        self.shadow_store = ShadowStore(audit_repo=self.audit)
        self.shadow_engine = ShadowEngine(
            store=self.shadow_store,
            comparer=ShadowComparer(),
        )
        self.shadow_worker = ShadowWorker(
            audit_repo=self.audit,
            engine=self.shadow_engine,
            interval_sec=300.0,
            finalize_after_decisions=30,
        )
        self._shadow_worker_started: bool = False
        self._shadow_challenger: ChallengerRuntime | None = None

        # Order/risk/policy
        self.signal_policy = SignalPolicy(
            confidence_threshold=config.model.confidence_threshold,
            cooldown_seconds=4.0,
            rule_matrix=self.rule_matrix,
            algo_config=config.algo,
        )
        self.risk_engine = RiskEngine(
            config=config.risk,
            max_margin_usage_pct=config.risk.max_margin_usage_pct,
            max_allowed_lots=config.risk.max_allowed_lots,
        )
        self.order_manager = OrderLifecycleManager(
            adapter=adapter,
            audit_repo=self.audit,
            notifier=self.notifier,
            rule_matrix=self.rule_matrix,
            algo_config=config.algo,
            risk_engine=self.risk_engine,
            experience_engine=self.experience_engine,
        )

        # Online training toolchain
        self.trainer = WalkForwardTrainer(
            artifact_save_path=Path(self.config.model.model_artifact_path),
            random_seed=42,
            active_class_boost=2.5,  # can be increased to 3.5 after calibration
        )

        self.online_labeler = TripleBarrierLabeler(
            take_profit_atr_mult=1.1,
            stop_loss_atr_mult=1.0,
            max_holding_bars=15,
            friction_usd=0.35,
            embargo_bars=3,
        )

        self._rolling_feature_records: deque[dict] = deque(maxlen=4000)
        self._retrain_interval_bars: int = 50
        self._bars_since_last_retrain: int = 0
        self._retrain_task: asyncio.Task | None = None
        self._retrain_inflight: bool = False

        # Hedging tracker to avoid spamming multiple limit orders per ticket
        self._hedged_tickets: set[int] = set()

        # Web / UI Synchronization states to act as single source of truth
        self._last_tick: TickData | None = None
        self._last_fv: FeatureVector | None = None
        self._last_regime_state: MarketRegimeState | None = None
        self._last_probs: torch.Tensor | None = None
        self._last_proposal: TradeProposal | None = None
        #: Most recent Phase 08 pre-trade verdict, surfaced by the REST API.
        self._last_experience_decision: PreTradeExperienceDecision | None = None

        # Preload model/scaler bundle (pre-flight)
        model_path = Path(self.config.model.model_artifact_path)
        self._bundle = self._load_or_create_bundle(
            model_path=model_path, force_fresh=self.force_fresh_model
        )

        # PHASE 08: register the model that is actually serving live inference.
        # This is metadata only - the experience ledger constructed above is
        # already fully usable even when this artifact was just created fresh.
        self._register_active_model(model_path=model_path, replaced=False)

    def _register_active_model(self, model_path: Path, replaced: bool) -> None:
        """
        Stamps the active model identity onto future experiences.

        Called at startup and after every hot-swap. Historical experiences keep
        the provenance of the model that produced them and are never rewritten.
        """
        try:
            provenance = self.model_registry.register_model(
                artifact_path=model_path,
                model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                feature_schema_id=self.FEATURE_SCHEMA_ID,
                feature_dimension=self.FEATURE_DIM,
                config_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                replaced=replaced,
            )
            self.experience_engine.set_provenance(provenance)
        except Exception as e:
            # Provenance is observability, never a live-path dependency.
            logger.error("[MODEL] provenance registration failed (isolated)", error=str(e))

    def rebuild_experience_intelligence(self) -> int:
        """
        Rebuilds derived strategy intelligence from the immutable ledger.

        Exposed so startup, the REST API and operators can self-heal a corrupt
        derived registry. Raw experience rows are only read.
        """
        try:
            rebuilt = self.experience_engine.self_heal()
            return len(rebuilt)
        except Exception as e:
            logger.error("[SELF_HEAL] FAILED", error=str(e))
            return 0

    # -------------------------
    # Public lifecycle
    # -------------------------

    def start(self) -> None:
        """
        Synchronous entrypoint.
        """
        configure_logging(log_level="INFO", json_format=False, log_to_file=True)
        logger.info(
            "Initializing Live Engine",
            symbol=self.config.execution.symbol,
            mode=self.config.execution.mode.value,
        )

        try:
            # Pre-flight validation BEFORE connecting to broker
            self._preflight_or_raise()
        except Exception as e:
            logger.critical("Pre-flight validation failed", error=str(e), exc_info=True)
            try:
                self.notifier.notify_error(
                    "Engine Startup Pre-Flight", f"Startup pre-flight failed: {e}"
                )
                self.notifier.shutdown(timeout=2.0)
            except Exception:
                pass
            raise

        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            if os.name != "nt":
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
                    except NotImplementedError:
                        pass

            loop.run_until_complete(self.run_loop())

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received. Stopping...")
            self._running = False
        except Exception as e:
            logger.critical("Fatal exception in engine run loop", error=str(e), exc_info=True)
            try:
                self.notifier.notify_error(
                    "Engine Run-Loop Fatal", f"Unhandled critical exception: {e}"
                )
            except Exception:
                pass
            raise

        finally:
            try:
                if loop is not None and not loop.is_closed():
                    loop.run_until_complete(self._shutdown_async())
                    loop.close()
            except Exception:
                pass

    async def stop(self) -> None:
        self._running = False

    async def run_loop(self) -> None:
        """
        Main tick ingestion loop.
        """
        if not self.adapter.connect():
            logger.critical("MT5 connect() failed. Engine shutting down.")
            try:
                self.notifier.notify_error(
                    "MT5 Connectivity",
                    "MT5 connect() failed. Engine shutting down.",
                )
            except Exception:
                pass
            return

        self._running = True
        symbol = self.config.execution.symbol

        account = self.adapter.get_account_info()
        self._symbol_info = self.adapter.get_symbol_info(symbol)

        self._restore_peak_equity(account)
        self._notify_startup(account)

        await self._cold_start_warmup(symbol)

        await self._bootstrap_train_if_ready()

        # PHASE 08 STARTUP SEQUENCE (model-independent):
        #   1. immutable experiences already loaded from disk (SQLite)
        #   2. verify schema/provenance census
        #   3. rebuild derived intelligence off the event loop
        #   4. the active model was registered during construction
        # A missing/rebuilt model artifact does NOT reset any of this.
        await self._startup_experience_self_heal()

        # PHASE 08: start the accounting worker (background derived refresh).
        # It never touches the tick path; the periodic kick below only ever
        # schedules `to_thread` refreshes.
        self._start_accounting_worker()

        # PHASE 09: start the background intelligence worker. Fully isolated:
        # a failure inside it can never stop trading.
        self._start_intelligence_worker()

        # PHASE 09B: start the background strategy research worker. Research is
        # OFFLINE / BACKGROUND (dataset rebuild, discovery, validation gates).
        # Fully isolated: it can never stop trading and never places orders.
        self._start_research_worker()

        # PHASE 10: start the controlled training worker. Heavy training runs
        # ONLY in worker threads, never in the tick pipeline; fully isolated.
        self._start_training_worker()

        # PHASE 11: start the shadow-aggregation worker. Shadow evaluation is
        # bounded + isolated; it can never stop trading or touch orders.
        self._start_shadow_worker()

        logger.info(
            "LIVE CONNECTED",
            login=getattr(account, "login", 0) if account else 0,
            balance=getattr(account, "balance", 0.0) if account else 0.0,
            equity=getattr(account, "equity", 0.0) if account else 0.0,
            symbol=symbol,
            digits=self._symbol_info.digits if self._symbol_info else 2,
            model_path=str(self.config.model.model_artifact_path),
        )

        import time

        self._last_tick_processed_time = time.time()

        while self._running:
            try:
                # Tick Stagnation Watchdog: If no ticks/bars are processed for > 15 seconds, trigger healthcheck & reconnect.
                current_time = time.time()
                if (current_time - self._last_tick_processed_time) > 15.0:
                    # Avoid spamming reconnects if connected but market is closed (e.g. weekend or holidays)
                    if not self.adapter.is_connected():
                        logger.warning(
                            "[WARNING] Tick stream stalled and MT5 disconnected. Triggering MT5 adapter healthcheck & auto-reconnect"
                        )
                        try:
                            self.adapter.disconnect()
                            await asyncio.sleep(1.0)
                            self.adapter.connect()
                        except Exception as conn_err:
                            logger.error(
                                "Error during auto-reconnect in watchdog",
                                error=str(conn_err),
                                exc_info=True,
                            )
                    else:
                        logger.info("[WATCHDOG] Tick stream quiet. MT5 connection remains active.")
                    self._last_tick_processed_time = time.time()

                live_account = self.adapter.get_account_info()
                tick = self.adapter.get_last_tick(symbol)

                if live_account is None or tick is None:
                    await asyncio.sleep(0.2)
                    continue

                if self._symbol_info is None:
                    self._symbol_info = self.adapter.get_symbol_info(symbol)

                self._process_tick_pipeline(tick=tick, account=live_account)
                self._last_tick_processed_time = time.time()
                # PHASE 08: accounting worker kick (throttled internally). This
                # is the ONLY touch point and it schedules bounded to_thread
                # work; it can never block the tick loop.
                if self._accounting_worker_started:
                    try:
                        await asyncio.to_thread(self.accounting_worker.tick)
                    except Exception:
                        # Worker failure is fully isolated; never disturb ticks.
                        pass

                # PHASE 09: intelligence worker kick (throttled internally). It
                # runs in a worker thread and is fully failure-isolated; a
                # failure can never disturb the tick loop.
                if self._intelligence_worker_started:
                    try:
                        await asyncio.to_thread(self.intelligence_worker.tick)
                    except Exception:
                        pass

                # PHASE 09B: research worker kick (throttled internally, runs in
                # a worker thread). Research NEVER runs inside the tick
                # pipeline; a failure here can never disturb trading.
                if self._research_worker_started:
                    try:
                        await asyncio.to_thread(self.research_worker.tick)
                    except Exception:
                        pass

                # PHASE 10: controlled training worker kick (heavy CPU work is
                # bounded to worker threads; training can NEVER block ticks).
                if self._training_worker_started:
                    try:
                        await asyncio.to_thread(self.training_worker.tick)
                    except Exception:
                        pass

                # PHASE 11: shadow-aggregation worker kick (bounded, isolated).
                if self._shadow_worker_started:
                    try:
                        await asyncio.to_thread(self.shadow_worker.tick)
                    except Exception:
                        pass
                await asyncio.sleep(0.05)

            except Exception as e:
                logger.error("Error in live loop", error=str(e), exc_info=True)
                try:
                    self.notifier.notify_error("Real-Time Execution Loop", str(e))
                except Exception:
                    pass
                await asyncio.sleep(1.0)

        await self._shutdown_async()

    async def _shutdown_async(self) -> None:
        # Stop the accounting worker first (derived refresh, not financial truth).
        try:
            await self._stop_accounting_worker()
        except Exception:
            pass

        # PHASE 09: stop the intelligence worker (derived intelligence, isolated).
        try:
            await self._stop_intelligence_worker()
        except Exception:
            pass

        # PHASE 09B: stop the strategy research worker (isolated).
        try:
            await self._stop_research_worker()
        except Exception:
            pass

        # PHASE 10: stop the controlled training worker (isolated).
        try:
            await self._stop_training_worker()
        except Exception:
            pass

        # PHASE 11: stop the shadow-aggregation worker (isolated).
        try:
            await self._stop_shadow_worker()
        except Exception:
            pass

        # Cancel retrain task safely
        try:
            if self._retrain_task and not self._retrain_task.done():
                self._retrain_task.cancel()
                with contextlib.suppress(Exception):
                    await self._retrain_task
        except Exception:
            pass

        try:
            self.adapter.disconnect()
        except Exception:
            pass

        try:
            self.audit.close()
        except Exception:
            pass

        try:
            self.notifier.notify_shutdown(reason="Engine Stopped")
        except Exception:
            pass

        logger.info("Engine shutdown complete.")

    # -------------------------
    # Preflight
    # -------------------------

    def _preflight_or_raise(self) -> None:
        """
        Validate artifacts and critical configuration before live.
        """
        model_path = Path(self.config.model.model_artifact_path)
        if not model_path.parent.exists():
            raise RuntimeError(f"Model directory missing: {model_path.parent}")

        # Model file can be created on cold start; scaler may not exist (allowed).
        if model_path.exists():
            logger.info("Model artifact present", path=str(model_path))
        else:
            logger.warning("Model artifact not found; will initialize fresh", path=str(model_path))

        # Validate schema contract
        if self.config.model.feature_schema_version != "v1.0":
            logger.warning(
                "Feature schema version unexpected",
                version=self.config.model.feature_schema_version,
            )

        # Telegram hardening: never log token
        if self.config.telegram.enabled and (
            not os.getenv("NEXUS_TELEGRAM_BOT_TOKEN") and not self.config.telegram.bot_token
        ):
            logger.warning("Telegram enabled but token missing (env override recommended)")

    # -------------------------
    # Init helpers
    # -------------------------

    def _init_regime_classifier(self, symbol: str) -> MarketRegimeClassifier:
        """
        Initializes the MarketRegimeClassifier matching its active constructor signature.
        """
        try:
            return MarketRegimeClassifier(
                symbol=symbol,
                spread_chop_enter_usd=0.50,
                spread_chop_exit_usd=0.40,
                min_regime_hold_sec=4.0,
                switch_prob_margin=0.10,
            )
        except TypeError:
            return MarketRegimeClassifier(symbol=symbol)

    # -------------------------
    # Model / scaler bundle
    # -------------------------

    def _load_or_create_bundle(self, model_path: Path, force_fresh: bool) -> ModelBundle:
        model = self._load_or_initialize_model_weights(
            model_path=model_path, force_fresh=force_fresh
        )
        scaler = self._load_scaler_artifacts(model_path=model_path)
        return ModelBundle(model=model, scaler=scaler, artifact_path=model_path)

    def _load_or_initialize_model_weights(self, model_path: Path, force_fresh: bool) -> ScalpNet:
        """
        Loads model.pt if present, validates 50D contract, otherwise creates and saves.
        """
        model = ScalpNet(num_features=self.FEATURE_DIM, num_classes=4)
        model.eval()

        if model_path.exists() and not force_fresh:
            state_dict = torch.load(model_path, map_location="cpu")

            expected = model.input_projection.weight.shape
            loaded = state_dict.get("input_projection.weight", torch.empty(0)).shape
            if loaded != expected:
                backup_path = model_path.with_suffix(".pt.corrupt")
                logger.critical(
                    "Checkpoint dimension mismatch; quarantining",
                    expected=str(expected),
                    loaded=str(loaded),
                    backup=str(backup_path),
                )
                try:
                    model_path.rename(backup_path)
                except Exception:
                    pass
                raise RuntimeError(
                    f"Checkpoint dimension mismatch: expected {expected}, got {loaded}"
                )

            model.load_state_dict(state_dict)
            logger.info("Loaded model weights", path=str(model_path))
            return model

        logger.info("Initializing fresh model weights", path=str(model_path))
        self._save_model_weights_atomic(model, model_path)
        return model

    def _load_scaler_artifacts(self, model_path: Path) -> ScalerBundle:
        scaler_path = model_path.with_suffix(".scaler.npz")
        if not scaler_path.exists():
            logger.info("Scaler artifact missing (cold-start acceptable)", path=str(scaler_path))
            return ScalerBundle(mean=None, std=None)

        try:
            data = np.load(scaler_path)
            mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
            std = np.asarray(data["std"], dtype=np.float32).reshape(-1)

            if mean.shape[0] != self.FEATURE_DIM or std.shape[0] != self.FEATURE_DIM:
                raise RuntimeError(
                    f"Scaler dim invalid: mean{mean.shape} std{std.shape} expected ({self.FEATURE_DIM},)"
                )

            logger.info(
                "Loaded scaler artifacts successfully",
                path=str(scaler_path),
                mean_shape=mean.shape,
                std_shape=std.shape,
            )
            return ScalerBundle(mean=mean, std=std)

        except Exception as err:
            logger.warning(
                "Failed to load scaler; fallback to raw features",
                error=str(err),
                path=str(scaler_path),
            )
            return ScalerBundle(mean=None, std=None)

    def _save_model_weights_atomic(self, model: ScalpNet, model_path: Path) -> None:
        """Saves current PyTorch model weights state_dict atomically to disk with thread lock and logging."""
        with self._bundle_lock:
            try:
                model_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = model_path.with_suffix(".pt.tmp")

                # Detach state dict to CPU before saving for HFT thread safety
                cpu_state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
                torch.save(cpu_state, tmp)
                tmp.replace(model_path)

                logger.info(
                    "Saved PyTorch model weights artifact atomically to disk",
                    path=str(model_path),
                    tensor_layers=len(cpu_state),
                )
            except Exception as err:
                logger.error(
                    "Failed to save atomic model weights to disk",
                    error=str(err),
                    path=str(model_path),
                )

    # -------------------------
    # Warmup + bootstrap training
    # -------------------------

    def evaluate_warmup_readiness(self, symbol: str, h1_bars: list, h4_bars: list) -> bool:
        """
        Evaluates HTF bar counts and feature vector validation state to determine if warmup is complete.
        """
        h1_status = "READY" if len(h1_bars) >= self.H1_REQUIRED_BARS else "INSUFFICIENT"
        logger.info(
            f"[WARMUP] H1\nrequired_bars={self.H1_REQUIRED_BARS}\navailable_bars={len(h1_bars)}\nstatus={h1_status}"
        )

        h4_status = "READY" if len(h4_bars) >= self.H4_REQUIRED_BARS else "INSUFFICIENT"
        logger.info(
            f"[WARMUP] H4\nrequired_bars={self.H4_REQUIRED_BARS}\navailable_bars={len(h4_bars)}\nstatus={h4_status}"
        )

        completed_bars = self.aggregator.get_completed_bars()
        htf_fallbacks = 0
        valid_count = 0
        fallback_count = 0
        invalid_count = 0

        if completed_bars:
            last_b = completed_bars[-1]
            last_tick = TickData(
                symbol=symbol,
                timestamp=getattr(last_b, "timestamp", datetime.now(UTC)),
                bid=last_b.close,
                ask=last_b.close + 0.20,
                volume=last_b.tick_volume,
            )
            sample_fv = self.feature_engine.compute_from_bars(completed_bars, last_tick)

            # Check if HTF features are in default cold start fallback states
            if sample_fv.htf_h4_trend == 0.0:
                htf_fallbacks += 1
                logger.warning(
                    "[FEATURE_FALLBACK]\ntimeframe=H4\nfeature=htf_h4_trend\nreason=INSUFFICIENT_H4_BARS\nsource=adapter.get_historical_bars\nfallback=0.0\nwarmup_state="
                    + self.warmup_state
                )
            if sample_fv.htf_h1_momentum == 0.0:
                htf_fallbacks += 1
                logger.warning(
                    "[FEATURE_FALLBACK]\ntimeframe=H1\nfeature=htf_h1_momentum\nreason=INSUFFICIENT_H1_BARS\nsource=adapter.get_historical_bars\nfallback=0.0\nwarmup_state="
                    + self.warmup_state
                )

            x50 = sample_fv.to_tensor_input()
            for val in x50:
                if math.isnan(val) or math.isinf(val):
                    invalid_count += 1
                elif val == 0.0:
                    fallback_count += 1
                else:
                    valid_count += 1

        is_ready = (h1_status == "READY") and (h4_status == "READY") and (htf_fallbacks == 0)

        if not is_ready:
            missing_h1 = max(0, self.H1_REQUIRED_BARS - len(h1_bars))
            missing_h4 = max(0, self.H4_REQUIRED_BARS - len(h4_bars))
            missing_tf = "H1" if missing_h1 > 0 else "H4"
            missing_cnt = missing_h1 if missing_h1 > 0 else missing_h4
            req_cnt = self.H1_REQUIRED_BARS if missing_h1 > 0 else self.H4_REQUIRED_BARS
            avail_cnt = len(h1_bars) if missing_h1 > 0 else len(h4_bars)

            logger.info(
                f"[WARMUP] WAITING\ntimeframe={missing_tf}\nrequired={req_cnt}\navailable={avail_cnt}\nmissing={missing_cnt}\nattempt={self._warmup_attempt}"
            )
            logger.info(
                f"[FEATURE_STATUS]\ntotal_features=50\nvalid={valid_count}\nfallback={fallback_count}\ninvalid={invalid_count}\nhtf_fallbacks={htf_fallbacks}\nstatus=NOT_READY"
            )
            self.warmup_state = "SAFE_NOT_READY"
            self._inference_enabled = False
            logger.error("[WARMUP] FAILED\nreason=INSUFFICIENT_HTF_HISTORY\nstate=SAFE_NOT_READY")
            logger.warning("[INFERENCE] BLOCKED\nreason=HTF_WARMUP_INCOMPLETE")
        else:
            self.warmup_state = "READY"
            self._inference_enabled = True
            logger.info(
                f"[FEATURE_STATUS]\ntotal_features=50\nvalid={valid_count}\nfallback={fallback_count}\ninvalid={invalid_count}\nhtf_fallbacks={htf_fallbacks}\nstatus=READY"
            )
            logger.info(
                f"[WARMUP] COMPLETE\nsymbol={symbol}\nH1={len(h1_bars)}/{self.H1_REQUIRED_BARS}\nH4={len(h4_bars)}/{self.H4_REQUIRED_BARS}\nfallback_features={htf_fallbacks}\nstatus=READY"
            )
            logger.info("[INFERENCE] ENABLED\nreason=HTF_WARMUP_COMPLETE")

        return is_ready

    def _start_accounting_worker(self) -> None:
        """
        Starts the accounting worker (idempotent).

        The worker itself is a throttled synchronous refresher; it is kicked
        periodically via `asyncio.to_thread` from the run loop. This method
        only flips its state so the kick is enabled.
        """
        if self._accounting_worker_started:
            return
        self._accounting_worker_started = True
        try:
            self.accounting_worker.start()
        except Exception as err:
            # Isolation: worker startup must never block the engine.
            logger.error("[ACCOUNTING_WORKER] event=START status=FAILED", error=str(err))
            self._accounting_worker_started = False

    async def _stop_accounting_worker(self) -> None:
        """Stops the accounting worker (idempotent, never raises)."""
        self._accounting_worker_started = False
        try:
            self.accounting_worker.stop()
        except Exception as err:
            logger.error("[ACCOUNTING_WORKER] event=STOP status=FAILED", error=str(err))

    # ---------------------------------------------------------------------
    # PHASE 09: INTELLIGENCE WORKER lifecycle
    # ---------------------------------------------------------------------

    def _start_intelligence_worker(self) -> None:
        """Starts the background intelligence worker (idempotent)."""
        if self._intelligence_worker_started:
            return
        self._intelligence_worker_started = True
        try:
            self.intelligence_worker.start()
        except Exception as err:
            # Isolation: worker startup must never block the engine.
            logger.error("[INTELLIGENCE_WORKER] event=START status=FAILED", error=str(err))
            self._intelligence_worker_started = False

    async def _stop_intelligence_worker(self) -> None:
        """Stops the intelligence worker (idempotent, never raises)."""
        self._intelligence_worker_started = False
        try:
            self.intelligence_worker.stop()
        except Exception as err:
            logger.error("[INTELLIGENCE_WORKER] event=STOP status=FAILED", error=str(err))

    def _start_research_worker(self) -> None:
        """Starts the background strategy research worker (idempotent)."""
        if self._research_worker_started:
            return
        self._research_worker_started = True
        try:
            self.research_worker.start()
        except Exception as err:
            # Isolation: research startup must never block the engine.
            logger.error("[RESEARCH_WORKER] event=START status=FAILED", error=str(err))
            self._research_worker_started = False

    def _start_training_worker(self) -> None:
        """Starts the controlled training worker (idempotent)."""
        if self._training_worker_started:
            return
        self._training_worker_started = True
        try:
            self.training_worker.start()
        except Exception as err:
            # Isolation: training startup must never block the engine.
            logger.error("[TRAINING_WORKER] event=START status=FAILED", error=str(err))
            self._training_worker_started = False

    async def _stop_training_worker(self) -> None:
        """Stops the controlled training worker (idempotent, never raises)."""
        self._training_worker_started = False
        try:
            self.training_worker.stop()
        except Exception as err:
            logger.error("[TRAINING_WORKER] event=STOP status=FAILED", error=str(err))

    def _start_shadow_worker(self) -> None:
        """Starts the shadow-aggregation worker (idempotent)."""
        if self._shadow_worker_started:
            return
        self._shadow_worker_started = True
        try:
            self.shadow_worker.start()
        except Exception as err:
            logger.error("[SHADOW_WORKER] event=START status=FAILED", error=str(err))
            self._shadow_worker_started = False

    async def _stop_shadow_worker(self) -> None:
        """Stops the shadow-aggregation worker (idempotent, never raises)."""
        self._shadow_worker_started = False
        try:
            self.shadow_worker.stop()
        except Exception as err:
            logger.error("[SHADOW_WORKER] event=STOP status=FAILED", error=str(err))

    async def _stop_research_worker(self) -> None:
        """Stops the strategy research worker (idempotent, never raises)."""
        self._research_worker_started = False
        try:
            self.research_worker.stop()
        except Exception as err:
            logger.error("[RESEARCH_WORKER] event=STOP status=FAILED", error=str(err))

    async def _startup_experience_self_heal(self) -> None:
        """
        Verifies experience provenance and rebuilds derived intelligence.

        Runs the rebuild in a worker thread so a large ledger cannot delay the
        first live tick, and is fully exception-isolated: a learning-layer
        failure must never prevent the engine from trading safely.
        """
        try:
            total = await asyncio.to_thread(self.experience_ledger.count_experiences)
            census = await asyncio.to_thread(self.experience_ledger.get_schema_distribution)
            logger.info(
                "[EXPERIENCE] LEDGER LOADED",
                experiences=total,
                schema_distribution=census,
                active_schema=self.experience_engine.provenance.feature_schema_id,
                active_dimension=self.experience_engine.provenance.feature_dimension,
            )
            if total == 0:
                logger.info("[SELF_HEAL] COMPLETE", status="SKIPPED_EMPTY_LEDGER")
                return
            rebuilt = await asyncio.to_thread(self.experience_engine.self_heal)
            logger.info("[EXPERIENCE] DERIVED INTELLIGENCE READY", strategies=len(rebuilt))
        except Exception as e:
            logger.error("[SELF_HEAL] FAILED", error=str(e), exc_info=True)

    async def _cold_start_warmup(self, symbol: str) -> None:
        self._warmup_attempt += 1
        logger.info(f"[WARMUP] START\nsymbol={symbol}\nrequired_timeframes=[H1,H4]")

        # Non-blocking async fetch of HTF historical bars
        h1_bars = (
            await asyncio.to_thread(
                self.adapter.get_historical_bars, symbol, "H1", self.H1_REQUIRED_BARS
            )
            or []
        )
        h4_bars = (
            await asyncio.to_thread(
                self.adapter.get_historical_bars, symbol, "H4", self.H4_REQUIRED_BARS
            )
            or []
        )

        # Fetch 3500 M1 bars (3500 M1 bars = 14.5 H4 bars) to populate full M1/H1/H4 aggregations
        hist_m1_bars = (
            await asyncio.to_thread(self.adapter.get_historical_bars, symbol, "M1", 3500) or []
        )

        for b in hist_m1_bars:
            self.aggregator._completed_bars.append(b)

        completed = self.aggregator.get_completed_bars()
        if len(completed) >= 55:
            last_300 = completed[-300:] if len(completed) > 300 else completed
            for i in range(54, len(last_300)):
                window = last_300[: i + 1]
                b = last_300[i]
                bar_time = getattr(b, "timestamp", getattr(b, "time", datetime.now(UTC)))
                synthetic_tick = TickData(
                    symbol=symbol,
                    timestamp=bar_time,
                    bid=b.close,
                    ask=b.close + 0.20,
                    volume=b.tick_volume,
                )
                fv = self.feature_engine.compute_from_bars(window, synthetic_tick)
                x50 = self._validate_50d_tensor(fv.to_tensor_input(), context="cold_start_warmup")
                record = {f"feat_{idx}": float(x50[idx]) for idx in range(self.FEATURE_DIM)}
                record.update(
                    close=b.close,
                    high=b.high,
                    low=b.low,
                    open=b.open,
                    spread=0.20,
                    atr_m1=fv.atr_m1,
                )
                self._rolling_feature_records.append(record)

        self.evaluate_warmup_readiness(symbol, h1_bars, h4_bars)

        # Immediately extract and update real SMC overlays to prevent cold-start blank canvas in MT5 mode
        completed_bars = self.aggregator.get_completed_bars()
        if completed_bars and hasattr(self, "server_state") and self.server_state is not None:
            raw_atr = (
                self._rolling_feature_records[-1]["atr_m1"]
                if self._rolling_feature_records
                else 1.5
            )
            real_overlays = self.signal_policy.extract_live_chart_overlays(
                completed_bars=completed_bars, atr_val=raw_atr
            )
            bars_list = []
            for b in completed_bars[-250:]:
                bars_list.append(
                    {
                        "time": b.timestamp.isoformat()
                        if hasattr(b.timestamp, "isoformat")
                        else str(b.timestamp),
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.tick_volume,
                        "is_complete": True,
                    }
                )
            self.server_state.update_live_visuals(bars_list, real_overlays)
            logger.info("Cold-start SMC visual overlays successfully bridged to server state!")

    async def _bootstrap_train_if_ready(self) -> None:
        if len(self._rolling_feature_records) < 300:
            return

        logger.info(
            "BOOTSTRAP: initial online fine-tune starting...",
            rows=len(self._rolling_feature_records),
        )
        df_hist = pl.DataFrame(list(self._rolling_feature_records))
        df_labeled = self.online_labeler.label_dataframe(df_hist)

        feature_cols = list(self.FEATURE_COLS)
        logger.info("Training features", feature_cols=feature_cols)

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            return

        # trainer returns updated model (assumed)
        updated_model = self.trainer.fine_tune_online(
            model=bundle.model,
            recent_df=df_labeled,
            feature_cols=feature_cols,
            epochs=5,
            learning_rate=1e-4,
            max_holding_bars=15,
        )
        updated_model.eval()

        # Reload scaler after training (if trainer writes it)
        scaler = self._load_scaler_artifacts(bundle.artifact_path)

        # Atomic swap bundle
        with self._bundle_lock:
            self._bundle = ModelBundle(
                model=updated_model, scaler=scaler, artifact_path=bundle.artifact_path
            )

        self._run_model_diagnostics_and_summary(df_labeled=df_labeled, feature_cols=feature_cols)

        # PHASE 09 HARDENING: if the (possibly rolled-back) model is now in a
        # mono-class collapse, re-initialize it rather than serving the broken
        # baseline until the next rejected fine-tune.
        if self._bundle is not None:
            self._reinitialize_collapsed_model()

    # -------------------------
    # Hot-path tick pipeline
    # -------------------------

    def _process_tick_pipeline(self, tick: TickData, account: AccountInfo) -> None:
        try:
            # Synchronize the live hot-swapped AlgoConfig on every single tick pulse
            self.signal_policy.algo_config = self.config.algo
            self.order_manager.algo_config = self.config.algo
            self.risk_engine.min_risk_reward_ratio = self.config.algo.min_risk_reward_ratio
            self.risk_engine.min_rr_high_confidence = getattr(
                self.config.algo, "min_rr_high_confidence", 1.2
            )
            self.risk_engine.high_confidence_threshold = getattr(
                self.config.algo, "high_confidence_threshold", 0.70
            )

            is_new_bar = self.aggregator.process_tick(tick)

            # cap bars (O(1) amortized)
            if len(self.aggregator._completed_bars) > 4000:
                self.aggregator._completed_bars = self.aggregator._completed_bars[-4000:]

            completed_bars = self.aggregator.get_completed_bars()
            fv = self.feature_engine.compute_from_bars(
                completed_bars=completed_bars, current_tick=tick
            )

            if is_new_bar and completed_bars:
                self._on_new_bar(tick=tick, fv=fv, last_bar=completed_bars[-1])

            # Regime state (Module 1)
            regime_state: MarketRegimeState = self.regime_classifier.classify_tick(
                current_tick=tick,
                is_macro_news_window=False,
            )

            # Manage open positions
            active_positions = self.order_manager.manage_active_positions(
                symbol=tick.symbol,
                current_tick=tick,
                feature_vector=fv,
                symbol_info=self._symbol_info,
                account=account,
            )
            current_pos_count = len(active_positions)

            # PHASE 09: feed the immutable position-lifecycle timeline. This is
            # a pure classification + queued write; it never executes anything
            # and can never block the tick path.
            self._observe_positions(
                positions=active_positions,
                tick=tick,
                fv=fv,
                regime_state=regime_state,
            )

            # Check Warmup Readiness Gate before Inference
            if not self._inference_enabled or self.warmup_state != "READY":
                import time

                curr_t = time.time()

                # On new bar or every 15 seconds, attempt to re-evaluate warmup readiness
                if is_new_bar or (curr_t - getattr(self, "_last_warmup_check_time", 0.0)) >= 15.0:
                    self._last_warmup_check_time = curr_t
                    h1_bars = (
                        self.adapter.get_historical_bars(tick.symbol, "H1", self.H1_REQUIRED_BARS)
                        or []
                    )
                    h4_bars = (
                        self.adapter.get_historical_bars(tick.symbol, "H4", self.H4_REQUIRED_BARS)
                        or []
                    )
                    if self.evaluate_warmup_readiness(tick.symbol, h1_bars, h4_bars):
                        logger.info("[WARMUP] RE-EVALUATION PASSED -> Engine transition to READY")

                if not self._inference_enabled or self.warmup_state != "READY":
                    if curr_t - self._last_inference_blocked_log >= 10.0:
                        logger.warning("[INFERENCE] BLOCKED\nreason=HTF_WARMUP_INCOMPLETE")
                        self._last_inference_blocked_log = curr_t

                    # Return NO_TRADE proposal safely when inference is blocked
                    proposal = TradeProposal(
                        request_id=f"blocked_{int(curr_t)}",
                        symbol=tick.symbol,
                        generated_at=tick.timestamp,
                        action=ActionType.NO_TRADE,
                        confidence=0.0,
                        proposed_entry=tick.bid,
                        stop_loss=tick.bid * 0.99,
                        take_profit=tick.bid * 1.01,
                        risk_reward_ratio=1.0,
                        reason_code="HTF_WARMUP_INCOMPLETE",
                    )
                    self.audit.log_signal(proposal)
                    self._last_tick = tick
                    self._last_fv = fv
                    self._last_regime_state = regime_state
                    self._last_proposal = proposal
                    return

            # Inference
            probs = self._infer_probabilities(fv=fv)

            # Heartbeat radar logging: On EVERY M1 Bar completion or every 10 seconds of active ticks, force log.
            import time

            current_time = time.time()
            force_log = False
            if is_new_bar or (current_time - self._last_radar_log_time) >= 10.0:
                force_log = True
                self._last_radar_log_time = current_time

            # Policy
            proposal = self.signal_policy.evaluate_probabilities(
                probabilities=probs,
                current_tick=tick,
                feature_vector=fv,
                regime_state=regime_state,
                survival_mode=self._survival_mode_active,
                force_log=force_log,
                order_manager=self.order_manager,
            )

            # =================================================================
            # PHASE 08 PRE-TRADE EXPERIENCE INTELLIGENCE GATE
            # -----------------------------------------------------------------
            # Runs AFTER the signal policy and BEFORE risk sizing / dispatch, so
            # a rejection here happens strictly before any order placement. The
            # gate can only down-rank or convert to NO_TRADE; it never sizes,
            # places or modifies an order, and it never blocks the tick loop
            # (score lookups are TTL-cached and rate-limited).
            # =================================================================
            proposal, exp_decision = self.experience_engine.evaluate_proposal(
                proposal=proposal,
                feature_vector=fv,
                regime_state=regime_state,
            )
            self._last_experience_decision = exp_decision

            # =================================================================
            # PHASE 09 PRE-TRADE INTELLIGENCE GATE (suitability / WARN tier)
            # -----------------------------------------------------------------
            # Layers a bounded suitability + WARN decision on top of the Phase 08
            # gate. It can only DOWNGRADE (WARN / PENALIZE / REJECT), never
            # upgrade; rejection is a NO_TRADE before risk sizing / dispatch.
            # =================================================================
            proposal, exp_decision, suitability = self.intelligence_gate.evaluate(
                proposal=proposal, fv=fv, regime=regime_state
            )
            self._last_experience_decision = exp_decision
            self._last_suitability_verdict = suitability

            self.audit.log_signal(proposal)

            # Update synchronization properties for the Web backend
            self._last_tick = tick
            self._last_fv = fv
            self._last_regime_state = regime_state
            self._last_probs = probs
            self._last_proposal = proposal

            # =================================================================
            # PHASE 11: CHALLENGER SHADOW RECORDING (SAME live feature vector)
            # -----------------------------------------------------------------
            # Records the Champion's real decision and runs the Challenger on
            # the IDENTICAL feature vector used by the live path. Purely
            # observational: the Challenger produces a hypothetical proposal
            # only and can never place an order. Bounded + failure-isolated.
            # =================================================================
            self._record_shadow_decision(
                tick=tick,
                fv=fv,
                regime_state=regime_state,
                proposal=proposal,
            )

            # Extract and update real SMC overlays for the live chart canvas
            real_overlays = self.signal_policy.extract_live_chart_overlays(
                completed_bars=completed_bars, atr_val=fv.atr_m1
            )
            if hasattr(self, "server_state") and self.server_state is not None:
                bars_list = []
                for b in completed_bars[-250:]:
                    bars_list.append(
                        {
                            "time": b.timestamp.isoformat(),
                            "open": b.open,
                            "high": b.high,
                            "low": b.low,
                            "close": b.close,
                            "volume": b.tick_volume,
                            "is_complete": True,
                        }
                    )
                forming_bar = self.aggregator.get_current_forming_bar()
                if forming_bar:
                    bars_list.append(
                        {
                            "time": forming_bar.timestamp.isoformat(),
                            "open": forming_bar.open,
                            "high": forming_bar.high,
                            "low": forming_bar.low,
                            "close": forming_bar.close,
                            "volume": forming_bar.tick_volume,
                            "is_complete": False,
                        }
                    )
                self.server_state.update_live_visuals(bars_list, real_overlays)

            policy_decision = proposal
            if policy_decision.action != ActionType.NO_TRADE:
                # ---------------------------------------------------------------
                # AI POSITION REVERSAL: close-then-flip, never stack
                # ---------------------------------------------------------------
                if getattr(policy_decision, "is_ai_reversal", False) or (
                    policy_decision.action == ActionType.CLOSE_POSITION
                    and "AI_REVERSAL_SIGNAL" in (policy_decision.reason_code or "")
                ):
                    reversal_volume = 0.0
                    if self._symbol_info:
                        reversal_volume = self.risk_engine.calculate_volume(
                            entry=policy_decision.proposed_entry,
                            sl=policy_decision.stop_loss,
                            tp=policy_decision.take_profit,
                            account=account,
                            symbol_info=self._symbol_info,
                        )
                        reversal_volume = self.risk_engine.get_clamped_position_size(
                            volume=reversal_volume,
                            account=account,
                            symbol_info=self._symbol_info,
                        )

                    success = self.order_manager.execute_ai_reversal(
                        decision=policy_decision,
                        volume=reversal_volume,
                        current_tick=tick,
                        symbol_info=self._symbol_info,
                    )
                    logger.info(
                        f"[info] AI REVERSAL EXECUTED ticket={policy_decision.ticket} "
                        f"new_action={getattr(policy_decision.reversal_action, 'value', None)} "
                        f"volume={reversal_volume} success={success}"
                    )

                # FOR NEW ENTRY SIGNALS
                elif policy_decision.action in (
                    ActionType.BUY,
                    ActionType.SELL,
                    ActionType.BUY_MARKET,
                    ActionType.SELL_MARKET,
                    ActionType.BUY_LIMIT,
                    ActionType.SELL_LIMIT,
                    ActionType.BUY_STOP,
                    ActionType.SELL_STOP,
                ):
                    if self._symbol_info:
                        dynamic_volume = self.risk_engine.calculate_volume(
                            entry=policy_decision.proposed_entry,
                            sl=policy_decision.stop_loss,
                            tp=policy_decision.take_profit,
                            account=account,
                            symbol_info=self._symbol_info,
                        )
                        # Guarantee that the lot size respects the safety clamp under any mathematical condition
                        dynamic_volume = self.risk_engine.get_clamped_position_size(
                            volume=dynamic_volume,
                            account=account,
                            symbol_info=self._symbol_info,
                        )
                        success = self.order_manager.dispatch_order(policy_decision, dynamic_volume)
                        logger.info(
                            f"[info] DISPATCH ORDER action={policy_decision.action.value} price={policy_decision.proposed_entry} volume={dynamic_volume}"
                        )

                        if success:
                            risk_usd = account.equity * (
                                self.config.risk.risk_per_trade_pct / 100.0
                            )
                            try:
                                mapped_order_type = self.risk_engine._map_action_to_order_type(
                                    policy_decision.action
                                )
                                order_obj = TradeOrder(
                                    order_id=policy_decision.request_id,
                                    symbol=policy_decision.symbol,
                                    order_type=mapped_order_type,
                                    volume=dynamic_volume,
                                    price=policy_decision.proposed_entry,
                                    stop_loss=policy_decision.stop_loss,
                                    take_profit=policy_decision.take_profit,
                                    magic_number=888101,
                                    comment="NSE_HFT_SIZED",
                                )
                                self.notifier.notify_order_opened(
                                    order=order_obj,
                                    risk_usd=risk_usd,
                                    callback=lambda msg_id: (
                                        self.order_manager.register_order_message(
                                            order_obj.order_id, msg_id
                                        )
                                        if msg_id
                                        else None
                                    ),
                                )
                            except Exception:
                                pass
                        else:
                            # Dispatch failed! Clear the price lock immediately so bot is not locked out of trading!
                            self.signal_policy.last_order_price = None
                            self.signal_policy.last_order_time = None
                            self.signal_policy._last_active_direction = None
                            self.signal_policy._last_active_direction_time = None
                            self.signal_policy._last_executed_price = 0.0

                # FOR POSITION LIFECYCLE ACTIONS
                elif policy_decision.action in (
                    ActionType.CLOSE_POSITION,
                    ActionType.PARTIAL_CLOSE,
                    ActionType.MODIFY_SL_TP,
                    ActionType.CANCEL_ORDER,
                ):
                    self.order_manager.execute_lifecycle_action(policy_decision)
                    ticket = getattr(policy_decision, "ticket", 0) or 0
                    logger.info(
                        f"[info] DISPATCH LIFECYCLE ACTION action={policy_decision.action.value} ticket={ticket}"
                    )

            # Evaluate intelligent hedging / counter-position policy
            self._evaluate_hedging_policy(
                active_positions=active_positions,
                tick=tick,
                probs=probs,
                regime_state=regime_state,
                fv=fv,
                account=account,
            )

            # Equity / drawdown tracking + audit
            self._update_survival_state(account=account, current_pos_count=current_pos_count)
            self.audit.log_account_snapshot(account=account, peak_equity=self._peak_equity)
            # Keep the order manager's account snapshot fresh so closed-trade autopsy rows
            # carry accurate balance/equity/drawdown values.
            self.order_manager.update_account_snapshot(
                account=account, peak_equity=self._peak_equity
            )

        except Exception as pipeline_err:
            logger.error(
                "Silent recovery: exception caught in hot-path tick processing pipeline",
                error=str(pipeline_err),
                exc_info=True,
            )

    # ---------------------------------------------------------------------
    # PHASE 09: position lifecycle observation
    # ---------------------------------------------------------------------

    def _observe_positions(
        self,
        positions: list[Position],
        tick: TickData,
        fv: FeatureVector,
        regime_state: MarketRegimeState | None,
    ) -> None:
        """
        Feeds the immutable position-lifecycle timeline from the live path.

        Every open position becomes a `PositionSnapshot` + `MarketContext` +
        `DecisionContext` observation; the tracker classifies which lifecycle
        events to emit. Fully exception-isolated and non-blocking.
        """
        try:
            market = MarketContext(
                symbol=tick.symbol,
                timeframe="M1",
                session="ALL",
                market_regime=regime_state.regime_type.value if regime_state else "UNKNOWN",
                volatility_state="NORMAL",
                atr=max(float(fv.atr_m1 or 0.0), 0.0),
                spread=float(max(0.0, tick.ask - tick.bid)),
            )
            for pos in positions:
                if pos.volume <= 0.0:
                    continue
                snapshot = PositionSnapshot(
                    entry_price=pos.price_open,
                    current_price=tick.bid if pos.type == OrderType.BUY else tick.ask,
                    volume=pos.volume,
                    stop_loss=pos.sl,
                    take_profit=pos.tp,
                    floating_pnl=pos.profit,
                )
                # Risk-normalised excursions from the order manager trackers.
                perf = self._position_performance(pos.ticket)
                self.intelligence_lifecycle.observe_position(
                    ticket=pos.ticket,
                    snapshot=snapshot,
                    performance=perf,
                    market=market,
                    decision=self._position_decision_context(pos.ticket, pos.symbol),
                    at=tick.timestamp,
                )
        except Exception as obs_err:
            logger.error("[POSITION_TRACK] observation failed (isolated)", error=str(obs_err))

    def _position_performance(self, ticket: int) -> PositionPerformance:
        """Builds risk-normalised excursion performance from order-manager state."""
        try:
            om = self.order_manager
            planned_risk = abs(
                om._entry_prices.get(ticket, 0.0) - om._entry_sls.get(ticket, 0.0)
            ) or (om._entry_atr.get(ticket, 1.5) * 1.5)
            mfe_points = float(om._mfe_tracker.get(ticket, 0.0))
            mae_points = float(om._mae_tracker.get(ticket, 0.0))
            peak_profit = float(om._peak_profit_usd.get(ticket, 0.0))
            peak_dd = float(om._peak_drawdown_usd.get(ticket, 0.0))
            mfe_r = abs(mfe_points) / planned_risk if planned_risk > 1e-9 else 0.0
            mae_r = abs(mae_points) / planned_risk if planned_risk > 1e-9 else 0.0
            entry_time = om._entry_timestamps.get(ticket)
            duration = (datetime.now(UTC) - entry_time).total_seconds() if entry_time else 0.0
            giveback = 0.0
            if peak_profit > 0.0:
                floating = float(om._mfe_tracker.get(ticket, 0.0))
                giveback = max(0.0, (peak_profit - floating) / peak_profit)
            return PositionPerformance(
                mfe=mfe_r,
                mae=mae_r,
                max_profit_reached=peak_profit,
                max_loss_reached=peak_dd,
                profit_giveback_pct=giveback,
                holding_duration_sec=max(0.0, duration),
            )
        except Exception:
            return PositionPerformance()

    def _position_decision_context(self, ticket: int, symbol: str) -> DecisionContext:
        """Resolves the decision identity that produced this position, if known."""
        try:
            om = self.order_manager
            strategy_id = om._entry_reasons.get(ticket, "")
            feature_schema = self.FEATURE_SCHEMA_ID
            return DecisionContext(
                strategy_id=strategy_id or f"unknown_{symbol}",
                strategy_version="1.0.0",
                feature_schema_id=feature_schema,
                model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                confidence=float(om._entry_confidences.get(ticket, 0.0)),
                probability=float(om._entry_confidences.get(ticket, 0.0)),
            )
        except Exception:
            return DecisionContext()

    def _evaluate_hedging_policy(
        self,
        active_positions: list[Position],
        tick: TickData,
        probs: torch.Tensor,
        regime_state: MarketRegimeState | None,
        fv: FeatureVector,
        account: AccountInfo,
    ) -> None:
        """
        Intelligent Hedging / Counter-Position Policy (PyTorch & Regime-Driven).
        """
        import time

        from nexus_scalp.domain.enums import ActionType, OrderType
        from nexus_scalp.domain.models import TradeProposal
        from nexus_scalp.features.regime_classifier import RegimeType

        # Garbage collect closed tickets
        active_tickets = {pos.ticket for pos in active_positions}
        self._hedged_tickets &= active_tickets

        atr = max(self.order_manager._safe_feature_float(fv, "atr_m1", 1.50), 0.50)
        probs_list = probs.squeeze().tolist()
        if not isinstance(probs_list, list):
            probs_list = [probs_list]
        prob_buy = probs_list[1] if len(probs_list) > 1 else 0.0
        prob_sell = probs_list[2] if len(probs_list) > 2 else 0.0

        for pos in active_positions:
            # Only evaluate positions currently in drawdown
            if pos.profit >= 0.0:
                continue

            if pos.ticket in self._hedged_tickets:
                continue

            # Evaluate whether hold score has dropped below threshold or volatility has shifted
            hold_score = self.order_manager._hold_score_tracker.get(pos.ticket, 100)

            hold_score_dropped = hold_score < 50
            volatility_shifted = False
            if regime_state and regime_state.regime_type == RegimeType.VOLATILITY_EXPANSION:
                if hold_score < 75 or pos.profit < -0.50:
                    volatility_shifted = True

            if not (hold_score_dropped or volatility_shifted):
                continue

            # Prevent per-tick log spam if active position capacity is already full
            symbol_positions = [p for p in active_positions if p.symbol == pos.symbol]
            if len(symbol_positions) >= self.config.risk.max_concurrent_positions:
                self._hedged_tickets.add(pos.ticket)
                continue

            logger.info(
                "Hedging trigger met for position in drawdown",
                ticket=pos.ticket,
                hold_score=hold_score,
                volatility_shifted=volatility_shifted,
                pnl=pos.profit,
            )

            # Determine whether to hedge or average using PyTorch model predictions and regime indicators
            if pos.type == OrderType.BUY:
                if prob_buy >= prob_sell or (
                    regime_state and regime_state.regime_type == RegimeType.RANGING_MEAN_REVERSION
                ):
                    is_buy_limit = True
                    target_entry = round(tick.bid - atr * 1.0, 2)
                    stop_loss = round(target_entry - atr * 1.5, 2)
                    take_profit = round(pos.price_open, 2)
                else:
                    is_buy_limit = False
                    target_entry = round(tick.ask + atr * 1.0, 2)
                    stop_loss = round(target_entry + atr * 1.5, 2)
                    take_profit = round(tick.bid - atr * 1.5, 2)
            elif prob_sell >= prob_buy or (
                regime_state and regime_state.regime_type == RegimeType.RANGING_MEAN_REVERSION
            ):
                is_buy_limit = False
                target_entry = round(tick.ask + atr * 1.0, 2)
                stop_loss = round(target_entry + atr * 1.5, 2)
                take_profit = round(pos.price_open, 2)
            else:
                is_buy_limit = True
                target_entry = round(tick.bid - atr * 1.0, 2)
                stop_loss = round(target_entry - atr * 1.5, 2)
                take_profit = round(tick.ask + atr * 1.5, 2)

            action = ActionType.BUY_LIMIT if is_buy_limit else ActionType.SELL_LIMIT

            proposal = TradeProposal(
                request_id=f"hedge_{pos.ticket}_{int(time.time())}",
                symbol=pos.symbol,
                generated_at=tick.timestamp,
                action=action,
                confidence=float(max(prob_buy, prob_sell)),
                proposed_entry=float(target_entry),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                risk_reward_ratio=1.35,
                reason_code=f"HEDGE_TRIGGER_{action.name}_SCORE_{hold_score}",
            )

            if self._symbol_info:
                hedge_order = self.risk_engine.evaluate_proposal(
                    proposal=proposal,
                    account=account,
                    symbol_info=self._symbol_info,
                    active_positions=active_positions,
                    current_tick=tick,
                    regime_state=regime_state,
                    atr=atr,
                )

                if hedge_order:
                    logger.info(
                        "Dispatching intelligent hedging limit order",
                        original_ticket=pos.ticket,
                        action=hedge_order.order_type.value,
                        volume=hedge_order.volume,
                        price=hedge_order.price,
                    )
                    success = self.order_manager.execute_order(hedge_order)
                    if success:
                        self._hedged_tickets.add(pos.ticket)
                        try:
                            self.notifier.notify_generic_message(
                                title="Intelligent Hedging Activated",
                                message=(
                                    f"Position {pos.ticket} is in drawdown (Hold Score: {hold_score}). "
                                    f"Placed hedging order {hedge_order.order_type.value} of "
                                    f"{hedge_order.volume} lots at {hedge_order.price}."
                                ),
                            )
                        except Exception:
                            pass

    def _on_new_bar(self, tick: TickData, fv, last_bar) -> None:
        x50 = self._validate_50d_tensor(fv.to_tensor_input(), context="new_bar_record")
        rec = {f"feat_{i}": float(x50[i]) for i in range(self.FEATURE_DIM)}
        rec.update(
            close=last_bar.close,
            high=last_bar.high,
            low=last_bar.low,
            open=last_bar.open,
            spread=(tick.ask - tick.bid),
            atr_m1=fv.atr_m1,
        )
        self._rolling_feature_records.append(rec)
        self._bars_since_last_retrain += 1

        if (
            self._bars_since_last_retrain >= self._retrain_interval_bars
            and len(self._rolling_feature_records) >= 300
            and not self._retrain_inflight
        ):
            try:
                loop = asyncio.get_running_loop()
                self._retrain_task = loop.create_task(self._trigger_async_online_fine_tune())
            except RuntimeError:
                pass

    def _infer_probabilities(self, fv) -> torch.Tensor:
        x50 = self._validate_50d_tensor(fv.to_tensor_input(), context="live_inference")
        x_np = np.array(x50, dtype=np.float32).reshape(1, -1)

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            raise RuntimeError("Model bundle not initialized")

        x_np = bundle.scaler.transform_50d(x_np)
        x = torch.tensor(x_np, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)

        bundle.model.eval()
        with torch.inference_mode():
            return bundle.model(x)

    def _record_shadow_decision(
        self,
        tick: TickData,
        fv: Any,
        regime_state: MarketRegimeState,
        proposal: TradeProposal,
    ) -> None:
        """
        Records one parallel Champion/Challenger decision on the SAME live
        feature vector (spec 3 / 4). Bounded + failure-isolated: a Challenger
        fault must never affect production execution (spec 17).
        """
        if self._shadow_challenger is None:
            return
        try:
            engine = self.shadow_engine
            if not engine.active_run_id:
                return
            # Same feature vector the Champion used:
            x50 = (
                fv.to_tensor_input() if hasattr(fv, "to_tensor_input") else [0.0] * self.FEATURE_DIM
            )
            feature_hash = getattr(fv, "feature_hash", "") or str(hash(tuple(x50[:5])))
            regime_str = getattr(getattr(regime_state, "regime", None), "value", "UNKNOWN")
            if isinstance(regime_str, str) is False and regime_str is not None:
                regime_str = str(regime_str)
            from nexus_scalp.shadow.models import ShadowModelRef

            champ = self.champion_manager.champion_or_none()
            champ_ref = ShadowModelRef(
                model_id=(champ.model_id if champ else self.champion_manager.model_id),
                model_version=(
                    champ.model_version if champ else self.champion_manager.model_version
                ),
                feature_schema_id=self.FEATURE_SCHEMA_ID,
                feature_dimension=self.FEATURE_DIM,
                artifact_hash=(champ.artifact_hash if champ else ""),
                is_champion=True,
            )
            engine.set_champion_ref(champ_ref)
            engine.record_shadow_decision(
                timestamp=tick.timestamp,
                symbol=tick.symbol,
                timeframe="M1",
                feature_hash=feature_hash,
                feature_schema_id=self.FEATURE_SCHEMA_ID,
                feature_dimension=self.FEATURE_DIM,
                regime=regime_str,
                session=getattr(proposal, "session", "") or "ALL",
                configuration_version=str(getattr(self.config.model, "feature_schema_version", "")),
                champion_ref=champ_ref,
                champion_action=proposal.action.value
                if hasattr(proposal.action, "value")
                else str(proposal.action),
                champion_confidence=float(getattr(proposal, "confidence", 0.0)),
                champion_probabilities=[
                    float(v)
                    for v in (self._last_probs.tolist() if self._last_probs is not None else [])
                ],
                champion_strategy_id="",
                decision_id=getattr(proposal, "request_id", ""),
                feature_vector=x50,
            )
        except Exception as e:
            # Shadow is observability only: a failure here NEVER disturbs live.
            logger.error("[SHADOW] event=RECORD_FAILURE (isolated)", error=str(e))

    # -------------------------
    # Async retraining worker
    # -------------------------

    async def _trigger_async_online_fine_tune(self) -> None:
        if self._retrain_inflight:
            return
        self._retrain_inflight = True
        try:
            logger.info("ASYNC RETRAIN START", buffer_size=len(self._rolling_feature_records))

            df = pl.DataFrame(list(self._rolling_feature_records))
            df_labeled = self.online_labeler.label_dataframe(df)
            feature_cols = list(self.FEATURE_COLS)

            with self._bundle_lock:
                bundle = self._bundle
            if bundle is None:
                return

            # Run training off loop thread
            updated_model = await asyncio.to_thread(
                self.trainer.fine_tune_online,
                bundle.model,
                df_labeled,
                feature_cols,
                3,  # epochs
                1e-4,  # lr
                15,  # max_holding_bars
            )
            updated_model.eval()

            # Refresh scaler + persist weights
            scaler = self._load_scaler_artifacts(bundle.artifact_path)
            self._save_model_weights_atomic(updated_model, bundle.artifact_path)

            with self._bundle_lock:
                self._bundle = ModelBundle(
                    model=updated_model, scaler=scaler, artifact_path=bundle.artifact_path
                )

            # PHASE 08: the model artifact was just rewritten. Re-register its
            # provenance so NEW experiences carry the new identity. Existing
            # experiences, strategy memory and lifecycle state are untouched -
            # a retrain never resets learning memory.
            self._register_active_model(model_path=bundle.artifact_path, replaced=True)

            self._bars_since_last_retrain = 0
            logger.info("ASYNC RETRAIN SUCCESS")

        except Exception as err:
            logger.error("Async retrain failed", error=str(err), exc_info=True)

        finally:
            self._retrain_inflight = False

    # -------------------------
    # Diagnostics
    # -------------------------

    def _run_model_diagnostics_and_summary(
        self, df_labeled: pl.DataFrame, feature_cols: list[str]
    ) -> None:
        logger.info("=== MODEL DIAGNOSTICS ===")

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            logger.warning("No model bundle for diagnostics")
            return

        # Test 1: forward pass sanity
        sample_x_np = (
            df_labeled.select(feature_cols).tail(20).to_numpy().astype(np.float32, copy=False)
        )
        sample_x_np = bundle.scaler.transform_50d(sample_x_np)
        x = torch.tensor(sample_x_np, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)

        with torch.inference_mode():
            p = bundle.model(x)
        test1_pass = not (torch.isnan(p).any() or torch.isinf(p).any())

        # Test 2/3: calibrated class distribution
        test_df = df_labeled.tail(100)
        test_x_np = test_df.select(feature_cols).to_numpy().astype(np.float32, copy=False)
        test_x_np = bundle.scaler.transform_50d(test_x_np)
        tx = torch.tensor(test_x_np, dtype=torch.float32)
        tx = torch.nan_to_num(tx, nan=0.0, posinf=1.0, neginf=-1.0)

        with torch.inference_mode():
            probs = bundle.model(tx).cpu().numpy()

        buy_probs = probs[:, 1]
        sell_probs = probs[:, 2]
        threshold = float(self.config.model.confidence_threshold)

        raw_preds = np.argmax(probs[:, :3], axis=1)
        preds = np.zeros(len(probs), dtype=int)
        for i in range(len(probs)):
            c = raw_preds[i]
            if c == 1 and buy_probs[i] >= threshold:
                preds[i] = 1
            elif c == 2 and sell_probs[i] >= threshold:
                preds[i] = 2
            else:
                preds[i] = 0

        total = len(preds)
        buy_pct = float(np.sum(preds == 1) / max(total, 1) * 100.0)
        sell_pct = float(np.sum(preds == 2) / max(total, 1) * 100.0)
        no_trade_pct = float(np.sum(preds == 0) / max(total, 1) * 100.0)

        test3_pass = buy_pct < 85.0 and sell_pct < 85.0

        logger.info(
            "MODEL SUMMARY",
            test1_tensor_sanity="PASS" if test1_pass else "FAIL",
            class_dist=f"BUY {buy_pct:.1f}% | SELL {sell_pct:.1f}% | NO_TRADE {no_trade_pct:.1f}%",
            threshold=f"{threshold:.2f}",
            status="HEALTHY" if (test1_pass and test3_pass) else "WARNING",
        )
        logger.info("=======================")

    # -------------------------
    # Model collapse detection & auto-recovery
    # -------------------------

    def _detect_model_collapse(
        self, df_labeled: pl.DataFrame, feature_cols: list[str]
    ) -> dict[str, float] | None:
        """
        Runs the model over a recent sample and returns the class distribution.

        Returns None when no bundle is available. The caller decides whether the
        distribution indicates a mono-class collapse and how to react.
        """
        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            return None
        try:
            test_df = df_labeled.tail(100)
            test_x_np = test_df.select(feature_cols).to_numpy().astype(np.float32, copy=False)
            test_x_np = bundle.scaler.transform_50d(test_x_np)
            tx = torch.tensor(test_x_np, dtype=torch.float32)
            tx = torch.nan_to_num(tx, nan=0.0, posinf=1.0, neginf=-1.0)
            with torch.inference_mode():
                probs = bundle.model(tx).cpu().numpy()
            buy_probs = probs[:, 1]
            sell_probs = probs[:, 2]
            threshold = float(self.config.model.confidence_threshold)
            raw_preds = np.argmax(probs[:, :3], axis=1)
            preds = np.zeros(len(probs), dtype=int)
            for i in range(len(probs)):
                c = raw_preds[i]
                if c == 1 and buy_probs[i] >= threshold:
                    preds[i] = 1
                elif c == 2 and sell_probs[i] >= threshold:
                    preds[i] = 2
                else:
                    preds[i] = 0
            total = max(len(preds), 1)
            return {
                "buy_pct": float(np.sum(preds == 1) / total * 100.0),
                "sell_pct": float(np.sum(preds == 2) / total * 100.0),
                "no_trade_pct": float(np.sum(preds == 0) / total * 100.0),
            }
        except Exception as e:
            logger.error("[MODEL] collapse detection failed (isolated)", error=str(e))
            return None

    def _reinitialize_collapsed_model(self) -> bool:
        """
        Detects a mono-class prediction collapse (>= 85% on a single active class)
        and re-initializes the live model with fresh weights.

        Previously a collapsed baseline (e.g. 100% SELL) was kept serving live
        ticks: the fine-tuning quality gate rejected every update and rolled back
        to the SAME collapsed baseline, so the engine never escaped the bad state.
        Re-initialization is atomic under `_bundle_lock` and only touches the model
        weights - the experience ledger and strategy memory are untouched.
        """
        try:
            # Build a small sample from the rolling feature buffer.
            if len(self._rolling_feature_records) < 32:
                return False
            df = pl.DataFrame(list(self._rolling_feature_records))
            feature_cols = list(self.FEATURE_COLS)
            dist = self._detect_model_collapse(df, feature_cols)
            if dist is None:
                return False
            buy_pct = dist["buy_pct"]
            sell_pct = dist["sell_pct"]
            # A healthy model must not be dominated by a single active class.
            collapsed = buy_pct >= 85.0 or sell_pct >= 85.0
            if not collapsed:
                return False

            logger.warning(
                "[MODEL] MONO_CLASS_COLLAPSE_DETECTED - re-initializing weights",
                buy_pct=round(buy_pct, 1),
                sell_pct=round(sell_pct, 1),
                no_trade_pct=round(dist["no_trade_pct"], 1),
            )
            model_path = Path(self.config.model.model_artifact_path)
            fresh = ScalpNet(num_features=self.FEATURE_DIM, num_classes=4)
            fresh.eval()
            with self._bundle_lock:
                self._bundle = ModelBundle(
                    model=fresh,
                    scaler=self._bundle.scaler if self._bundle else None,
                    artifact_path=model_path,
                )
            self._save_model_weights_atomic(fresh, model_path)
            self._register_active_model(model_path=model_path, replaced=True)
            logger.warning("[MODEL] COLLAPSE_RECOVERY_COMPLETE - fresh weights serving live ticks")
            return True
        except Exception as e:
            logger.error("[MODEL] collapse recovery failed (isolated)", error=str(e))
            return False

    # -------------------------
    # Risk/survival tracking
    # -------------------------

    def _restore_peak_equity(self, account: AccountInfo | None) -> None:
        last_snapshot = self.audit.get_last_account_snapshot()
        if last_snapshot and "peak_equity" in last_snapshot:
            self._peak_equity = float(last_snapshot["peak_equity"])
            logger.info(
                "Restored peak equity from audit DB", peak_equity=f"{self._peak_equity:.2f}"
            )
        elif account:
            self._peak_equity = float(account.equity)

        if account:
            self._last_balance = float(account.balance)

    def _notify_startup(self, account: AccountInfo | None) -> None:
        if not account:
            return
        try:
            self.notifier.notify_startup(
                symbol=self.config.execution.symbol,
                mode=self.config.execution.mode.value,
                balance=account.balance,
                equity=account.equity,
            )
        except Exception:
            pass

    def _update_survival_state(self, account: AccountInfo, current_pos_count: int) -> None:
        # Withdrawal adjustment heuristic retained
        if self._last_balance > 0.0:
            balance_delta = account.balance - self._last_balance
            no_trade_was_closed = current_pos_count >= self._last_active_position_count
            if (
                balance_delta < 0.0
                and no_trade_was_closed
                and abs(balance_delta) > (account.equity * 0.02)
            ):
                self._peak_equity += balance_delta
                logger.info(
                    "Withdrawal detected; adjusted peak equity",
                    peak_equity=f"{self._peak_equity:.2f}",
                )

        self._last_balance = float(account.balance)
        self._last_active_position_count = int(current_pos_count)

        if account.equity > self._peak_equity:
            self._peak_equity = float(account.equity)
            self._consecutive_losses = 0
            if self._survival_mode_active:
                self._survival_mode_active = False
                try:
                    self.notifier.notify_survival_mode_changed(active=False, drawdown_pct=0.0)
                except Exception:
                    pass

        elif account.equity < self._peak_equity and self._peak_equity > 0:
            drawdown_pct = ((self._peak_equity - account.equity) / self._peak_equity) * 100.0
            if (
                drawdown_pct > (self.config.risk.max_account_drawdown_pct * 0.5)
                and not self._survival_mode_active
            ):
                self._survival_mode_active = True
                logger.warning("SURVIVAL MODE ON", drawdown_pct=round(drawdown_pct, 2))
                try:
                    self.notifier.notify_survival_mode_changed(
                        active=True, drawdown_pct=drawdown_pct
                    )
                except Exception:
                    pass

            if drawdown_pct > self.config.risk.max_account_drawdown_pct:
                logger.critical("MAX DRAWDOWN EXCEEDED; HALTING", dd_pct=round(drawdown_pct, 2))
                try:
                    self.notifier.notify_kill_switch_activated(
                        f"Max Drawdown Exceeded ({drawdown_pct:.2f}%)"
                    )
                except Exception:
                    pass
                self._running = False

    # -------------------------
    # -------------------------
    # Feature contract validation (schema-driven)
    # -------------------------

    @classmethod
    def _validate_50d_tensor(cls, features: Sequence[float], context: str) -> list[float]:
        """
        Validates and sanitizes a feature vector against the ACTIVE schema.

        Name kept for backward compatibility with existing call sites and tests;
        the width itself comes from `FEATURE_DIM` (schema registry), so this
        function keeps working unchanged when the contract widens.
        """
        if len(features) != cls.FEATURE_DIM:
            raise RuntimeError(
                f"Feature contract violation in {context}: schema={cls.FEATURE_SCHEMA_ID} "
                f"expected {cls.FEATURE_DIM}, got {len(features)}"
            )

        out: list[float] = []
        for idx, val in enumerate(features):
            try:
                f = float(val)
            except Exception:
                logger.warning(
                    "Non-numeric feature sanitized", context=context, feature=f"feat_{idx}"
                )
                f = 0.0

            if not np.isfinite(f):
                logger.warning(
                    "Non-finite feature sanitized", context=context, feature=f"feat_{idx}"
                )
                f = 0.0

            out.append(float(np.clip(f, -3.0, 3.0)))
        return out
