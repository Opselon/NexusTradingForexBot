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
import time
import uuid
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus_scalp.hygiene.worker_runner import DatabaseHygieneWorker
    from nexus_scalp.incidents.telemetry import IncidentTelemetryCollector
    from nexus_scalp.incidents.worker import IncidentWorker

import numpy as np
import polars as pl
import torch

from nexus_scalp.accounting import AccountingCore, AccountingWorker
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.candle_intelligence import (
    CandleIntelligenceConfig,
    CandleIntelligenceEngine,
    RegimeState,
)

# RUNTIME CONFIGURATION (hot reload): the authoritative runtime provider.
# Consumers read the current immutable snapshot; live.yaml is bootstrap-only.
from nexus_scalp.configuration import RuntimeConfigStore
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ActionType, ExecutionMode, OrderType
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
from nexus_scalp.features.liquidity_runtime import LiquidityGovernor
from nexus_scalp.features.regime_classifier import MarketRegimeClassifier, MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector, ScalpFeatureEngine
from nexus_scalp.features.schema import active_columns, active_dimension, active_schema
from nexus_scalp.governance import (
    GovernanceEvent,
    GovernanceShadowRuntime,
    GovernanceStage,
    GovernanceStore,
    ModelGovernanceEngine,
)
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
from nexus_scalp.model_generation.setup_detector import SetupDetector
from nexus_scalp.model_lifecycle.champion import ChampionManager
from nexus_scalp.model_lifecycle.models import ModelStatus
from nexus_scalp.model_lifecycle.orchestrator import ModelLifecycleOrchestrator
from nexus_scalp.model_lifecycle.persist_decision import decision_of
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
from nexus_scalp.settings import (
    load_settings_service,
)
from nexus_scalp.shadow.challenger import ChallengerRuntime
from nexus_scalp.shadow.comparison import ShadowComparer
from nexus_scalp.shadow.engine import ShadowEngine
from nexus_scalp.shadow.store import ShadowStore
from nexus_scalp.shadow.worker import ShadowWorker
from nexus_scalp.signals.policy import SignalPolicy
from nexus_scalp.signals.rule_matrix import RuleMatrixEngine
from nexus_scalp.strategies.factory import (
    AutonomousLoopWorker,
    EvolutionConfig,
    StrategyFactory,
)
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

logger = get_logger("nexus_scalp.application.live_engine")


def _split_telegram_report(text: str, max_len: int = 3500) -> list[str]:
    """Deterministic paragraph-boundary splitter for oversized Telegram
    reports. Splits on blank-line groups so section headers stay intact;
    rejoining the chunks reproduces the original text exactly."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


# -----------------------------
# Small supporting structs
# -----------------------------


@dataclass(frozen=True)
class ScalerBundle:
    mean: np.ndarray | None
    std: np.ndarray | None

    def is_ready(self) -> bool:
        """False when mean/std are missing OR any std is zero/negative/non-finite.

        OBS-PERF-RESILIENCE: a scaler with a zero (or negative / non-finite)
        std divides by zero — numpy silently emits ±inf/±5.0-clipped garbage
        with only a RuntimeWarning, which past the tensor-stage
        ``nan_to_num`` (neginf -> -1.0) quietly poisons the model input.
        Such a scaler is NOT ready: transform() must pass features through
        UNCHANGED and the caller must see the raw values, never fabricated
        ones (no-silent-fallback contract).
        """
        if self.mean is None or self.std is None:
            return False
        try:
            return bool(np.all(np.isfinite(self.std)) and np.all(self.std > 0.0))
        except Exception:
            return False

    def dimension(self) -> int | None:
        """Declared scaler width (mean/std length) or None when not ready."""
        if self.mean is None or self.std is None:
            return None
        try:
            return int(self.mean.shape[0])
        except Exception:
            return None

    def transform(self, x: np.ndarray) -> np.ndarray:
        """Dimension-agnostic scaler (70D/50D/60D) - clips tails to [-5,+5]."""
        if not self.is_ready():
            return x
        return np.clip((x - self.mean) / self.std, -5.0, 5.0)

    def transform_50d(self, x_1x50: np.ndarray) -> np.ndarray:
        """Backward-compat alias: delegates to the dimension-agnostic transform."""
        return self.transform(x_1x50)


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

    # ------------------------------------------------------------------
    # BUG-125: EFFECTIVE MODEL CONTRACT - artifact-driven, not class-frozen.
    #
    # The class constants above are the BOOTSTRAP default (scalp_v1/50D).
    # The authoritative live contract is derived from the LOADED BUNDLE:
    # a validated 70D artifact (scaler width 70 + tensor width 70) drives
    # effective_feature_dim=70 / effective_feature_schema_id=scalp_v3, so
    # the canonical 70D tensor (Base 0..49 | News 50..59 | Liquidity
    # 60..69) is assembled for inference. With the 50D Champion loaded the
    # effective contract stays scalp_v1/50D and behavior is byte-identical
    # to the pre-BUG-125 hot path. One source of truth: the bundle itself.
    # ------------------------------------------------------------------

    @property
    def effective_feature_dim(self) -> int:
        """Authoritative feature width of the LOADED model bundle.

        Resolution order: scaler width (mean/std length) > model tensor
        width (num_features) > class bootstrap default. Never raises -- a
        probe failure falls back to the class default (50D-safe).
        """
        with contextlib.suppress(Exception):
            with self._bundle_lock:
                b = self._bundle
            if b is not None:
                d = b.scaler.dimension() if hasattr(b.scaler, "dimension") else None
                if isinstance(d, int) and d > 0:
                    return d
                nf = int(getattr(b.model, "num_features", 0) or 0)
                if nf > 0:
                    return nf
        return int(self.__class__.FEATURE_DIM)

    @property
    def effective_feature_schema_id(self) -> str:
        """Schema id bound to the LOADED model's dimension.

        70D bundles bind to the canonical scalp_v3 contract
        (features/schema_contract.py); everything else keeps the ACTIVE
        schema id (scalp_v1). This is the single authoritative mapping --
        no duplicated hardcoded dimensions anywhere in the engine.
        """
        try:
            if self.effective_feature_dim == 70:
                from nexus_scalp.features.schema_contract import SCHEMA_ID as _SCHEMA_70D

                return _SCHEMA_70D
        except Exception:
            pass
        return str(self.__class__.FEATURE_SCHEMA_ID)

    @property
    def effective_feature_cols(self) -> tuple[str, ...]:
        """Ordered feat_* columns for the effective contract."""
        return tuple(f"feat_{i}" for i in range(self.effective_feature_dim))

    def _retrain_record_dim(self) -> int:
        """BUG-185: contract width for rolling-retrain buffer records.

        The buffer is consumed ONLY by the online fine-tune path, whose
        trainer is rebound to the LOADED bundle's contract (BUG-182B).
        Records must therefore be built at the bundle's width — the class
        bootstrap (FEATURE_DIM) is only correct while the bundle is None
        or matches it. Never raises; falls back to the class contract so
        pre-bundle construction phases keep their existing behavior.
        """
        with contextlib.suppress(Exception):
            with self._bundle_lock:
                b = self._bundle
            if b is not None:
                d = b.scaler.dimension() if hasattr(b.scaler, "dimension") else None
                if isinstance(d, int) and d > 0:
                    return d
                nf = int(getattr(b.model, "num_features", 0) or 0)
                if nf > 0:
                    return nf
        return int(self.__class__.FEATURE_DIM)

    def _build_retrain_record(
        self,
        *,
        base50: Sequence[float],
        fv: Any,
        bar: Any,
        spread: float,
        context: str,
    ) -> dict[str, Any]:
        """BUG-185 PART-3: canonical retrain-buffer record assembly.

        A 70D champion record MUST carry the full canonical scalp_v3
        geometry (Base 0..49 | News 50..59 | Liquidity 60..69) — NOT a
        50-element base slice indexed over a 70-wide range (the IndexError
        class). Base features are validated exactly as the live inference
        path validates them; News 10D uses the SAME canonical projection as
        the live 70D assembly (news_10d_from_context); Liquidity 10D is the
        governor's real causal snapshot (VALID + 10 floats + bounds). When
        the real liquidity block is not yet available the record is REFUSED
        (None) — never zero-filled — so no fabricated liquidity row can
        ever enter online training (INV-009 / no-silent-pad rule).
        """
        record_dim = self._retrain_record_dim()
        base = self._validate_50d_tensor(base50, context=context)
        rec: dict[str, Any] = {f"feat_{i}": float(base[i]) for i in range(len(base))}

        if record_dim >= 60:
            # News 10D (indices 50..59): CANONICAL projection, cache-only
            # read (INV-001). BUG-190: the raw CurrentNewsContext.model_dump()
            # uses different key names than the canonical training schema
            # (active_event_count / bullish_score / bearish_score / state /
            # missing novelty) - the canonical mapping (vectorize_news_context
            # -> build_news_10) is the single projection, matching inference.
            # Absent context is the documented DISABLED projection (0.0 x10),
            # never a fabrication of live data.
            news10: list[float]
            try:
                news_ctx: Any = None
                if (
                    getattr(self, "_news_enabled", False)
                    and getattr(self, "news_engine", None) is not None
                ):
                    try:
                        news_ctx = self.news_engine.current_context()
                    except Exception:
                        news_ctx = None
                if news_ctx is None:
                    news10 = [0.0] * 10
                else:
                    from nexus_scalp.governance.alignment import vectorize_news_context
                    from nexus_scalp.shadow.shadow70.news_provider import build_news_10

                    news10, _ = build_news_10(vectorize_news_context(news_ctx))
            except Exception as news_err:  # isolated; refuse > fabricate
                logger.warning("[ONLINE_TRAIN] event=NEWS_BLOCK_UNAVAILABLE error=%s", news_err)
                news10 = [0.0] * 10
            for j, v in enumerate(news10):
                rec[f"feat_{50 + j}"] = float(v)

        if record_dim >= 70:
            # Liquidity 10D (indices 60..69): REAL causal snapshot only.
            gov = getattr(self, "liquidity_governor", None)
            snap = getattr(gov, "last_snapshot", None) if gov is not None else None
            causal = (
                getattr(gov, "causal_state", lambda: "INVALID")() if gov is not None else "INVALID"
            )
            liq10: list[float] | None = None
            if snap is not None and causal == "VALID":
                try:
                    vec = list(snap.features)
                    if len(vec) == 10 and all(-3.0 <= float(v) <= 3.0 for v in vec):
                        liq10 = [float(v) for v in vec]
                except Exception:
                    liq10 = None
            if liq10 is None:
                # Same refusal contract as the live 70D inference path:
                # no snapshot / stale -> SKIP the record (never fabricate).
                logger.warning(
                    "[ONLINE_TRAIN] event=RECORD_SKIPPED reason=LIQUIDITY_SNAPSHOT_NOT_VALID "
                    "causal=%s context=%s (no fabricated liquidity enters training)",
                    causal,
                    context,
                )
                return None

            for j, v in enumerate(liq10):
                rec[f"feat_{60 + j}"] = float(v)

        if len(rec) != record_dim:
            # Defensive safety net (NOT the primary fix): structured refusal
            # instead of a raw IndexError / partial record.
            logger.error(
                "[ONLINE_TRAIN] event=FEATURE_CONTRACT_MISMATCH expected_dim=%s "
                "actual_dim=%s context=%s action=SKIP",
                record_dim,
                len(rec),
                context,
            )
            return None

        rec.update(
            close=bar.close,
            high=bar.high,
            low=bar.low,
            open=bar.open,
            spread=spread,
            atr_m1=fv.atr_m1,
        )
        return rec

    def _rebind_trainer_to_bundle(self) -> None:
        """BUG-185: bind the online trainer to the LOADED bundle's contract.

        Extracted from __init__ (BUG-182B moved the call here). Called at
        boot AND from every bundle-mutation site (hot swap, promotion,
        rollback, collapse recovery) so a contract-width change can never
        leave the trainer bound to the previous width. No-op while widths
        already agree; self-disables online training when the artifact dim
        has no registered schema (fail-safe, never fabricates).
        """
        try:
            with self._bundle_lock:
                _b0 = self._bundle
            _eff_dim0 = int(
                _b0.scaler.dimension()
                if _b0 is not None and hasattr(_b0.scaler, "dimension")
                else (getattr(_b0.model, "num_features", 0) if _b0 is not None else 0) or 0
            )
        except Exception:
            _eff_dim0 = 0
        if _eff_dim0 > 0 and _eff_dim0 != self.trainer.num_features:
            # BUG-185: resolve by DIMENSION (module-level schema_for_dimension),
            # not by hard-coding scalp_v3 for 70 — the registry owns the
            # mapping and a 50D rebind (hot-swap back) must also restore
            # scalp_v1.
            from nexus_scalp.features.schema import schema_for_dimension as _sfd

            _schema = _sfd(_eff_dim0)
            if _schema is not None and _schema.dimension == _eff_dim0:
                self.trainer.feature_schema = _schema
                self.trainer.num_features = _eff_dim0
                logger.info(
                    "[ONLINE_TRAIN] trainer rebound to loaded-bundle contract",
                    artifact_dim=_eff_dim0,
                    schema=_schema.schema_id,
                )
            else:
                logger.warning(
                    "[ONLINE_TRAIN] loaded artifact dim %s has no registered schema; "
                    "online fine-tune will self-disable (no width crash, no clobber)",
                    _eff_dim0,
                )
                self._online_train_disabled = True
        # FIX #1+#8: also rebind live temporal sequence contract
        with contextlib.suppress(Exception):
            self._rebind_live_temporal_contract()

    # ----------------------------
    # FIX #1+#8: live temporal contract helpers — delegate to LiveSequenceService
    # ----------------------------
    def _live_sequence_defaults(self) -> None:
        from nexus_scalp.application.live_sequence import LiveSequenceService

        st = LiveSequenceService.defaults()
        self._live_sequence_buffer = st.buffer
        self._live_sequence_seq_len = st.seq_len
        self._live_sequence_max_gap_us = st.max_gap_us
        self._live_last_bar_ts_us = st.last_bar_ts_us
        self._live_sequence_gap_invalid = st.gap_invalid

    def _rebind_live_temporal_contract(self) -> None:
        if not hasattr(self, "_live_sequence_buffer"):
            self._live_sequence_defaults()
        from nexus_scalp.application.live_sequence import LiveSequenceService, LiveSequenceState

        state = LiveSequenceState(
            buffer=self._live_sequence_buffer,
            seq_len=self._live_sequence_seq_len,
            max_gap_us=self._live_sequence_max_gap_us,
            last_bar_ts_us=self._live_last_bar_ts_us,
            gap_invalid=self._live_sequence_gap_invalid,
        )
        meta = None
        try:
            import json as _json
            from pathlib import Path as _Path

            b = self._bundle
            mp = getattr(b, "artifact_path", None) if b is not None else None
            if mp is not None:
                meta_p = _Path(str(mp)).with_suffix(".meta.json")
                if meta_p.exists():
                    meta = _json.loads(meta_p.read_text(encoding="utf-8"))
        except Exception:
            meta = None
        LiveSequenceService.rebind_from_meta(state, meta)
        self._live_sequence_buffer = state.buffer
        self._live_sequence_seq_len = state.seq_len
        self._live_sequence_max_gap_us = state.max_gap_us
        self._live_last_bar_ts_us = state.last_bar_ts_us
        self._live_sequence_gap_invalid = state.gap_invalid

    def _maybe_build_live_sequence_tensor(self, x_scaled_now, bar_ts=None):
        from nexus_scalp.application.live_sequence import LiveSequenceService, LiveSequenceState

        state = LiveSequenceState(
            buffer=self._live_sequence_buffer,
            seq_len=self._live_sequence_seq_len,
            max_gap_us=self._live_sequence_max_gap_us,
            last_bar_ts_us=self._live_last_bar_ts_us,
            gap_invalid=self._live_sequence_gap_invalid,
        )
        result = LiveSequenceService.maybe_build_sequence_tensor(state, x_scaled_now, bar_ts)
        self._live_sequence_buffer = state.buffer
        self._live_sequence_seq_len = state.seq_len
        self._live_sequence_max_gap_us = state.max_gap_us
        self._live_last_bar_ts_us = state.last_bar_ts_us
        self._live_sequence_gap_invalid = state.gap_invalid
        return result

    def note_bar_gap(self, gap_us: int) -> None:
        from nexus_scalp.application.live_sequence import LiveSequenceService, LiveSequenceState

        state = LiveSequenceState(
            buffer=self._live_sequence_buffer,
            seq_len=self._live_sequence_seq_len,
            max_gap_us=self._live_sequence_max_gap_us,
            last_bar_ts_us=self._live_last_bar_ts_us,
            gap_invalid=self._live_sequence_gap_invalid,
        )
        LiveSequenceService.note_bar_gap(state, gap_us)
        self._live_sequence_buffer = state.buffer
        self._live_last_bar_ts_us = state.last_bar_ts_us
        self._live_sequence_gap_invalid = state.gap_invalid

    def reset_live_sequence(self) -> None:
        from nexus_scalp.application.live_sequence import LiveSequenceService, LiveSequenceState

        state = LiveSequenceState(
            buffer=self._live_sequence_buffer,
            seq_len=self._live_sequence_seq_len,
            max_gap_us=self._live_sequence_max_gap_us,
            last_bar_ts_us=self._live_last_bar_ts_us,
            gap_invalid=self._live_sequence_gap_invalid,
        )
        LiveSequenceService.reset(state)
        self._live_sequence_buffer = state.buffer
        self._live_last_bar_ts_us = state.last_bar_ts_us
        self._live_sequence_gap_invalid = state.gap_invalid

    def __init__(
        self,
        config: AppConfig,
        adapter: IMT5Port,
        audit_repo: AuditRepository | None = None,
        force_fresh_model: bool = False,
        mode_override: ExecutionMode | None = None,
    ) -> None:
        self.config = config
        self.adapter = adapter
        # BUG-148: explicit operator mode (CLI --mode). Highest authority at
        # boot — beats any persisted settings-DB execution.mode value.
        self._mode_override: ExecutionMode | None = mode_override
        # BUG-130: pre-declare the order manager BEFORE any init section can
        # fail. A construction exception mid-__init__ must never leave
        # run_loop reaching for a missing attribute — the guard below treats
        # None as "not ready yet" instead of crashing the reconciliation.
        self.order_manager: OrderLifecycleManager | None = None
        if audit_repo is not None:
            self.audit = audit_repo
        else:
            # DATABASE PORTABILITY: resolve the authoritative provider from the
            # settings database + environment; SQLite remains the default.
            from nexus_scalp.database.config import load_database_config

            self.audit = AuditRepository(config=load_database_config("audit"))
        self.force_fresh_model = bool(force_fresh_model)
        # BUG-232: mode-session generation. Bumped on every cross-boundary
        # hot-swap; stale-tick / stale-proposal checks compare against it so
        # an event derived from the old adapter's feed can never mutate the
        # new mode's state (the PAPER->LIVE 2000.08 dispatch defect).
        self._mode_session_generation: int = 0
        # BUG-226: boot-time provenance tag for the audit stream. Derived from
        # the EFFECTIVE execution mode after the BUG-212 adapter alignment, so
        # a PAPER boot tags every ledger row and snapshot it writes as PAPER
        # (LIVE remains the safe default when the mode is unknown).
        try:
            _boot_source = str(getattr(self.config.execution.mode, "value", "") or "").upper()
        except Exception:
            _boot_source = ""
        self._boot_account_source = (
            _boot_source if _boot_source in ("LIVE", "PAPER", "SHADOW") else "LIVE"
        )

        # =====================================================================
        # RUNTIME CONFIGURATION (hot reload core): the authoritative
        # versioned provider. live.yaml is BOOTSTRAP-only; after startup the
        # engine consumes the immutable snapshot (see _sync_runtime_config).
        # =====================================================================
        self.runtime_config = RuntimeConfigStore(bootstrap=config)

        # Audit retention purge (BUG-054): throttled to once per 6h, kicked via
        # asyncio.to_thread from the run loop, fully failure-isolated. Runs
        # bounded batched deletes OUTSIDE the tick path.
        self._audit_purge_interval_sec: float = 6 * 3600.0
        self._last_audit_purge_time: float = 0.0
        # Daily Telegram performance summary (BUG-057): once per 24h.
        self._daily_summary_interval_sec: float = 24 * 3600.0
        self._last_daily_summary_time: float = 0.0
        # TASK-11: database hygiene worker cycle (low frequency, off hot path).
        # First-run posture is AUDIT_ONLY (never deletes on debut); an operator
        # opts into SAFE_CLEAN --apply via the CLI. Idle scan ~6h, deep cycle
        # ~24h — never every 60s.
        self._hygiene_interval_sec: float = 6 * 3600.0
        self._last_hygiene_time: float = 0.0
        self._hygiene_worker: DatabaseHygieneWorker | None = None
        self._hygiene_mode = "AUDIT_ONLY"
        # TASK-22: continuous runtime hygiene scheduler (config-driven cadence,
        # first-run audit, consistency/index checks, quarantine + Telegram
        # reports). Replaces the bare TASK-11 worker as the runtime driver.
        self._hygiene_scheduler: Any = None

        # TASK-13: incident response worker (background, off tick path).
        # Lazy construction in run_loop so a DB failure at startup can never
        # block trading; the worker is observability-only (INV-019).
        self._incident_interval_sec: float = 60.0
        self._last_incident_time: float = 0.0
        self._incident_worker: IncidentWorker | None = None  # lazy: IncidentWorker
        self._incident_telemetry: IncidentTelemetryCollector | None = None

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
        #: Broker-aware account snapshot cache (typed; refreshed off the hot path).
        self._account_snapshot: Any = None
        #: Cached AccountInfo between 5s refreshes (avoids a per-tick RPC).
        self._last_account_info: Any = None
        self._last_account_refresh: float = 0.0
        #: Real runtime execution mode - updated from connection state, never
        #: blindly trusted from config (task section 8: mode must be real).
        self._runtime_mode: str = ""
        # BUG-148: UI/CLI mode authority fallback (safety default for direct
        # construction paths that bypass the explicit mode_override kwarg).
        self._mode_override = self._mode_override or None

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

        # =====================================================================
        # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: LIVE-FRESHNESS TRUTH MODEL
        # ---------------------------------------------------------------------
        # Root cause (proven from telemetry 2026-08-26): ticks advance
        # (tick_age_sec ~0.5s) but features/inference/proposal timestamps were
        # FROZEN at 06:25:42 for >770s while the engine reported health=READY
        # and an ever-increasing state_version. state_version / uptime /
        # HTTP 200 are NOT proof of intelligence freshness. This block exposes
        # the real freshness of every pipeline stage and is purely
        # observational at the instrumentation site (it never blocks trading).
        # =====================================================================
        # Freshness config (BUGFIX-G29): upper bounds (seconds) beyond which a
        # stage is reported STALE. Tunable via runtime_config key
        # "freshness.max_age_sec" (default 30.0s); values are documented so QA
        # can assert on them.
        self._freshness_max_age_sec: float = float(
            (
                getattr(config, "freshness", None) is not None
                and getattr(config.freshness, "max_age_sec", 30.0)
            )
            or 30.0
        )
        # Monotonic tick timestamp: strictly increasing wall-clock-ms of the
        # most recent MARKET tick observed on the live path. Exposes that the
        # data feed itself is moving independently of feature/inference age.
        self._monotonic_tick_ms: int = 0
        self._last_tick_timestamp: datetime | None = None
        # Observed (engine-snapshot) stage timestamps - authoritative for
        # change-detection / staleness.
        self.last_feature_update: datetime | None = None
        self.last_inference_timestamp: datetime | None = None
        self.last_decision_timestamp: datetime | None = None
        self.last_successful_inference: datetime | None = None
        self.last_failed_inference: datetime | None = None
        # Monotonic sequence ids: increment only when the STAGE actually
        # re-ran on NEW substantive input (not on heartbeat). Lets the UI/QA
        # prove inference progressed without trusting timestamps alone.
        self._tick_sequence: int = 0
        self._feature_sequence: int = 0
        self._inference_sequence: int = 0
        self._decision_sequence: int = 0
        # Deterministic change-detection hashes (volatile timestamps excluded)
        # so the coordinator can prove exactly where state becomes frozen.
        self._last_raw_market_hash: str = ""
        self._last_feature_hash: str = ""
        self._last_model_input_hash: str = ""
        self._last_model_output_hash: str = ""
        # Telemetry counters (mission requirement)
        self._market_updates_total: int = 0
        self._feature_builds_total: int = 0
        self._inference_runs_total: int = 0
        self._inference_failures_total: int = 0
        self._decision_updates_total: int = 0
        self._stale_state_detected_total: int = 0
        # In-flight worker tracker for non-blocking background dispatch
        self._inflight_workers: set[str] = set()
        self._background_tasks: set[asyncio.Task[Any]] = set()

        # Buffers / engines
        symbol = config.execution.symbol
        self.aggregator = BarAggregator(symbol=symbol, timeframe_minutes=1)
        self.feature_engine = ScalpFeatureEngine(
            symbol=symbol,
            fvg_mitigation_sensitivity=config.algo.fvg_mitigation_sensitivity,
            order_block_lookback_bars=config.algo.order_block_lookback_bars,
        )

        # Module 1: Market Regime Engine (init hardening)
        self.regime_classifier = self._init_regime_classifier(symbol=symbol)

        # =================================================================
        # BUG-072: isolated user-settings architecture.
        # Telegram credentials come from the SECURE secret store (DPAPI) /
        # app_settings.db, NEVER from live.yaml (legacy values are migrated
        # then blanked). Env overrides remain the diagnosis escape hatch.
        # =================================================================
        self.settings_service = load_settings_service()
        # Attach the persistent store (settings DB) so runtime config
        # versions and values persist across restarts (boot hydration:
        # persisted values layer over the bootstrap snapshot).
        try:
            from nexus_scalp.configuration import PersistentConfigStore

            self.runtime_config.rehydrate(PersistentConfigStore(self.settings_service))
        except Exception as _pcs_err:
            logger.warning(
                "[RUNTIME_CONFIG] persistent store attach failed (non-fatal): %s",
                _pcs_err,
            )
        try:
            legacy: dict[str, Any] = {}
            legacy_path = Path("configs/live.yaml")
            if legacy_path.exists():
                import yaml as _yaml

                with open(legacy_path, encoding="utf-8") as _f:
                    legacy = _yaml.safe_load(_f) or {}
        except Exception as _leg_err:
            logger.warning("[SETTINGS] legacy scan failed (non-fatal): %s", _leg_err)
            legacy = {}

        migration = self.settings_service.migrate_legacy_yaml(legacy)
        if migration.get("migrated"):
            logger.info(
                "[SETTINGS] legacy telegram secrets migrated to secure store (correlation_id=%s)",
                migration.get("correlation_id", "-"),
            )
            self.settings_service.blank_legacy_secrets(legacy_path)

        # Env override wins for diagnosis; otherwise the secure store is
        # authoritative (never live.yaml).
        env_token = os.getenv("NEXUS_TELEGRAM_BOT_TOKEN")
        env_admin = os.getenv("NEXUS_TELEGRAM_ADMIN_ID")
        sec_token, sec_admin = self.settings_service.get_telegram_credentials()
        bot_token = env_token or sec_token or ""
        admin_id = env_admin or sec_admin or ""
        self._telegram_credential_source = "ENV" if (env_token or env_admin) else "SECURE_SETTINGS"

        # telegram.enabled default: config value until user settings override it
        cfg_enabled_row = self.settings_service.db.get("telegram.enabled")
        tg_enabled = (
            bool(cfg_enabled_row.value)
            if cfg_enabled_row and cfg_enabled_row.value is not None
            else bool(config.telegram.enabled)
        )

        # UI-controlled execution mode: the settings DB is authoritative
        # when the user changed it from the dashboard (UI == source of
        # control). Falls back to the YAML/config default otherwise so a
        # fresh install keeps its documented default.
        # BUG-148: an EXPLICIT operator mode (CLI --mode / dashboard set) is
        # the highest authority — a persisted DB value must never silently
        # override the operator's explicit start choice.
        if self._mode_override is None:
            try:
                mode_row = self.settings_service.db.get("execution.mode")
                if mode_row is not None and mode_row.value is not None:
                    persisted_mode = str(mode_row.value).strip().upper()
                    if persisted_mode in {m.value for m in ExecutionMode}:
                        self.config.execution.mode = ExecutionMode(persisted_mode)
            except Exception as _mode_err:
                logger.warning(
                    "[SETTINGS] execution.mode override failed (non-fatal): %s", _mode_err
                )
        else:
            self.config.execution.mode = self._mode_override
            logger.info(
                "[MODE] explicit operator override honored mode=%s", self._mode_override.value
            )

        # BUG-212: boot-time adapter/mode alignment (defense in depth). The
        # effective mode is now resolved (explicit override > settings DB >
        # config), so the adapter boundary can be asserted INSIDE the engine:
        # a PAPER boot must never keep a real broker adapter that any caller
        # bound. Runs BEFORE OrderLifecycleManager construction so the order
        # path is wired to the corrected boundary from the first tick.
        # NOTE: static type checkers may narrow `self.adapter` to the
        # constructor parameter type; that narrowing is re-widened here via
        # the instance attribute assignment below.
        _boot_adapter = self.align_adapter_to_boot_mode(adapter, self.config.execution.mode)
        if _boot_adapter is not self.adapter:
            self.adapter = _boot_adapter
            adapter = _boot_adapter  # keep the local used by later __init__ wiring consistent

        self.notifier = TelegramNotifier(
            bot_token=bot_token,
            admin_id=admin_id,
            enabled=tg_enabled,
        )
        logger.info(
            "[TELEGRAM_CONFIG] enabled=%s configured=%s token_present=%s "
            "admin_id_present=%s source=%s",
            self.notifier.enabled,
            bool(bot_token and admin_id),
            bool(bot_token),
            bool(admin_id),
            self._telegram_credential_source,
        )
        if config.telegram.enabled and not bot_token:
            logger.warning(
                "[TELEGRAM_CONFIG_ERROR] reason=BOT_TOKEN_MISSING "
                "(set via settings UI or NEXUS_TELEGRAM_BOT_TOKEN env)"
            )
        if config.telegram.enabled and bot_token and not admin_id:
            logger.warning("[TELEGRAM_CONFIG_ERROR] reason=ADMIN_CHAT_ID_MISSING")

        # BUG-061: local candle-intelligence subsystem (candle-close gate).
        # Isolated DB (candle_intel.db); feeds decisions for entry/hold/fast-exit.
        try:
            self.candle_intel = CandleIntelligenceEngine(CandleIntelligenceConfig(enabled=True))
        except Exception as ci_err:
            self.candle_intel = None
            logger.error("[CANDLE_INTEL] init failed (isolated)", error=str(ci_err))
        self._last_candle_decision: Any = None

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
        # ACCOUNT HISTORY: BROKER-AUTHORITATIVE HISTORY SYNC
        # ---------------------------------------------------------------------
        # Durable normalized copy of MT5 order/deal history with exact
        # deduplication (broker tickets). The sync worker is bounded,
        # throttled, watermark-based, failure-isolated and NEVER on the tick
        # path (kicked via asyncio.to_thread from the run loop).
        # =====================================================================
        from nexus_scalp.adapters.database.broker_history_sync import (
            BrokerHistorySyncWorker,
        )

        symbol_h = str(
            getattr(getattr(self.config, "execution", None), "symbol", "XAUUSD") or "XAUUSD"
        )
        self.history_sync_worker = BrokerHistorySyncWorker(
            audit=self.audit,
            adapter=adapter,
            symbol=symbol_h,
            interval_sec=300.0,
        )
        self._history_sync_started: bool = False

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
        # TASK-21: research observability facade (gates/events/evidence/snapshots).
        from nexus_scalp.research.observability import ResearchObservabilityStore

        self.research_observability = ResearchObservabilityStore(audit_repo=self.audit)
        self.research_pipeline = ResearchPipeline(
            dataset_builder=self.research_dataset_builder,
            registry=self.strategy_registry,
            observability=self.research_observability,
        )
        self.research_worker = ResearchWorker(
            audit_repo=self.audit,
            ledger=self.experience_ledger,
            pipeline=self.research_pipeline,
            interval_sec=60.0,
        )
        self._research_worker_started: bool = False

        # =====================================================================
        # STRATEGY FACTORY: AUTONOMOUS STRATEGY EVOLUTION / RESEARCH LOOP
        # ---------------------------------------------------------------------
        # Orchestrates candidate generation -> structural validation ->
        # authoritative research pipeline (backtest/WF/OOS/robustness) ->
        # ranking -> elite selection -> evolution. Runs OFF the tick path via
        # asyncio.to_thread(); persistence goes through the audit queue. It
        # NEVER places orders and NEVER promotes to ACTIVE automatically.
        # =====================================================================
        # Generated-strategy research memory lives in an ISOLATED store
        # (artifacts/strategies.db on SQLite, or PostgreSQL) — never in
        # the audit DB. The factory falls back to the audit queue only
        # when the isolated store cannot be opened.
        _strategy_store: Any = None
        try:
            from nexus_scalp.strategies.research_store import open_store

            _strategy_store = open_store()
            logger.info(
                "[STRATEGY_FACTORY] isolated research store ready",
                provider=_strategy_store.config.provider.value,
            )
        except Exception as _store_err:
            logger.warning(
                "[STRATEGY_FACTORY] isolated store unavailable, using audit queue",
                error=str(_store_err),
            )
            _strategy_store = None
        _factory_provider = self._build_factory_llm_provider()
        self.strategy_factory = StrategyFactory(
            audit_repo=self.audit,
            research_pipeline=self.research_pipeline,
            config=EvolutionConfig(),
            symbols=[str(self.config.execution.symbol or "XAUUSD")],
            notifier=getattr(self, "notifier", None),
            store=_strategy_store,
            provider=_factory_provider,
        )

        self.strategy_factory_worker = AutonomousLoopWorker(
            factory=self.strategy_factory,
            max_generations=EvolutionConfig().max_generations,
            target_elite_count=EvolutionConfig().target_elite_count,
        )
        self._factory_worker_started: bool = False

        # =====================================================================
        # PHASE 10: CONTROLLED MODEL TRAINING & CHALLENGER ENGINE
        # ---------------------------------------------------------------------
        # Trains candidate models OFFLINE from verified experience. The
        # production Champion is NEVER touched by candidate training; a
        # Challenger is validated and compared but never auto-promoted.
        # =====================================================================
        initial_art_path = Path(self.config.model.model_artifact_path)
        declared_dim = self._declared_contract_dim_for_path(initial_art_path) or self.FEATURE_DIM
        declared_schema = "scalp_v3" if declared_dim == 70 else self.FEATURE_SCHEMA_ID

        self.champion_manager = ChampionManager(
            artifact_path=str(initial_art_path),
            model_id="primary_scalp",
            model_version=str(
                getattr(
                    self.config.model,
                    "feature_schema_version",
                    "v1.0" if declared_dim != 70 else "v3.0",
                )
            ),
            feature_schema_id=declared_schema,
            feature_dimension=declared_dim,
            # BUG-243: declared head; live 70D now serves 3 (canonical).
            num_classes=self._declared_head_classes_for_path(
                initial_art_path.with_suffix(".meta.json")
            ),
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

        # =================================================================
        # TASK-6: LIVE MODEL GOVERNANCE (CHG-0003)
        # -----------------------------------------------------------------
        # Truthful registry reconciliation, the deterministic 10-gate model
        # load gate, the audited promotion/rollback lifecycle, gold-hash
        # health, and the governance shadow runtime (same-input alignment +
        # feature/news parity + latency + failure isolation). Governance is
        # observability-only: it imports no adapter / order manager / risk
        # engine and can never place, modify or close an order (INV-002/003).
        # =================================================================
        self.governance_store = GovernanceStore(audit_repo=self.audit)
        self.governance_engine = ModelGovernanceEngine(
            store=self.governance_store,
            dependency_map={
                "activate": self._activate_promoted_model,
                "rollback_activate": self._activate_rollback_model,
            },
        )
        self._governance_shadow: GovernanceShadowRuntime | None = None
        self._governance_reference_vector: list[float] | None = None
        self._governance_health_last_save: float = 0.0
        self._governance_health_save_interval_sec: float = 300.0
        # =====================================================================
        # TASK-05-70D-SHADOW: 70D LIQUIDITY SHADOW RUNTIME (OBSERVABILITY ONLY)
        # ---------------------------------------------------------------------
        # Evaluates a validated 70D candidate against the live Champion using
        # the SAME canonical market state. The 70D shadow can never place,
        # modify or cancel an order; it imports no adapter/order-manager/risk
        # engine and can never influence execution, policy or confidence
        # thresholds (INV-018). Wired lazily: no candidate => IDLE.
        # =====================================================================
        from nexus_scalp.shadow.shadow70.health import (
            Shadow70DriftMonitor,
            Shadow70FeatureHealthMonitor,
        )
        from nexus_scalp.shadow.shadow70.runtime import Shadow70Runtime
        from nexus_scalp.shadow.shadow70.store import Shadow70Store
        from nexus_scalp.shadow.shadow70.worker import Shadow70Worker

        self._shadow70_store = Shadow70Store(audit_repo=self.audit)
        self._shadow70_runtime = Shadow70Runtime()
        self._shadow70_health = Shadow70FeatureHealthMonitor(window=1000)
        self._shadow70_drift = Shadow70DriftMonitor()
        self._shadow70_worker = Shadow70Worker(store=self._shadow70_store, max_queue=2000)
        self._shadow70_worker_started: bool = False
        self._shadow70_enabled: bool = False  # enabled by operator via API attach

        # =====================================================================
        # PHASE 12: NEWS INTELLIGENCE ENGINE (isolated, optional)
        # ---------------------------------------------------------------------
        # Dedicated news.db; worker via asyncio.to_thread; news gate applies a
        # BOUNDED confidence adjustment only. News can never place/modify/close
        # an order and can never override risk/exposure/kill-switch. If the
        # news subsystem fails to construct, trading continues unaffected.
        # =====================================================================
        self._news_enabled: bool = bool(getattr(config, "news", None) and config.news.enabled)
        self.news_engine: Any | None = None
        self.news_worker: Any | None = None
        self.news_gate: Any | None = None
        self._news_worker_started: bool = False
        # ---------------------------------------------------------------------
        # TASK-02-70D-INTEGRATION: Liquidity Intelligence governor (info-only).
        # Produces the 70D liquidity snapshot/status for API + UI + candidate
        # pipelines. NEVER touches orders/SL/TP/risk/execution (brief 21).
        # =====================================================================
        liq_cfg = getattr(config, "model", None)
        liq_enabled = (
            bool(getattr(liq_cfg, "liquidity_features_enabled", False)) if liq_cfg else False
        )
        # NOTE: load_settings_service is imported at module level (line ~100);
        # a function-local import here would shadow it for the earlier
        # `self.settings_service = load_settings_service()` call (UnboundLocalError).
        try:
            liq_svc = load_settings_service()
            row = liq_svc.db.get("model.liquidity_features_enabled")
            if row is not None:
                liq_enabled = bool(row.value)
        except Exception:
            pass  # settings DB absent -> keep config default, never crash boot
        self.liquidity_governor = LiquidityGovernor(enabled=liq_enabled, settings_service=liq_svc)
        self.liquidity_governor.bind_engine(self)
        # ---------------------------------------------------------------------
        # MSLIE (Market Structure & Liquidity Intelligence Engine): market
        # PERCEPTION layer. Consumes the same completed bars the feature
        # engine uses and produces the MarketIntelligenceFeatureVectorV1
        # (regime, swing structure, liquidity map, sweep events, breakout
        # quality, smart money) for AI models / debug UI. PURE perception:
        # no adapter, no order manager, no risk engine (INV-002), no DB on
        # the tick path (INV-001), strict causality (INV-008). Never alters
        # the live 50D/70D feature contract (INV-009).
        # =====================================================================
        try:
            from nexus_scalp.mslie import MarketStructureEngine

            exec_cfg = getattr(config, "execution", None)
            self.mslie_engine = MarketStructureEngine(
                symbol=getattr(exec_cfg, "symbol", "XAUUSD") or "XAUUSD",
                timeframe="M1",
            )
        except Exception as ms_exc:
            logger.warning(
                "[MSLIE] event=CONSTRUCT_FAILED error=%s (perception layer disabled; trading unaffected)",
                ms_exc,
            )
            self.mslie_engine = None
        self._last_mslie_vector: Any | None = None
        self._last_news_gate: Any | None = None
        # News enabled is AUTHORITATIVE from the runtime snapshot (persisted
        # toggle), not from the bootstrap yaml alone — so a restart respects
        # the operator's UI choice. LiveEngine bootstraps from config then
        # rehydrates; override with the snapshot truth if present.
        self._news_enabled = bool(getattr(config, "news", None) and config.news.enabled)
        self._news_auto_analysis_enabled = bool(
            getattr(getattr(config, "news", None), "auto_analysis_enabled", False)
        )
        with contextlib.suppress(Exception):
            _news_snap = self.runtime_config.get_snapshot().news
            self._news_enabled = bool(_news_snap.enabled)
            self._news_auto_analysis_enabled = bool(
                getattr(_news_snap, "auto_analysis_enabled", False)
            )
        if self._news_enabled:
            try:
                from nexus_scalp.news import NewsEngine, NewsGate, NewsWorker

                news_config = config.news
                self.news_engine = NewsEngine(config=news_config)
                self.news_worker = NewsWorker(
                    engine=self.news_engine,
                    interval_sec=float(getattr(news_config, "worker_interval_sec", 60)),
                    max_queue=int(getattr(news_config, "max_queue_size", 1000)),
                )
                # News Auto Analysis — seed worker gate from snapshot/bootstrap (no API key needed)
                with contextlib.suppress(Exception):
                    self.news_worker.auto_analysis_enabled = bool(
                        getattr(self, "_news_auto_analysis_enabled", False)
                    )
                self.news_gate = NewsGate(config=news_config)
                logger.info("[NEWS] event=CONSTRUCTED status=ENABLED")
            except Exception as news_err:
                self._news_enabled = False
                self.news_engine = None
                self.news_worker = None
                self.news_gate = None
                logger.error(
                    "[NEWS] event=CONSTRUCT_FAILED status=DISABLED (trading unaffected)",
                    error=str(news_err),
                )

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
            # TASK-3 (BUG-086): the close path finalizes the immutable
            # position timeline (POSITION_EXITED) with canonical realized
            # PnL / R / exit mechanism.
            lifecycle_tracker=self.intelligence_lifecycle,
        )
        # BUG-226: seed the audit-stream provenance from the effective boot
        # mode; the accounting layer filters PAPER-tagged rows out of metrics.
        self.audit.current_account_source = self._boot_account_source

        # Online training toolchain
        # P0-2026-09-04: the online trainer must NEVER own the champion
        # serving path as its save target — fine_tune_online persists through
        # the engine's _save_model_weights_atomic on the BUNDLE's artifact
        # path (persist-decision gated, BUG-235/236). The trainer's own save
        # path is therefore an isolated candidate location; the serving path
        # stays untouched by trainer defaults.
        self.trainer = WalkForwardTrainer(
            artifact_save_path=Path(
                "artifacts/model_generation/models/online_buffer/online_model.pt"
            ),
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
        # FIX #1+#8: live sequence deque declared+initialized in the class
        # header (see _live_sequence_defaults above); _rebind_live_temporal_contract
        # already ran during __init__ earlier (before bundle load ordering).
        self._retrain_interval_bars: int = 50
        self._bars_since_last_retrain: int = 0
        self._retrain_task: asyncio.Task | None = None
        self._retrain_inflight: bool = False
        # BUG-169: throttle timestamp for the width-mismatch warning (set on first use).
        self._online_train_width_warn_at: float = 0.0

        # Hedging tracker to avoid spamming multiple limit orders per ticket
        self._hedged_tickets: set[int] = set()

        # Web / UI Synchronization states to act as single source of truth
        self._last_tick: TickData | None = None
        self._last_fv: FeatureVector | None = None
        # Market Radar (Hunter SetupDetector) - live, bar-close cadence (BUG-138 fix).
        self.setup_detector = SetupDetector()
        self._last_market_radar: dict[str, Any] | None = None
        self._last_model_input_tensor: list[float] | None = None
        self._last_regime_state: MarketRegimeState | None = None
        self._last_probs: torch.Tensor | None = None
        self._last_proposal: TradeProposal | None = None
        self._last_inference_latency_ms: float | None = None
        # TASK latency forensics: honest staged breakdown (model/feature/e2e).
        self._last_latency_breakdown: dict | None = None
        self._last_model_forward_ms: float | None = None
        self._last_feature_ms: float | None = None
        self._last_e2e_ms: float | None = None
        # OBS-PERF-RESILIENCE: rolling latency regression detector — bounded
        # in-memory p95 window over the staged latency breakdown with an
        # edge-triggered regression alert (never blocks, never raises).
        self._latency_regression: Any | None = None
        self._inference_count: int = 0
        #: Most recent Phase 08 pre-trade verdict, surfaced by the REST API.
        self._last_experience_decision: PreTradeExperienceDecision | None = None
        # Chart/UI snapshot cache: the SMC overlays + 900-bar payload are
        # recomputed ONLY when a bar completes (or the first tick after
        # construction). Between ticks the completed-bar series cannot change,
        # so re-running the O(n) extraction on every tick is pure waste
        # (measured ~6-7ms/tick at 900 bars vs ~0 for the cached path).
        self._last_chart_snapshot_key: object = None
        self._last_chart_snapshot_bars: list[dict[str, Any]] | None = None
        self._last_chart_snapshot_overlays: dict[str, Any] | None = None
        self._last_chart_snapshot_time: float = 0.0

        # Preload model/scaler bundle (pre-flight).
        # BUG-136: honor the REHYDRATED runtime snapshot model_artifact_path
        # (persisted via hot-swap / runtime-config apply) at boot; fall back
        # to the bootstrap default only when no persisted value exists.
        # Without this, a restart reverts to the 50D default bundle while the
        # persistent store expects 70D -> false MODEL_INPUT_DIMENSION_MISMATCH.
        model_path_str = self.config.model.model_artifact_path
        with contextlib.suppress(Exception):
            _md_snap = self.runtime_config.get_snapshot().model.model_artifact_path
            if _md_snap:
                model_path_str = str(_md_snap)
        model_path = Path(model_path_str)
        self._bundle = self._load_or_create_bundle(
            model_path=model_path, force_fresh=self.force_fresh_model
        )

        # BUG-182B: this rebind MUST run AFTER _load_or_create_bundle below,
        # it reads self._bundle; in the old position (before the load) the
        # bundle was still None, the rebind silently skipped and every online
        # fine-tune fed 50D records into the 70-input head (43 matmul crashes
        # on 2026-09-01).
        # BUG-169 (2026-08-31 live forensics): the trainer was bound to the
        # CLASS bootstrap contract (scalp_v1/50D) while the LOADED artifact
        # is the 70D champion — every online fine-tune fed a (N,50) matrix
        # into a 70-input Linear head and crashed with
        # "mat1 and mat2 shapes cannot be multiplied (10x50 and 70x128)"
        # (60 failures on 2026-08-31 alone; each attempt also hit the
        # WalkForwardTrainer scaler save while the engine held the artifact,
        # logging WinError 5). Bind the trainer to the EFFECTIVE contract of
        # the loaded bundle instead. The rolling buffer records are rebuilt
        # on the same effective contract (records are written from the
        # validated live tensor), so frame validation stays consistent.
        # This is a NO-OP while the 50D champion is loaded.
        # BUG-185: shared rebind helper - hot-swap/promotion/rollback can
        # also change the serving contract, so the trainer must rebind on
        # every bundle mutation, not only at boot.
        self._rebind_trainer_to_bundle()

        # PHASE 08: register the model that is actually serving live inference.
        # This is metadata only - the experience ledger constructed above is
        # already fully usable even when this artifact was just created fresh.
        self._register_active_model(model_path=model_path, replaced=False)
        # TASK-6: make the registry truthful about CURRENT_CHAMPION.
        with contextlib.suppress(Exception):
            self._sync_champion_registry_state()

    def _build_factory_llm_provider(self) -> Any | None:
        """Builds the (optional) Strategy Factory LLM provider from settings.

        The API key is read from the OS-protected secret store (DPAPI on
        Windows); base URL + model + temperature come from the settings DB.
        Any failure -> None: the factory then uses the deterministic
        generators (the LLM is an assisted source, never a requirement).
        """
        try:
            from nexus_scalp.strategies.factory.provider import LLMGenerationProvider

            svc = getattr(self, "settings_service", None)
            if svc is None:
                return None
            cfg = svc.get_factory_llm_config()
            if not cfg.get("api_key") or not cfg.get("api_base_url") or not cfg.get("model"):
                return None
            # CHG-0034: honor user intent + runtime auto-disable (INV-024:
            # additive guard only — this method is on the factory build path,
            # NEVER the trading tick path). A disabled feature builds no
            # provider -> deterministic generators are used automatically.
            try:
                if not svc.factory_effective_enabled():
                    logger.info(
                        "[STRATEGY_FACTORY] provider build skipped (effective_enabled=false)"
                    )
                    return None
            except AttributeError:
                pass  # older settings service without CHG-0034 API
            return LLMGenerationProvider(
                api_base_url=cfg["api_base_url"],
                model=cfg["model"],
                api_key=cfg["api_key"],
                temperature=cfg.get("temperature", 0.7),
                secret_store=svc.secrets,
                request_timeout_sec=cfg.get("request_timeout_sec", 300.0),
                max_requests_per_generation=cfg.get("max_requests_per_generation", 60),
            )
        except Exception as e:
            logger.warning(
                "[STRATEGY_FACTORY] LLM provider build failed (deterministic fallback)",
                error=str(e),
            )
            return None

    def _rebuild_factory_llm_provider(self) -> None:
        """Hot-swaps the running factory provider after a web settings save."""
        try:
            if self.strategy_factory is None:
                return
            self.strategy_factory.provider = self._build_factory_llm_provider()
            logger.info(
                "[STRATEGY_FACTORY] factory provider hot-rebuilt",
            )
        except Exception as e:
            logger.warning("[STRATEGY_FACTORY] provider hot-rebuild failed", error=str(e))

    def _register_active_model(self, model_path: Path, replaced: bool) -> None:
        """
        Stamps the active model identity onto future experiences.

        BUG-125: the advertised schema/dimension are taken from the
        authoritative bundle when present, not from the class default.
        """
        try:
            eff_id = str(self.effective_feature_schema_id)
            eff_dim = int(self.effective_feature_dim)
            provenance = self.model_registry.register_model(
                artifact_path=model_path,
                model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                feature_schema_id=eff_id,
                feature_dimension=eff_dim,
                config_version=str(getattr(self.runtime_config, "get_version", lambda: 0)()),
                replaced=replaced,
            )
            self.experience_engine.set_provenance(provenance)
        except Exception as e:
            logger.error("[MODEL] provenance registration failed (isolated)", error=str(e))

    async def hot_swap_model(self, new_artifact_path: str, *, source: str = "WEB_UI") -> dict:
        """Atomically swap the serving model artifact (safe hot swap).

        Loads + validates + warms the NEW bundle FIRST; only on success the
        current bundle is released and the new one becomes authoritative.
        In-flight inference completes against the old bundle under the
        bundle lock. Never replaces a healthy model with an invalid artifact.

        P0-2026-09-04 SECURITY HARDENING (hot-swap governance):
          * path allow-list — the swap target must resolve inside the
            approved artifact roots (traversal / symlink escape / arbitrary
            external files rejected before any load);
          * bundle coherence — a manifest.json next to the artifact is
            verified against the actual bytes when present (stale sidecar /
            hash mismatch rejected);
          * safe load — weights_only state_dict deserialization with
            declared-width verification (no arbitrary pickle objects);
          * metadata/head coherence — model.meta.json class count must equal
            the actual tensor head; a rejected candidate (REJECTED lifecycle
            or production_eligible=False when the field exists) cannot be
            activated through the swap path.
        """
        from nexus_scalp.training.safe_loader import load_state_dict_safe

        new_path = Path(new_artifact_path)
        old_path = Path(self.config.model.model_artifact_path)
        if not new_path.exists():
            logger.error(
                "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_FAILED reason=ARTIFACT_MISSING path=%s",
                new_artifact_path,
            )
            return {
                "success": False,
                "reason": "ARTIFACT_MISSING",
                "runtime_applied": False,
            }
        # P0 hardening: allow-list BEFORE any load (never trust the caller).
        try:
            from nexus_scalp.training.champion_guard import resolve_under

            resolved = resolve_under(new_path)
        except Exception as path_err:
            logger.error(
                "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_FAILED reason=PATH_REJECTED detail=%s",
                path_err,
            )
            return {
                "success": False,
                "reason": "PATH_REJECTED",
                "detail": str(path_err),
                "runtime_applied": False,
            }
        del resolved
        try:
            # Governance coherence: verify the bundle manifest when present.
            manifest_path = new_path.parent / "manifest.json"
            meta_path = new_path.with_suffix(".meta.json")
            if manifest_path.exists():
                import hashlib
                import json as _json

                manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
                h = hashlib.sha256(new_path.read_bytes()).hexdigest()
                if str(manifest.get("model_sha256", "")) and h != manifest["model_sha256"]:
                    return {
                        "success": False,
                        "reason": "BUNDLE_HASH_MISMATCH",
                        "runtime_applied": False,
                    }
                if manifest.get("production_eligible") is False:
                    return {
                        "success": False,
                        "reason": "CANDIDATE_NOT_PRODUCTION_ELIGIBLE",
                        "runtime_applied": False,
                    }
            if meta_path.exists():
                import json as _json

                meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("production_eligible") is False:
                    return {
                        "success": False,
                        "reason": "CANDIDATE_NOT_PRODUCTION_ELIGIBLE",
                        "runtime_applied": False,
                    }
            # AGENT-10 (TASK-AGENT10-MODEL-PIPELINE): metadata/tensor
            # coherence + schema-identity gate BEFORE any attach. The P0
            # docstring always claimed head==meta class coherence; the check
            # is now real: artifact head vs meta num_classes, artifact width
            # vs meta declared dimension, and the meta schema id must be
            # REGISTERED (dimension equality alone is not identity).
            coherence = self._artifact_meta_coherence(new_path)
            if not coherence["ok"]:
                logger.error(
                    "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_FAILED reason=%s detail=%s",
                    coherence["reason"],
                    coherence,
                )
                return {
                    "success": False,
                    "reason": coherence["reason"],
                    "detail": coherence,
                    "runtime_applied": False,
                }
            # Safe deserialization + declared-width verification (dimension
            # gate: the artifact's own declared contract, as before).
            expected_dim = self._expected_num_features_for_artifact(new_path)

            def _safe_state():
                return load_state_dict_safe(
                    new_path, expected_input_dim=expected_dim, check_approved_root=False
                )

            await asyncio.to_thread(_safe_state)
            # Load + validate the NEW bundle in isolation (never touching
            # the serving bundle). _load_or_create_bundle raises on dimension
            # mismatch and quarantines corrupt checkpoints.
            new_bundle = await asyncio.to_thread(
                self._load_or_create_bundle, model_path=new_path, force_fresh=False
            )

            def _warmup_and_hash():
                # Warm-up: one forward pass validates the artifact end-to-end.
                import hashlib

                import numpy as np
                import torch

                warm = np.zeros((1, int(new_bundle.model.num_features)), dtype=np.float32)
                warm = new_bundle.scaler.transform(warm)
                with torch.inference_mode():
                    new_bundle.model(torch.tensor(warm, dtype=torch.float32))

                # Compute artifact hash for traceability (model version/hash)
                h = hashlib.sha256()
                with open(new_path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                return h.hexdigest()[:16]

            artifact_hash = await asyncio.to_thread(_warmup_and_hash)

            # ATOMIC SWAP under the bundle lock: new bundle replaces old.
            with self._bundle_lock:
                self._bundle = new_bundle
            # BUG-185: the new artifact may declare a different contract
            # width - rebind the online trainer before anything retrains.
            self._rebind_trainer_to_bundle()
            self.config.model.model_artifact_path = new_artifact_path
            self._register_active_model(model_path=new_path, replaced=True)
            # Surface model version/hash on the runtime snapshot
            self.runtime_config.apply(
                {"model.model_artifact_path": new_artifact_path},
                source=f"MODEL_HOT_SWAP::{source}",
            )
            logger.info(
                "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_COMPLETED source=%s "
                "artifact_hash=%s old=%s new=%s",
                source,
                artifact_hash,
                old_path.name,
                new_path.name,
            )
            return {
                "success": True,
                "runtime_applied": True,
                "artifact_hash": artifact_hash,
                "artifact_path": new_artifact_path,
                "configuration_version": self.runtime_config.get_version(),
            }
        except Exception as exc:
            logger.error(
                "[MODEL_HOT_SWAP] event=MODEL_HOT_SWAP_FAILED source=%s error=%s",
                source,
                exc,
            )
            return {
                "success": False,
                "reason": str(exc),
                "runtime_applied": False,
                "current_model_unchanged": True,
            }

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
        configure_logging(
            log_level="INFO",
            json_format=False,
            log_to_file=True,
            log_file_path=Path("logs"),
        )
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
            with contextlib.suppress(Exception):
                self.notifier.notify_error(
                    "Engine Startup Pre-Flight", f"Startup pre-flight failed: {e}"
                )
                self.notifier.shutdown(timeout=2.0)
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
            with contextlib.suppress(Exception):
                self.notifier.notify_error(
                    "Engine Run-Loop Fatal", f"Unhandled critical exception: {e}"
                )
            raise

        finally:
            with contextlib.suppress(Exception):
                if loop is not None and not loop.is_closed():
                    loop.run_until_complete(self._shutdown_async())
                    loop.close()

    def _activate_promoted_model(self, *, model_id: str, model_version: str) -> None:
        """Operator-approved runtime activation for a promoted model.

        Called by ModelGovernanceEngine.promote() AFTER the audited
        APPROVED -> CHAMPION transition is recorded. This is the ONLY place a
        model can become the live Champion, and it requires the operator
        approval token end-to-end (spec 21 / 24). Activation swaps the model
        bundle atomically under _bundle_lock. A failure propagates so the
        promotion is recorded as blocked (evidence preserved).
        """
        if not model_id:
            raise RuntimeError("activation requires a model identity")
        model_path = Path(self.config.model.model_artifact_path)
        if not model_path.exists():
            raise RuntimeError(f"activation target artifact missing: {model_path}")
        with self._bundle_lock:
            bundle = self._load_or_create_bundle(model_path=model_path, force_fresh=False)
            self._bundle = bundle
        self._rebind_trainer_to_bundle()  # BUG-185: width may have changed
        self._register_active_model(model_path=model_path, replaced=True)
        logger.info(
            "[MODEL_GOVERNANCE] event=PROMOTION_EXECUTED",
            model_id=model_id,
            model_version=model_version,
            artifact=str(model_path),
        )

    def _activate_rollback_model(self, *, model_id: str, model_version: str) -> None:
        """Rolls the runtime pointer back to the previous Champion (spec 23).

        The previous artifact must already be staged at the configured model
        path; the hash is verified against the registry event evidence by the
        rollback API gate before this point. Evidence about the FAILED model
        is preserved in the governance event ledger — never deleted.
        """
        model_path = Path(self.config.model.model_artifact_path)
        if not model_path.exists():
            raise RuntimeError(f"rollback target artifact missing: {model_path}")
        with self._bundle_lock:
            bundle = self._load_or_create_bundle(model_path=model_path, force_fresh=False)
            self._bundle = bundle
        self._rebind_trainer_to_bundle()  # BUG-185: width may have changed
        self._register_active_model(model_path=model_path, replaced=True)
        logger.info(
            "[MODEL_GOVERNANCE] event=ROLLBACK_EXECUTED",
            restored=f"{model_id}@{model_version}",
            artifact=str(model_path),
        )

    def _champion_bundle_healthy(self) -> bool:
        """Post-activation smoke: the current model bundle must load and be
        healthy (spec 9 pre-promotion smoke + spec 10 post-promotion health).

        READ-ONLY: no order, no broker mutation.
        """
        try:
            champ = self.champion_manager.champion_or_none()
            if champ is None or not champ.available:
                return False
            if self._bundle is None:
                return False
            return True
        except Exception:
            return False

    def _governance_snapshot_health(self) -> dict[str, Any]:
        """Truthful runtime health for the governance layer (spec 27)."""
        champ: dict[str, Any] = {}
        try:
            c = self.champion_manager.champion_or_none()
            if c is not None:
                champ = {
                    "id": c.model_id,
                    "version": c.model_version,
                    "schema": c.feature_schema_id,
                    "healthy": c.available,
                    "artifact_hash": c.artifact_hash,
                }
        except Exception:
            champ = {"id": self.champion_manager.model_id, "healthy": False}
        chal: dict[str, Any] = {"state": "NONE", "id": "", "version": "", "schema": ""}
        if self._governance_shadow is not None:
            s = self._governance_shadow.summary()
            chal = {
                "id": s.get("model_id", ""),
                "version": s.get("model_version", ""),
                "schema": s.get("schema_id", ""),
                "state": "SHADOW",
            }
        shad: dict[str, Any] = {
            "running": self._governance_shadow is not None
            and bool(self.shadow_engine.active_run_id),
            "comparisons": self._governance_shadow.comparisons if self._governance_shadow else 0,
            "errors": self._governance_shadow.errors if self._governance_shadow else 0,
            "dropped": self._governance_shadow.dropped if self._governance_shadow else 0,
            "last_update": "",
        }
        return self.governance_engine.health(champion=champ, challenger=chal, shadow=shad)

    def _save_governance_health_periodic(self) -> None:
        """Bounded model_runtime_health snapshot (~5 min, queued, isolated)."""
        try:
            if self.governance_store is None:
                return
            if (
                time.time() - self._governance_health_last_save
                < self._governance_health_save_interval_sec
            ):
                return
            self._governance_health_last_save = time.time()
            health = self._governance_snapshot_health()
            self.governance_store.save_health(
                {
                    "checked_at": health.get("checked_at", ""),
                    "champion_id": health["champion"].get("id", ""),
                    "champion_version": health["champion"].get("version", ""),
                    "champion_schema": health["champion"].get("schema", ""),
                    "champion_healthy": health["champion"].get("healthy", False),
                    "challenger_id": health["challenger"].get("id", ""),
                    "challenger_version": health["challenger"].get("version", ""),
                    "challenger_state": health["challenger"].get("state", "NONE"),
                    "shadow_running": health["shadow"].get("running", False),
                    "shadow_comparisons": health["shadow"].get("comparisons", 0),
                    "shadow_errors": health["shadow"].get("errors", 0),
                    "shadow_dropped": health["shadow"].get("dropped", 0),
                    "last_update": health["shadow"].get("last_update", ""),
                    "payload": health,
                }
            )
        except Exception as e:
            logger.debug("[MODEL_GOVERNANCE] health snapshot skipped (isolated)", error=str(e))

    # ------------------------------------------------------------------
    # AGENT-3 (TASK-AGENT3-MODEL-GOV): pure decision core of the champion
    # registry truthfulness sync, extracted so the sync contract is
    # unit-testable without a running engine. The legacy check compared
    # ONLY the artifact path, so a foreign CHAMPION row registered over
    # the serving path with a contradictory schema/dimension (the stale
    # t70d_v1_full row: scalp_v3/70D vs the runtime's declared contract)
    # was silently left claiming production authority. The sync now
    # verifies the FULL contract triple (path + schema + dimension) and
    # DEMOTES mismatched champion rows to ARCHIVED (never deletes: BUG
    # ledger rule 45 — history preserved) before re-stamping the
    # truthful live row. This is a registry-truth repair, NOT a model
    # promotion: no artifact is written, no gate is bypassed (INV-015).
    # ------------------------------------------------------------------

    @staticmethod
    def _evaluate_champion_registry_sync(
        _self: LiveEngine | None,
        *,
        current_row: dict[str, Any] | None,
        serving_artifact_path: str,
        serving_schema_id: str,
        serving_dimension: int,
        serving_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Pure decision for _sync_champion_registry_state (no I/O).

        Returns {"action": NOOP|REPAIR|BOOTSTRAP, ...} describing exactly
        what the caller must do to make the registry truthful.
        """
        norm = lambda p: str(p or "").replace("\\\\", "/")  # noqa: E731
        serving_path = norm(serving_artifact_path)
        model_id = f"primary_scalp_{serving_schema_id}_{serving_dimension}d"
        base: dict[str, Any] = {
            "serving_path": serving_path,
            "serving_schema_id": serving_schema_id,
            "serving_dimension": int(serving_dimension),
            "new_champion_model_id": model_id,
        }
        if current_row is None:
            return {**base, "action": "BOOTSTRAP", "reason": "no champion row"}

        row_path = norm(current_row.get("artifact_path", ""))
        row_schema = str(current_row.get("feature_schema_id", "") or "")
        row_dim = int(current_row.get("feature_dimension", 0) or 0)
        contract_match = (
            row_path == serving_path
            and row_schema == serving_schema_id
            and row_dim == int(serving_dimension)
        )
        if contract_match:
            return {**base, "action": "NOOP", "reason": "already_truthful"}

        # Path matches but the CONTRACT is contradictory: the row claims
        # the serving artifact under the wrong schema/dimension. Repair =
        # demote the stale row to ARCHIVED and re-register truthfully.
        stale_model_id = str(current_row.get("model_id", "") or "")
        return {
            **base,
            "action": "REPAIR",
            "reason": "champion_row_contract_mismatch",
            "stale_row_model_id": stale_model_id,
            "stale_row_schema": row_schema,
            "stale_row_dimension": row_dim,
            "demote_stale_to": ModelStatus.ARCHIVED.value,
        }

    def _sync_champion_registry_state(self) -> None:
        """Makes the registry truthful about the CURRENT Champion (spec 3)."""
        try:
            if self.governance_store is None:
                return
            champ = self.champion_manager.champion_or_none()
            if champ is None or not champ.artifact_hash:
                return
            from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry

            lifecycle = ModelLifecycleRegistry(
                audit_repo=self.audit, model_registry=self.model_registry
            )
            rows = lifecycle.list_models(status=ModelStatus.CHAMPION, limit=5)
            current = rows[0] if rows else None
            decision = self._evaluate_champion_registry_sync(
                None,
                current_row=current,
                serving_artifact_path=self.config.model.model_artifact_path,
                serving_schema_id=self.FEATURE_SCHEMA_ID,
                serving_dimension=self.FEATURE_DIM,
                serving_fingerprint=champ.artifact_hash,
            )
            action = decision.get("action")
            if action == "NOOP":
                return
            if action == "REPAIR":
                # Demote the contradictory CHAMPION row first (append-only:
                # ARCHIVED preserves history, never deletes evidence).
                stale_model_id = str(decision.get("stale_row_model_id", "") or "")
                stale_version = str(current.get("model_version", "") or "") if current else ""
                if stale_model_id and stale_version:
                    with contextlib.suppress(Exception):
                        lifecycle.set_status(
                            model_id=stale_model_id,
                            model_version=stale_version,
                            status=ModelStatus.ARCHIVED,
                            reason=(
                                "AGENT-3 registry truth repair: champion row "
                                "contract mismatch (declared "
                                f"{decision.get('stale_row_schema')}"
                                f"@{decision.get('stale_row_dimension')}D vs "
                                f"serving {self.FEATURE_SCHEMA_ID}"
                                f"@{self.FEATURE_DIM}D)"
                            ),
                        )
            self.model_registry.register_model(
                artifact_path=self.config.model.model_artifact_path,
                model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                feature_schema_id=self.FEATURE_SCHEMA_ID,
                feature_dimension=self.FEATURE_DIM,
                config_version=str(getattr(self.runtime_config, "get_version", lambda: 0)()),
                replaced=False,
            )
            iid = f"{self.model_registry.current.model_role.lower()}_{self.FEATURE_SCHEMA_ID}_{self.FEATURE_DIM}d"
            try:
                lifecycle.set_status(
                    model_id=iid,
                    model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                    status=ModelStatus.CHAMPION,
                    reason="registry truthfulness sync: live Champion row",
                )
            except Exception as e:
                logger.error("[MODEL_GOVERNANCE] champion registry sync failed", error=str(e))
            self.governance_store.record_event(
                GovernanceEvent(
                    event_id=f"ev_{uuid.uuid4().hex[:16]}",
                    event="REGISTRY_RECONCILED",
                    stage=GovernanceStage.REGISTRY,
                    model_id=self.champion_manager.model_id,
                    model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                    schema_id=self.FEATURE_SCHEMA_ID,
                    reason="live Champion registry truthfulness correction",
                    payload={"artifact_path": self.config.model.model_artifact_path},
                )
            )
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] registry sync failed (isolated)", error=str(e))

    async def stop(self) -> None:
        self._running = False

    async def run_loop(self) -> None:
        """
        Main tick ingestion loop.
        """
        # Resilient MT5 startup connect: the adapter itself retries
        # initialize() (bounded, backoff); the loop adds up to 3 OUTER attempts
        # so a transient IPC timeout (-10005) while the terminal is still
        # launching never kills the engine at boot.
        # Every attempt is surfaced to the console + Telegram so the operator
        # SEES the retry in progress (perfect-UI-UX requirement).
        import time as _time  # noqa: F401 - reserved for backoff timing telemetry

        mt5_connected = False
        for attempt in range(1, 4):
            logger.info(
                "[MT5_CONNECT] event=ATTEMPT attempt=%s/3 msg=connecting_to_terminal",
                attempt,
            )
            try:
                mt5_connected = self.adapter.connect()
            except Exception as conn_err:
                logger.warning(
                    "[MT5_CONNECT] event=EXCEPTION attempt=%s/3 msg=connect_raised error=%s",
                    attempt,
                    str(conn_err),
                )
                mt5_connected = False
            if mt5_connected:
                break
            if attempt < 3:
                wait_s = 1.5 * attempt
                logger.warning(
                    "[MT5_CONNECT] event=RETRY_ENGINE attempt=%s/3 msg=terminal_unavailable "
                    "wait_s=%s — retrying...",
                    attempt,
                    wait_s,
                )
                await asyncio.sleep(wait_s)

        if not mt5_connected:
            logger.critical("MT5 connect() failed after 3 attempts. Engine shutting down.")
            self.emit_incident_telemetry(
                event_type="MT5_CONNECT_FAILED",
                component="mt5",
                severity="HIGH",
                correlation_id="startup",
            )
            with contextlib.suppress(Exception):
                self.notifier.notify_error(
                    "MT5 Connectivity",
                    "MT5 connect() failed after retries. Engine shutting down.",
                )
            return

        self._running = True
        symbol = self.config.execution.symbol

        account = self.adapter.get_account_info()
        self._symbol_info = self.adapter.get_symbol_info(symbol)

        # PHASE 14: refresh the typed broker-aware account snapshot and derive
        # the REAL runtime mode from connection state + account permissions.
        try:
            self._account_snapshot = self.adapter.get_account_snapshot()
        except Exception:
            self._account_snapshot = None
        self._update_runtime_mode()

        self._restore_peak_equity(account)
        self._notify_startup(account)

        # BUG-072/073 restart safety: reconcile internal pending/position
        # state against broker truth at startup. The broker wins — a stale
        # internal pending (or a broker order the engine never tracked) is
        # repaired before any new entry can be considered. Isolated.
        try:
            om = self.order_manager
            if om is None:
                raise RuntimeError("order_manager not constructed yet (startup ordering)")
            rep = om.reconcile_pending_state(
                symbol=symbol, current_tick=self.adapter.get_last_tick(symbol)
            )
            logger.info(
                "[EXECUTION_RECONCILIATION] event=STARTUP "
                "pending_internal=%s pending_broker=%s mismatch=%s repaired=%s",
                rep["pending_internal"],
                rep["pending_broker"],
                rep["mismatch"],
                rep["repaired"],
            )
        except Exception as startup_rec_err:
            logger.error(
                "[EXECUTION_RECONCILIATION] event=STARTUP_FAILED (isolated)",
                error=str(startup_rec_err),
            )
            self.emit_incident_telemetry(
                event_type="EXECUTION_RECONCILIATION_FAILED",
                component="execution",
                severity="HIGH",
                correlation_id="startup",
            )

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

        # ACCOUNT HISTORY: start the bounded broker-history sync worker
        # (watermark + overlap, idempotent, kicked via to_thread).
        self._start_history_sync_worker()

        # PHASE 09: start the background intelligence worker. Fully isolated:
        # a failure inside it can never stop trading.
        self._start_intelligence_worker()

        # PHASE 09B: start the background strategy research worker. Research is
        # OFFLINE / BACKGROUND (dataset rebuild, discovery, validation gates).
        # Fully isolated: it can never stop trading and never places orders.
        self._start_research_worker()
        self._start_factory_worker()

        # PHASE 10: start the controlled training worker. Heavy training runs
        # ONLY in worker threads, never in the tick pipeline; fully isolated.
        self._start_training_worker()

        # PHASE 11: start the shadow-aggregation worker. Shadow evaluation is
        # bounded + isolated; it can never stop trading or touch orders.
        self._start_shadow_worker()

        # PHASE 12: start the news intelligence worker (isolated, optional).
        self._start_news_worker()

        logger.info(
            "LIVE CONNECTED",
            login=getattr(account, "login", 0) if account else 0,
            balance=getattr(account, "balance", 0.0) if account else 0.0,
            equity=getattr(account, "equity", 0.0) if account else 0.0,
            symbol=symbol,
            digits=self._symbol_info.digits if self._symbol_info else 2,
            model_path=str(self.config.model.model_artifact_path),
        )

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
                        self.emit_incident_telemetry(
                            event_type="MT5_DISCONNECTED",
                            component="mt5",
                            severity="HIGH",
                            correlation_id="tick-stream",
                        )
                        try:
                            self.adapter.disconnect()
                            await asyncio.sleep(1.0)
                            self.adapter.connect()
                            # RESYNC (BUG-054): after a reconnect the broker may
                            # have advanced 5-6h; reseed the aggregator from
                            # broker history so the chart/features/regime all
                            # rebuild from real candles instead of the stale
                            # pre-disconnect series.
                            try:
                                await self._resync_from_broker(symbol)
                            except Exception as resync_err:
                                logger.error(
                                    "Watchdog reconnect resync failed",
                                    error=str(resync_err),
                                    exc_info=True,
                                )
                        except Exception as conn_err:
                            logger.error(
                                "Error during auto-reconnect in watchdog",
                                error=str(conn_err),
                                exc_info=True,
                            )
                    else:
                        # BUGFIX-G29: connection is *live* but the tick stream is
                        # quiet (is_connected()==True while no new ticks arrive).
                        # The old branch simply reset the timer and declared the
                        # connection active, which masked a dead feed behind
                        # health=READY for 26 minutes in production. Now we treat
                        # a >15s quiet stream as a stalled ingestion: emit a
                        # STALE incident and force a market-data resubscribe /
                        # tick re-poll so ingestion actually restarts instead of
                        # being hidden. This never trades — it only restores the
                        # data feed; execution remains gated by the freshness
                        # contract (live_freshness_gate).
                        logger.warning(
                            "[WATCHDOG] Tick stream stalled while MT5 reports "
                            "connected (is_connected=True). Forcing market-data "
                            "resubscribe / tick re-poll to restart ingestion."
                        )
                        self.emit_incident_telemetry(
                            event_type="MT5_TICK_STREAM_STALLED",
                            component="mt5",
                            severity="HIGH",
                            correlation_id="tick-stream",
                        )
                        try:
                            # Re-subscribe symbols + re-poll fresh market state.
                            if hasattr(self.adapter, "resubscribe_symbol") and callable(
                                self.adapter.resubscribe_symbol
                            ):
                                self.adapter.resubscribe_symbol(symbol)
                            elif hasattr(self.adapter, "subscribe_symbols") and callable(
                                self.adapter.subscribe_symbols
                            ):
                                self.adapter.subscribe_symbols([symbol])
                            # Probe a fresh tick so the aggregator/feature path
                            # sees movement on the very next iteration.
                            with contextlib.suppress(Exception):
                                self.adapter.get_tick(symbol)
                            try:
                                await self._resync_from_broker(symbol)
                            except Exception as resync_err:
                                logger.error(
                                    "Watchdog stalled-stream resync failed",
                                    error=str(resync_err),
                                    exc_info=True,
                                )
                        except Exception as recon_err:
                            logger.error(
                                "Error during stalled-stream resubscribe",
                                error=str(recon_err),
                                exc_info=True,
                            )
                    self._last_tick_processed_time = time.time()

                # Account/tick refresh cadence: the account snapshot is
                # refreshed at most every 5s (it is only used for position
                # sizing / runtime mode / survival state — none of which need
                # per-tick freshness), but the account info + last tick are
                # needed for the decision loop. Between refreshes we reuse the
                # last snapshot to avoid a per-tick remote RPC (~4ms at
                # loopback, more over a real gateway).
                _now = time.time()
                if getattr(self, "_last_account_refresh", 0.0) + 5.0 < _now:
                    try:
                        live_account = self.adapter.get_account_info()
                    except Exception:
                        live_account = getattr(self, "_last_account_info", None)
                    self._last_account_info = live_account
                    self._last_account_refresh = _now
                else:
                    # Cache hit: reuse last successful account info (the tick
                    # still advances every iteration).
                    live_account = getattr(self, "_last_account_info", None)
                tick = self.adapter.get_last_tick(symbol)

                if live_account is None or tick is None:
                    await asyncio.sleep(0.2)
                    continue

                if self._symbol_info is None:
                    self._symbol_info = self.adapter.get_symbol_info(symbol)

                # PHASE 14: periodically refresh the typed broker-aware account
                # snapshot + REAL runtime mode (throttled - never per tick).
                if getattr(self, "_last_snapshot_refresh", 0.0) + 5.0 < time.time():
                    with contextlib.suppress(Exception):
                        self._account_snapshot = self.adapter.get_account_snapshot()
                    self._update_runtime_mode()
                    self._last_snapshot_refresh = time.time()

                # BUG-169: duplicate-tick early return. The MT5 last-tick poll
                # returns the SAME quote between feed updates; re-running the
                # full pipeline (features + policy + telemetry + audit) on it
                # burns the loop thread and logs NO_TRADE conf=0.0
                # (TICK_DUPLICATE_SUPPRESSED) as if it were a fresh decision,
                # which is what the UI then displays. A duplicate carries ZERO
                # new information: keep the previous proposal/state untouched
                # and service the heartbeat workers below.
                if (
                    tick.timestamp == getattr(self, "_pipeline_last_ts", None)
                    and float(tick.bid) == getattr(self, "_pipeline_last_bid", 0.0)
                    and float(tick.ask) == getattr(self, "_pipeline_last_ask", 0.0)
                ):
                    await self._service_pipeline_workers(now_t=time.time())
                    await asyncio.sleep(0.05)
                    continue
                self._pipeline_last_ts = tick.timestamp
                self._pipeline_last_bid = float(tick.bid)
                self._pipeline_last_ask = float(tick.ask)

                self._process_tick_pipeline(tick=tick, account=live_account)
                self._last_tick_processed_time = time.time()
                # PHASE 08: accounting worker kick (throttled internally). This
                # is the ONLY touch point and it schedules bounded to_thread
                # work; it can never block the tick loop.
                if self._accounting_worker_started:
                    try:
                        self._kick_worker("ACCOUNTING", self.accounting_worker.tick)
                    except Exception:
                        # Worker failure is fully isolated; never disturb ticks.
                        pass

                # BUG-054: audit retention purge (throttled ~6h, bounded batched
                # deletes, NEVER on the tick path). Failure is isolated: a purge
                # error must never disturb trading.
                now_t = time.time()
                if now_t - self._last_audit_purge_time >= self._audit_purge_interval_sec:
                    self._last_audit_purge_time = now_t
                    try:
                        await asyncio.to_thread(self.audit.purge_old_audit_data)
                    except Exception:
                        logger.error("Audit retention purge failed (isolated)")

                # TASK-11 + TASK-22: database hygiene cycle (config-driven
                # cadence; AUDIT_ONLY first run, off the tick path via
                # asyncio.to_thread; never deletes unless the operator enabled
                # apply_deletes and execution mode is not LIVE).
                if self._hygiene_scheduler is None and now_t - self._last_hygiene_time > 0:
                    try:
                        from nexus_scalp.hygiene.hygiene_runtime import (
                            RuntimeCleanupScheduler,
                            RuntimeHygieneSettings,
                        )

                        hyg_cfg = getattr(self.config, "database_hygiene", None) or {}
                        hygs = RuntimeHygieneSettings.from_mapping(
                            hyg_cfg.model_dump()
                            if hasattr(hyg_cfg, "model_dump")
                            else dict(hyg_cfg)
                        )
                        base_dir = getattr(self.config, "base_dir", None) or Path.cwd()
                        self._hygiene_scheduler = RuntimeCleanupScheduler(
                            repo_root=base_dir,
                            settings=hygs,
                            execution_mode=self._runtime_mode
                            or str(
                                getattr(self.config, "execution_mode", "PAPER") or "PAPER"
                            ).upper(),
                        )
                    except Exception as hyg_init_err:
                        logger.warning(
                            "[DB_HYGIENE] event=INIT_FAILED (isolated)",
                            error=str(hyg_init_err),
                        )
                if (
                    self._hygiene_scheduler is not None
                    and self._hygiene_scheduler.settings.enabled
                    and now_t - self._last_hygiene_time
                    >= self._hygiene_scheduler.light_interval_sec
                ):
                    self._last_hygiene_time = now_t
                    try:
                        deep = self._hygiene_scheduler.is_deep_due(now_t)
                        # Run the scheduler cycle on a thread; it owns the
                        # worker + quarantine + consistency + reports.
                        cyc = await asyncio.to_thread(self._hygiene_scheduler.run_cycle, deep=deep)
                        # Bounded Telegram REPORT (cooldown-gated, never spam).
                        if self._hygiene_scheduler.settings.telegram_report and (
                            self.notifier is not None and self.notifier.enabled
                        ):
                            tel = cyc.get("telemetry", {})
                            if (
                                not self._hygiene_scheduler._audit_done
                                or self._hygiene_scheduler.is_telegram_due(now_t)
                            ):
                                from nexus_scalp.hygiene.report import (
                                    build_telegram_report_text,
                                )

                                text = build_telegram_report_text(
                                    tel, self._hygiene_scheduler._cycle_number
                                )
                                self.notifier.send(text, severity="INFO")
                                self._hygiene_scheduler.mark_telegram_sent(now_t)
                    except Exception as hyg_err:
                        logger.warning(
                            "[DB_HYGIENE] event=CYCLE_FAILED (isolated)",
                            error=str(hyg_err),
                        )

                # TASK-13: incident response cycle (throttled ~60s, off the
                # tick path via to_thread; observability-only, INV-019). The
                # worker correlates structured telemetry into incidents and
                # persists them; it can never block or alter trading.
                if now_t - self._last_incident_time >= self._incident_interval_sec:
                    self._last_incident_time = now_t
                    try:
                        if self._incident_worker is None:
                            self._ensure_incident_worker()
                        if self._incident_worker is not None:
                            await asyncio.to_thread(self._incident_worker.tick)
                    except Exception as inc_err:
                        logger.warning(
                            "[INCIDENT_WORKER] event=CYCLE_FAILED (isolated)",
                            error=str(inc_err),
                        )

                # Daily Telegram performance summary (BUG-057): throttled to
                # once per 24h; built from the canonical accounting core (never
                # synthetic numbers). Failure is isolated.
                if now_t - self._last_daily_summary_time >= self._daily_summary_interval_sec:
                    self._last_daily_summary_time = now_t
                    try:
                        # Performance Intelligence upgrade: deterministic
                        # multi-stage report generator (reporting package)
                        # consumes the canonical AccountingCore read-only and
                        # produces the structured JSON contract + Telegram text.
                        from nexus_scalp.accounting import PeriodKind
                        from nexus_scalp.reporting import (
                            PerformanceReportEngine,
                            format_deep_report,
                            format_telegram_daily,
                        )

                        engine = PerformanceReportEngine(
                            core=self.accounting_core, kind=PeriodKind.DAY
                        )
                        container = engine.generate()
                        compact = format_telegram_daily(container)
                        deep = format_deep_report(container)
                        try:
                            if self.notifier.enabled:
                                # MESSAGE 1 = compact summary; MESSAGE 2/3 =
                                # deep intelligence (deterministic split when
                                # the deep text exceeds one message).
                                self.notifier.send(compact, severity="INFO")
                                if len(deep) > 3500:
                                    for chunk in _split_telegram_report(deep):
                                        self.notifier.send(chunk, severity="INFO")
                                else:
                                    self.notifier.send(deep, severity="INFO")
                        except Exception:
                            pass  # Telegram failure is isolated
                    except Exception as summary_err:
                        logger.error(
                            "[TELEGRAM_REPORT] event=FAILURE error_type=GENERATION error=%s",
                            summary_err,
                        )

                # ACCOUNT HISTORY: bounded background broker-history sync
                # (watermark + overlap, idempotent). Never on the tick path.
                if self._history_sync_started:
                    try:
                        self._kick_worker("HISTORY_SYNC", self.history_sync_worker.tick)
                    except Exception as wkr_err:
                        logger.warning("[HISTORY_SYNC_WORKER] event=KICK_FAILED error=%s", wkr_err)

                # PHASE 09: intelligence worker kick (throttled internally). It
                # runs in a worker thread and is fully failure-isolated; a
                # failure can never disturb the tick loop.
                if self._intelligence_worker_started:
                    try:
                        self._kick_worker("INTELLIGENCE", self.intelligence_worker.tick)
                    except Exception as wkr_err:
                        logger.warning("[INTELLIGENCE_WORKER] event=KICK_FAILED error=%s", wkr_err)

                # PHASE 09B: research worker kick (throttled internally, runs in
                # a worker thread). Research NEVER runs inside the tick
                # pipeline; a failure here can never disturb trading.
                if self._research_worker_started:
                    try:
                        self._kick_worker("RESEARCH", self.research_worker.tick)
                    except Exception as wkr_err:
                        logger.warning("[RESEARCH_WORKER] event=KICK_FAILED error=%s", wkr_err)

                # PHASE 10: controlled training worker kick (heavy CPU work is
                # bounded to worker threads; training can NEVER block ticks).
                if self._training_worker_started:
                    try:
                        self._kick_worker("TRAINING", self.training_worker.tick)
                    except Exception as wkr_err:
                        logger.warning("[TRAINING_WORKER] event=KICK_FAILED error=%s", wkr_err)

                # PHASE 11: shadow-aggregation worker kick (bounded, isolated).
                if self._shadow_worker_started:
                    try:
                        self._kick_worker("SHADOW", self.shadow_worker.tick)
                    except Exception as wkr_err:
                        logger.warning("[SHADOW_WORKER] event=KICK_FAILED error=%s", wkr_err)

                # PHASE 12: news intelligence worker kick (bounded, isolated).
                if self._news_enabled and self._news_worker_started:
                    try:
                        self._kick_worker("NEWS", self.news_worker.tick)
                    except Exception as wkr_err:
                        logger.warning("[NEWS_WORKER] event=KICK_FAILED error=%s", wkr_err)

                # TASK-6: bounded governance health snapshot (~5 min cadence,
                # queued write, failure-isolated — never blocks ticks).
                try:
                    self._save_governance_health_periodic()
                except Exception as gov_err:
                    logger.debug("[MODEL_GOVERNANCE] periodic health skipped", error=str(gov_err))
                await asyncio.sleep(0.05)

            except Exception as e:
                logger.error("Error in live loop", error=str(e), exc_info=True)
                with contextlib.suppress(Exception):
                    self.notifier.notify_error("Real-Time Execution Loop", str(e))
                await asyncio.sleep(1.0)

        await self._shutdown_async()

    async def _service_pipeline_workers(self, *, now_t: float) -> None:
        """BUG-169: heartbeat maintenance normally piggybacked on the tick
        iteration. On a duplicate tick the pipeline is skipped, but these
        time-throttled housekeeping duties MUST still run — otherwise a quiet
        feed would starve them. Contains only the throttled, non-trading
        cycles (purge / hygiene / incidents / daily summary); the worker KICKS
        are idempotent via _inflight_workers and keep their full cadence."""

        # BUG-054: audit retention purge (throttled ~6h, bounded batched
        # deletes, NEVER on the tick path).
        if now_t - self._last_audit_purge_time >= self._audit_purge_interval_sec:
            self._last_audit_purge_time = now_t
            try:
                await asyncio.to_thread(self.audit.purge_old_audit_data)
            except Exception:
                logger.error("Audit retention purge failed (isolated)")

        # TASK-13: incident response cycle (throttled ~60s, INV-019).
        if now_t - self._last_incident_time >= self._incident_interval_sec:
            self._last_incident_time = now_t
            try:
                if self._incident_worker is None:
                    self._ensure_incident_worker()
                if self._incident_worker is not None:
                    await asyncio.to_thread(self._incident_worker.tick)
            except Exception as inc_err:
                logger.warning(
                    "[INCIDENT_WORKER] event=CYCLE_FAILED (isolated)", error=str(inc_err)
                )

    #: PHASE 28: per-call timeout for background worker kicks executed via
    #: asyncio.to_thread inside run_loop. A hung C-extension call (MT5 IPC,
    #: sqlite C lock) previously parked a to_thread future forever, which
    #: froze the whole tick loop (inference/features/AI-Hub) while web stayed
    #: responsive. With wait_for, a hung kick is abandoned (the thread may
    #: linger but is detached from the loop) and the loop keeps ticking.
    WORKER_KICK_TIMEOUT_SEC: float = float(
        __import__("os").environ.get("NSE_WORKER_KICK_TIMEOUT", "45")
    )

    def _kick_worker(self, name: str, fn) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        WorkerSupervisor.kick_worker(
            name,
            fn,
            self._inflight_workers,
            self._background_tasks,
            timeout_sec=self.WORKER_KICK_TIMEOUT_SEC,
        )

    def _start_history_sync_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        WorkerSupervisor.start_worker(
            "account_history_sync",
            getattr(self, "history_sync_worker", None),
            self._history_sync_started,
            lambda v: setattr(self, "_history_sync_started", v),
        )

    async def _stop_history_sync_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        await WorkerSupervisor.stop_worker(
            "account_history_sync",
            getattr(self, "history_sync_worker", None),
            lambda v: setattr(self, "_history_sync_started", v),
        )

    async def _shutdown_async(self) -> None:
        # Stop the accounting worker first (derived refresh, not financial truth).
        with contextlib.suppress(Exception):
            await self._stop_accounting_worker()

        # ACCOUNT HISTORY: stop the broker-history sync worker.
        with contextlib.suppress(Exception):
            await self._stop_history_sync_worker()

        # PHASE 09: stop the intelligence worker (derived intelligence, isolated).
        with contextlib.suppress(Exception):
            await self._stop_intelligence_worker()

        # PHASE 09B: stop the strategy research worker (isolated).
        with contextlib.suppress(Exception):
            await self._stop_research_worker()

        # STRATEGY FACTORY: stop the autonomous loop worker (kill switch).
        with contextlib.suppress(Exception):
            await self._stop_factory_worker()

        # PHASE 10: stop the controlled training worker (isolated).
        with contextlib.suppress(Exception):
            await self._stop_training_worker()

        # PHASE 11: stop the shadow-aggregation worker (isolated).
        with contextlib.suppress(Exception):
            await self._stop_shadow_worker()

        # PHASE 12: stop the news intelligence worker (isolated, optional).
        with contextlib.suppress(Exception):
            await self._stop_news_worker()

        # TASK-13: stop the incident response worker (isolated).
        with contextlib.suppress(Exception):
            await self._stop_incident_worker()

        # Cancel retrain task safely
        with contextlib.suppress(Exception):
            if self._retrain_task and not self._retrain_task.done():
                self._retrain_task.cancel()
                with contextlib.suppress(Exception):
                    await self._retrain_task

        with contextlib.suppress(Exception):
            self.adapter.disconnect()

        with contextlib.suppress(Exception):
            self.audit.close()

        with contextlib.suppress(Exception):
            ci = getattr(self, "candle_intel", None)
            if ci is not None:
                ci.store.close()

        with contextlib.suppress(Exception):
            self.notifier.notify_shutdown(reason="Engine Stopped")

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
        Initializes the MarketRegimeClassifier with XAUUSD-evidenced calibration.

        Thresholds were recalibrated from 100k real XAUUSD M1 bars (2026-05..08)
        in BUG-132. The classifier defaults already encode those values, so we
        only override the two that differ from the constructor defaults
        (spread hysteresis band + hold/markup margins) to keep a single source of
        truth in the classifier module.
        """
        try:
            return MarketRegimeClassifier(
                symbol=symbol,
                spread_chop_enter_usd=0.25,
                spread_chop_exit_usd=0.18,
                min_regime_hold_sec=4.0,
                switch_prob_margin=0.10,
            )
        except TypeError:
            return MarketRegimeClassifier(symbol=symbol)

    def _assert_regime_state_freshness(self, tick: TickData) -> None:
        """BUG-TDF-Q2: alarm when a REUSED regime state is too old.

        Researcher TDF-R2 Q2/Q2b: the BUG-169 duplicate-tick path reuses
        ``_regime_last_state`` without any freshness check, so a frozen
        quote stream can hold the last regime (e.g. FREEZE_ALL /
        HIGH_SPREAD_CHOP) indefinitely — the classifier's hysteresis
        "never stuck frozen" guarantee silently assumes fresh ticks.

        ALARM-ONLY by design: forcing a reclassification from duplicate
        tick data would push the duplicate into the classifier's rolling
        rings (skewing tick_velocity / rv_5m / norm_ofi) and break the
        BUG-169 dedup contract. Instead a distinct, rate-limited,
        structured WARNING names the staleness (event=STALE_STATE_REUSED,
        audit-visible regime identity + ages) so operators/automation can
        detect a frozen feed. Never raises; never mutates state.
        """
        if getattr(self, "_regime_last_state", None) is None:
            return  # nothing cached yet; the fresh-tick path will stamp it
        try:
            max_age_sec = float(
                getattr(getattr(self.config, "algo", None), "regime_state_max_age_sec", 300.0)
            )
        except (TypeError, ValueError):
            max_age_sec = 300.0
        classified_at = getattr(self, "_regime_state_classified_at", None)
        now = time.time()
        if classified_at is not None and (now - float(classified_at)) <= max_age_sec:
            return  # state proven fresh within the window: silent
        if (now - getattr(self, "_regime_stale_warn_at", 0.0)) < max_age_sec:
            return  # already alarmed inside this window (rate-limit)
        self._regime_stale_warn_at = now
        state_age = f"{now - float(classified_at):.1f}s" if classified_at is not None else "unknown"
        logger.warning(
            "[REGIME] event=STALE_STATE_REUSED ALARM_ONLY mode=dedup_reuse "
            "state_age=%s max_age_sec=%.1f symbol=%s regime=%s reason=%s "
            "tick_ts=%s (frozen/duplicate quote stream suspected; "
            "BUG-169 dedup preserved: duplicate NOT reclassified)",
            state_age,
            max_age_sec,
            tick.symbol,
            getattr(getattr(self, "_regime_last_state", None), "regime_type", "UNKNOWN"),
            getattr(getattr(self, "_regime_last_state", None), "reason", "UNKNOWN"),
            tick.timestamp.isoformat(),
        )

    # -------------------------
    # Model / scaler bundle
    # -------------------------

    def _load_or_create_bundle(self, model_path: Path, force_fresh: bool) -> ModelBundle:
        model = self._load_or_initialize_model_weights(
            model_path=model_path, force_fresh=force_fresh
        )
        scaler = self._load_scaler_artifacts(model_path=model_path)
        return ModelBundle(model=model, scaler=scaler, artifact_path=model_path)

    @staticmethod
    def _artifact_meta_coherence(model_path: Path) -> dict[str, Any]:
        """AGENT-10: metadata/tensor coherence + schema-identity verdict.

        Reads model.meta.json (when present) and the serialized tensors and
        verifies:
          * artifact head width == meta num_classes/model_head_classes
            (the 4-head + 3-meta P0 incoherence class is rejected here);
          * artifact input width == meta feature dimension (BUG-141 class);
          * meta feature_schema_id is a REGISTERED schema id — dimension
            equality alone is not identity (family semantics, 50/60/70D).
        Returns {"ok": bool, "reason": str, ...diagnostic fields}. Missing
        meta is NOT an error here (cold-start bundles carry no meta; the
        width gate downstream still applies) — coherence is enforced only
        on the fields that EXIST.
        """
        import json as _json

        verdict: dict[str, Any] = {
            "ok": True,
            "reason": "",
            "path": str(model_path),
        }
        try:
            meta_path = Path(model_path).with_suffix(".meta.json")
            if not meta_path.exists():
                verdict["reason"] = "NO_META"
                return verdict
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            state = torch.load(model_path, map_location="cpu", weights_only=True)
            if not isinstance(state, dict):
                verdict.update(ok=False, reason="STATE_DICT_UNREADABLE")
                return verdict
            ip = state.get("input_projection.weight")
            cls = state.get("classifier.weight")
            if ip is None or cls is None or not hasattr(ip, "shape") or not hasattr(cls, "shape"):
                verdict.update(ok=False, reason="MISSING_CORE_TENSORS")
                return verdict
            artifact_head = int(cls.shape[0])
            artifact_dim = int(ip.shape[1])
            meta_head = meta.get("model_head_classes", meta.get("num_classes"))
            meta_dim = meta.get("feature_schema_dimension", meta.get("num_features"))
            verdict.update(
                artifact_head=artifact_head,
                artifact_dim=artifact_dim,
                meta_head=meta_head,
                meta_dim=meta_dim,
            )
            if meta_head is not None and int(meta_head) != artifact_head:
                verdict.update(ok=False, reason="HEAD_META_CLASS_MISMATCH")
                return verdict
            if meta_dim is not None and int(meta_dim) != artifact_dim:
                verdict.update(ok=False, reason="DIMENSION_META_MISMATCH")
                return verdict
            schema_id = str(meta.get("feature_schema_id", "") or "")
            if schema_id:
                from nexus_scalp.features.schema import FEATURE_SCHEMAS

                if not FEATURE_SCHEMAS.is_registered(schema_id):
                    verdict.update(ok=False, reason="UNREGISTERED_SCHEMA_ID")
                    return verdict
                resolved = FEATURE_SCHEMAS.resolve(schema_id)
                if resolved.dimension != artifact_dim:
                    verdict.update(ok=False, reason="SCHEMA_DIMENSION_MISMATCH")
                    return verdict
            verdict["reason"] = "COHERENT"
            return verdict
        except Exception as exc:  # unreadable artifact => refuse loudly
            verdict.update(ok=False, reason="COHERENCE_PROBE_FAILED", detail=str(exc))
            return verdict

    def _expected_num_features_for_artifact(self, model_path: Path) -> int:
        """Infer expected input width from the on-disk artifact, falling back to class default.

        When the checkpoint exists, its ``input_projection.weight.shape[1]`` is
        the source of truth (covers 50D + 70D). On cold-start (no file) the
        class ``FEATURE_DIM`` is kept so first-time users still bootstrap 50D.
        """
        with contextlib.suppress(Exception):
            if model_path.exists():
                probe = torch.load(model_path, map_location="cpu")
                w = probe.get("input_projection.weight") if isinstance(probe, dict) else None
                if w is not None and hasattr(w, "shape") and len(w.shape) == 2:
                    return int(w.shape[1])
        # BUG-125 regression: tests call via LiveEngine._expected_num_features_for_artifact(None, path)
        # (unbound with self=None on macOS). Handle None gracefully.
        if self is None:
            return int(LiveEngine.FEATURE_DIM)
        return int(self.__class__.FEATURE_DIM)

    def _declared_contract_dim_for_path(self, model_path: Path) -> int | None:
        """BUG-141: DECLARED feature width for an artifact path (meta.json first).

        Reads the bundle's own declaration (model.meta.json -> scaler npz ->
        existing checkpoint, in that order) instead of the process-wide class
        default. Returns None when the path carries no declaration yet
        (cold-start) so first-run bootstrap semantics are unchanged.
        """
        import json as _json

        with contextlib.suppress(Exception):
            meta_path = model_path.with_suffix(".meta.json")
            if meta_path.exists():
                with open(meta_path, encoding="utf-8") as fh:
                    meta = _json.load(fh)
                dim = meta.get("feature_schema_dimension") or meta.get("num_features")
                if isinstance(dim, int) and dim > 0:
                    return dim
        with contextlib.suppress(Exception):
            scaler_path = model_path.with_suffix(".scaler.npz")
            if scaler_path.exists():
                data = np.load(scaler_path)
                shape = tuple(np.asarray(data["mean"]).shape)
                if shape and shape[0] > 0:
                    return int(shape[0])
        with contextlib.suppress(Exception):
            if model_path.exists():
                probe = torch.load(model_path, map_location="cpu")
                w = probe.get("input_projection.weight") if isinstance(probe, dict) else None
                if w is not None and hasattr(w, "shape") and len(w.shape) == 2:
                    return int(w.shape[1])
        return None

    # ------------------------------------------------------------------
    # BUG-243 (Agent-4 serving-integrity lane): bundle-coherent class-head
    # mint. MODEL_ARTIFACT_FORENSICS proved the deployed champion carries
    # meta "model_head_classes: 3" over a 4-logit tensor. The mint sites
    # hardcoded ScalpNet(num_classes=4); the contract SSoT is 3. Mint now
    # reads the bundle's DECLARED head (meta; legacy-4 only when the
    # artifact itself declares 4) and otherwise falls back to SSoT.
    # ------------------------------------------------------------------
    @staticmethod
    def _declared_head_classes_for_path(meta_path: Path) -> int:
        """Declared neural head width for a bundle path (BUG-243)."""
        import json as _json

        from nexus_scalp.model_lifecycle.model_class_contract import (
            LEGACY_HEAD_CLASSES,
            TRAINED_CLASS_COUNT,
        )

        with contextlib.suppress(Exception):
            if Path(meta_path).exists():
                with open(meta_path, encoding="utf-8") as fh:
                    meta = _json.load(fh)
                for key in ("model_head_classes", "num_classes"):
                    val = meta.get(key)
                    if isinstance(val, int) and val in (TRAINED_CLASS_COUNT, LEGACY_HEAD_CLASSES):
                        return int(val)
        return TRAINED_CLASS_COUNT

    def _load_or_initialize_model_weights(self, model_path: Path, force_fresh: bool) -> ScalpNet:
        """Loads model.pt if present, validating against the artifact's own declared width.

        BUG-125: the width gate now validates against the checkpoint's own
        declared tensor width (artifact-driven contract selection) instead of
        the process-wide 50D default.
        """
        if force_fresh:
            # BUG-141: seed the width the PATH's declared contract demands
            # (meta/scaler/checkpoint), not the process-wide class default -
            # force_fresh must never mint a 50D file into a declared-70D path.
            expected_dim = self._declared_contract_dim_for_path(model_path) or int(
                self.__class__.FEATURE_DIM
            )
        else:
            expected_dim = self._expected_num_features_for_artifact(model_path)
        # BUG-243: mint at the bundle's DECLARED head width, not hardcoded 4.
        declared_head = self._declared_head_classes_for_path(model_path.with_suffix(".meta.json"))
        model = ScalpNet(num_features=expected_dim, num_classes=declared_head)
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
                with contextlib.suppress(Exception):
                    model_path.rename(backup_path)
                raise RuntimeError(
                    f"Checkpoint dimension mismatch: expected {expected}, got {loaded}"
                )

            model.load_state_dict(state_dict)
            logger.info("Loaded model weights", path=str(model_path), expected_dim=expected_dim)
            return model

        logger.info(
            "Initializing fresh model weights", path=str(model_path), expected_dim=expected_dim
        )
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

            # BUG-125: scaler width must match the MODEL's declared width
            expected_dim = self._expected_num_features_for_artifact(model_path)
            if mean.shape[0] != expected_dim or std.shape[0] != expected_dim:
                raise RuntimeError(
                    f"Scaler dim invalid: mean{mean.shape} std{std.shape} "
                    f"expected ({expected_dim},) for artifact {model_path.name}"
                )

            logger.info(
                "Loaded scaler artifacts successfully",
                path=str(scaler_path),
                mean_shape=mean.shape,
                std_shape=std.shape,
            )
            # OBS-PERF-RESILIENCE: a degenerate std (zero/negative/non-finite)
            # makes the bundle NOT-ready (transform passes features through
            # unchanged instead of dividing by zero). Surface it loudly at
            # load time — the degraded state must be visible, never silent.
            degenerate = int(np.sum(~(np.isfinite(std) & (std > 0.0))))
            if degenerate:
                logger.warning(
                    "[SCALER_DEGRADED] event=DEGENERATE_STD scaler_not_ready_features_passthrough",
                    path=str(scaler_path),
                    degenerate_columns=degenerate,
                    total_columns=int(std.shape[0]),
                )
            return ScalerBundle(mean=mean, std=std)

        except Exception as err:
            logger.warning(
                "Failed to load scaler; fallback to raw features",
                error=str(err),
                path=str(scaler_path),
            )
            return ScalerBundle(mean=None, std=None)

    def _save_model_weights_atomic(self, model: ScalpNet, model_path: Path) -> bool:
        """Saves current PyTorch model weights state_dict atomically to disk with thread lock and logging.

        BUG-141 guard: refuses to persist weights whose input width contradicts
        the target path's DECLARED contract (meta/scaler/checkpoint). A
        desynced runtime state must never silently overwrite a bundle with a
        mismatched-dimension artifact (the 2026-08-27 70d_liquidity clobber
        class). Mismatch -> CRITICAL log + no write (artifact preserved).

        Returns True on successful persist, False on BUG-141 refusal or I/O
        failure so callers can refuse the END-TO-END persist (no bundle swap,
        no provenance, explicit ASYNC_RETRAIN_REFUSED) instead of diverging
        memory==disk identity.
        """
        try:
            model_width = int(model.input_projection.weight.shape[1])
            declared = self._declared_contract_dim_for_path(model_path)
            if declared is not None and declared != model_width:
                logger.critical(
                    "[BUG141_GUARD] event=ARTIFACT_WIDTH_CONTRACT_REFUSED",
                    path=str(model_path),
                    model_width=model_width,
                    declared_dim=declared,
                )
                return False
        except Exception as guard_err:  # never block the save on guard failure
            logger.warning(
                "[BUG141_GUARD] contract probe failed (save proceeds)",
                error=str(guard_err),
                path=str(model_path),
            )
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
                return False
        return True

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

            # 0.0 is the documented HTF cold-start fallback value (not a real
            # reading); counting fallbacks here quantifies warmup progress.
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
                f"[FEATURE_STATUS]\nbase_features=50\nmodel_input_features={self.effective_feature_dim}\nfeature_schema={self.effective_feature_schema_id}\nvalid={valid_count}\nbase_fallbacks={fallback_count}\ninvalid={invalid_count}\nhtf_fallbacks={htf_fallbacks}\nstatus=NOT_READY"
            )
            self.warmup_state = "SAFE_NOT_READY"
            self._inference_enabled = False
            logger.error("[WARMUP] FAILED\nreason=INSUFFICIENT_HTF_HISTORY\nstate=SAFE_NOT_READY")
            logger.warning("[INFERENCE] BLOCKED\nreason=HTF_WARMUP_INCOMPLETE")
        else:
            self.warmup_state = "READY"
            self._inference_enabled = True
            logger.info(
                f"[FEATURE_STATUS]\nbase_features=50\nmodel_input_features={self.effective_feature_dim}\nfeature_schema={self.effective_feature_schema_id}\nvalid={valid_count}\nbase_fallbacks={fallback_count}\ninvalid={invalid_count}\nhtf_fallbacks={htf_fallbacks}\nstatus=READY"
            )
            # STATE-SEMANTICS (C-002, 2026-09-02): the htf_fallbacks counter
            # is the HTF (H1/H4) fallback count ONLY. It was previously
            # mislabeled fallback_features=N, contradicting
            # [FEATURE_STATUS] base_fallbacks=17 (BUG-070-5 class).
            logger.info(
                f"[WARMUP] COMPLETE\nsymbol={symbol}\nH1={len(h1_bars)}/{self.H1_REQUIRED_BARS}\nH4={len(h4_bars)}/{self.H4_REQUIRED_BARS}\nhtf_fallbacks={htf_fallbacks}\nbase_fallbacks={fallback_count}\nstatus=READY"
            )
            logger.info("[INFERENCE] ENABLED\nreason=HTF_WARMUP_COMPLETE")

        return is_ready

    def _start_accounting_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        WorkerSupervisor.start_worker(
            "accounting_worker",
            getattr(self, "accounting_worker", None),
            self._accounting_worker_started,
            lambda v: setattr(self, "_accounting_worker_started", v),
        )

    async def _stop_accounting_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        await WorkerSupervisor.stop_worker(
            "accounting_worker",
            getattr(self, "accounting_worker", None),
            lambda v: setattr(self, "_accounting_worker_started", v),
        )

    # ---------------------------------------------------------------------
    # PHASE 09: INTELLIGENCE WORKER lifecycle
    # ---------------------------------------------------------------------

    def _start_intelligence_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        WorkerSupervisor.start_worker(
            "intelligence_worker",
            getattr(self, "intelligence_worker", None),
            self._intelligence_worker_started,
            lambda v: setattr(self, "_intelligence_worker_started", v),
        )

    async def _stop_intelligence_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        await WorkerSupervisor.stop_worker(
            "intelligence_worker",
            getattr(self, "intelligence_worker", None),
            lambda v: setattr(self, "_intelligence_worker_started", v),
        )

    def _start_research_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        WorkerSupervisor.start_worker(
            "research_worker",
            getattr(self, "research_worker", None),
            self._research_worker_started,
            lambda v: setattr(self, "_research_worker_started", v),
        )

    def _start_factory_worker(self) -> None:
        """Starts the autonomous strategy-factory worker (idempotent).

        The loop starts in STOPPED control state; the operator drives it via
        the Strategy Factory UI/API (start/pause/resume/stop). Autonomous mode
        never starts itself on boot — generation is operator-triggered.
        """
        if self._factory_worker_started:
            return
        self._factory_worker_started = True
        try:
            # Recovery probe: if an autonomous loop was mid-generation before
            # restart, surface the persisted state (operator resumes manually).
            self.strategy_factory_worker.recover()
        except Exception as err:
            logger.error("[STRATEGY_FACTORY] event=START status=FAILED", error=str(err))
            self._factory_worker_started = False

    def _start_training_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        WorkerSupervisor.start_worker(
            "training_worker",
            getattr(self, "training_worker", None),
            self._training_worker_started,
            lambda v: setattr(self, "_training_worker_started", v),
        )

    async def _stop_training_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        await WorkerSupervisor.stop_worker(
            "training_worker",
            getattr(self, "training_worker", None),
            lambda v: setattr(self, "_training_worker_started", v),
        )

    def _start_shadow_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        WorkerSupervisor.start_worker(
            "shadow_worker",
            getattr(self, "shadow_worker", None),
            self._shadow_worker_started,
            lambda v: setattr(self, "_shadow_worker_started", v),
        )

    async def _stop_shadow_worker(self) -> None:
        from nexus_scalp.application.live_workers import WorkerSupervisor

        await WorkerSupervisor.stop_worker(
            "shadow_worker",
            getattr(self, "shadow_worker", None),
            lambda v: setattr(self, "_shadow_worker_started", v),
        )

    def _start_news_worker(self) -> None:
        """Starts the news intelligence worker (idempotent, never raises).

        PHASE 12: fully isolated - a news startup failure logs and the engine
        keeps trading with the news subsystem disabled.
        """
        if not self._news_enabled or self._news_worker_started:
            return
        self._news_worker_started = True
        try:
            self.news_worker.start()
            logger.info("[NEWS_WORKER] event=START status=RUNNING")
        except Exception as err:
            self._news_worker_started = False
            logger.error("[NEWS_WORKER] event=START status=FAILED", error=str(err))

    def _start_news_engine_from_snapshot(self, snap: Any) -> None:
        """Hot-reload helper: (re)construct the news engine + worker + gate.

        Used by _sync_runtime_config when the operator enables news from the
        UI without restarting. Fully isolated like the bootstrap constructor:
        a failure leaves the subsystem disabled and trading unaffected.
        """
        from nexus_scalp.news import NewsEngine, NewsGate, NewsWorker
        from nexus_scalp.news.config import NewsConfig, NewsPollingConfig

        cfg = NewsConfig(
            enabled=True,
            worker_interval_sec=int(snap.news.worker_interval_sec),
            max_queue_size=int(snap.news.max_queue_size),
            polling=NewsPollingConfig(
                fast_interval_sec=int(snap.news.poll_fast_interval_sec),
                medium_interval_sec=int(snap.news.poll_medium_interval_sec),
                slow_interval_sec=int(snap.news.poll_slow_interval_sec),
            ),
        )
        self.news_engine = NewsEngine(config=cfg)
        self.news_worker = NewsWorker(
            engine=self.news_engine,
            interval_sec=float(snap.news.worker_interval_sec),
            max_queue=int(snap.news.max_queue_size),
        )
        # seed auto-analysis gate from snapshot
        with contextlib.suppress(Exception):
            self.news_worker.auto_analysis_enabled = bool(
                getattr(snap.news, "auto_analysis_enabled", False)
            )
            self._news_auto_analysis_enabled = bool(self.news_worker.auto_analysis_enabled)
        self.news_gate = NewsGate(config=cfg)
        self._news_enabled = True
        self._news_worker_started = False
        # Start the worker if the engine is already running.
        if getattr(self, "_running", False):
            self._start_news_worker()
        logger.info(
            "[NEWS] event=HOT_RELOAD_CONSTRUCTED status=ENABLED runtime_version=%d",
            getattr(snap, "version", 0),
        )

    def _stop_news_engine_hot(self) -> None:
        """Hot-reload helper: tear down the news worker + engine + gate.

        Called when the operator disables news from the UI. Stops the worker
        (even if _news_enabled is still True — the guard in _stop_news_worker
        would otherwise early-return).
        """
        try:
            if self.news_worker is not None and self._news_worker_started:
                self._news_worker_started = False
                with contextlib.suppress(Exception):
                    self.news_worker.stop()
        finally:
            self._news_enabled = False
            self.news_engine = None
            self.news_worker = None
            self.news_gate = None

    async def _stop_news_worker(self) -> None:
        """Stops the news worker (idempotent, never raises)."""
        if not self._news_enabled or not self._news_worker_started:
            return
        self._news_worker_started = False
        try:
            self.news_worker.stop()
        except Exception as err:
            logger.error("[NEWS_WORKER] event=STOP status=FAILED", error=str(err))

    def _ensure_incident_worker(self) -> None:
        """Lazily constructs the incident worker + telemetry collector.

        Fully isolated: a construction failure logs and leaves the worker
        None so the engine keeps trading (INV-019).
        """
        try:
            if self._incident_worker is not None:
                return
            from nexus_scalp.incidents.store import IncidentStore
            from nexus_scalp.incidents.telemetry import IncidentTelemetryCollector
            from nexus_scalp.incidents.worker import IncidentWorker

            db_path = getattr(self.audit, "_db_path", "")
            store = IncidentStore(db_path=db_path, audit_repo=self.audit)
            notifier = getattr(self, "notifier", None)
            self._incident_worker = IncidentWorker(
                store=store,
                interval_sec=self._incident_interval_sec,
                telegram_notifier=notifier if notifier is not None else None,
            )
            self._incident_worker.start()
            self._incident_telemetry = IncidentTelemetryCollector(worker=self._incident_worker)
            logger.info("[INCIDENT_WORKER] event=START status=RUNNING")
        except Exception as inc_start_err:
            self._incident_worker = None
            self._incident_telemetry = None
            logger.warning(
                "[INCIDENT_WORKER] event=START_FAILED (isolated)",
                error=str(inc_start_err),
            )

    def emit_incident_telemetry(
        self,
        *,
        event_type: str,
        component: str,
        error_code: str = "",
        correlation_id: str = "",
        ticket: str = "",
        execution_id: str = "",
        severity: str | None = None,
    ) -> bool:
        """Feeds one structured runtime event into the incident pipeline.

        Called from engine error handlers; never blocks, never raises.
        Returns True when accepted.
        """
        if self._incident_telemetry is None:
            return False
        try:
            return self._incident_telemetry.emit(
                event_type=event_type,
                component=component,
                error_code=error_code,
                correlation_id=correlation_id,
                ticket=ticket,
                execution_id=execution_id,
                severity=severity,
            )
        except Exception:
            return False

    async def _stop_incident_worker(self) -> None:
        """Stops the incident worker (idempotent, never raises)."""
        try:
            if self._incident_worker is not None:
                self._incident_worker.stop()
                self._incident_worker = None
            self._incident_telemetry = None
        except Exception as err:
            logger.error("[INCIDENT_WORKER] event=STOP status=FAILED", error=str(err))

    def _news_strategy_direction(self, proposal: Any) -> str:
        """Infers the strategy direction behind a proposal for the news gate.

        Pure read of the proposal action - the news gate never decides the
        direction itself.
        """
        action = getattr(proposal, "action", None)
        action_str = action.value if hasattr(action, "value") else str(action or "")
        upper = str(action_str).upper()
        if upper in ("BUY", "BUY_MARKET", "BUY_LIMIT", "BUY_STOP"):
            return "BULLISH"
        if upper in ("SELL", "SELL_MARKET", "SELL_LIMIT", "SELL_STOP"):
            return "BEARISH"
        return "NEUTRAL"

    async def _stop_research_worker(self) -> None:
        """Stops the strategy research worker (idempotent, never raises)."""
        self._research_worker_started = False
        try:
            self.research_worker.stop()
        except Exception as err:
            logger.error("[RESEARCH_WORKER] event=STOP status=FAILED", error=str(err))

    async def _stop_factory_worker(self) -> None:
        """Stops the strategy-factory worker (idempotent, never raises).

        The kill switch (spec 106) prevents new generations / LLM requests;
        historical research rows are never corrupted.
        """
        self._factory_worker_started = False
        try:
            self.strategy_factory_worker.stop()
        except Exception as err:
            logger.error("[STRATEGY_FACTORY] event=STOP status=FAILED", error=str(err))

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
            # BUG-174: historical orphan backfill. Decisions created BEFORE the
            # P0-A writers existed (and predictive-limit gate rejections whose
            # model_action was unset before BUG-169b) never received a terminal
            # outcome -> they re-log as MISSING_OUTCOME on every dataset build
            # (308 lines on the 21:01 restart alone). Run the evidence-based
            # recovery sweep once per startup: it classifies from broker truth
            # (dispatch log -> audit_broker_orders/deals) and appends terminal
            # outcomes through the idempotent ledger. Bounded + append-only;
            # a failure here is isolated and logged.
            try:
                from nexus_scalp.experience.outcome_recovery_sweep import (
                    HistoricalOutcomeRecoverySweep,
                )

                sweep_result = await asyncio.to_thread(
                    HistoricalOutcomeRecoverySweep(ledger=self.experience_ledger).run,
                    False,
                )
                sd = sweep_result.to_dict()
                logger.info(
                    "[EXPERIENCE] ORPHAN_RECOVERY_SWEEP complete scanned=%s recovered=%s "
                    "unknown_provenance=%s still_live=%s excluded=%s reconciled=%s",
                    sd.get("scanned", 0),
                    sd.get("recovered", 0),
                    sd.get("unknown_provenance", 0),
                    sd.get("skipped_still_live", 0),
                    sd.get("excluded_by_filter", 0),
                    sd.get("reconciled", False),
                )
            except Exception as sweep_err:
                logger.error(
                    "[EXPERIENCE] ORPHAN_RECOVERY_SWEEP failed (isolated)", error=str(sweep_err)
                )
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

        # RESYNC (BUG-054): reseed the aggregator with the broker-authoritative
        # M1 history instead of blind-appending. After 5-6h downtime the first
        # live tick must CONTINUE the broker's current minute, not mint a
        # duplicate stale bar with the same timestamp.
        last_seeded = self.aggregator.reseed(hist_m1_bars)
        completed_init = self.aggregator.get_completed_bars()
        if completed_init:
            self._warm_liquidity_from_bars(completed_init, atr=1.5)

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
                # BUG-185 PART-3: 70D champion => the record carries the full
                # canonical scalp_v3 geometry (Base|News|Liquidity) via the
                # shared builder; the builder REFUSES (returns None) when the
                # real liquidity snapshot is not yet VALID instead of indexing
                # a 50-element base over a 70-wide range (IndexError class).
                record = self._build_retrain_record(
                    base50=fv.to_tensor_input(),
                    fv=fv,
                    bar=b,
                    spread=0.20,
                    context="cold_start_warmup",
                )
                if record is None:
                    continue
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
            for b in completed_bars[-900:]:
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
            logger.info(
                "Cold-start SMC visual overlays successfully bridged to server state!",
                bars=len(bars_list),
                last_seeded=last_seeded.timestamp.isoformat() if last_seeded else None,
            )

    async def _resync_from_broker(self, symbol: str) -> None:
        """Broker-authoritative reseed after downtime / reconnect (BUG-054).

        * Re-fetches 3500 M1 bars (or the engine's configured chart window).
        * Reseeds the aggregator (duplicate/stale minutes are dropped and the
          forming bar continues the broker's latest minute).
        * Recomputes the feature window so models/regime see a continuous
          series instead of a gap.
        * Pushes a fresh 900-bar snapshot + SMC overlays to ServerState so the
          UI immediately paints real broker candles.
        """
        chart_count = 3500
        hist_m1 = (
            await asyncio.to_thread(self.adapter.get_historical_bars, symbol, "M1", chart_count)
            or []
        )
        last_seeded = self.aggregator.reseed(hist_m1)
        completed_resync = self.aggregator.get_completed_bars()
        if completed_resync:
            self._warm_liquidity_from_bars(completed_resync, atr=1.5)
        if last_seeded is None:
            logger.warning("[RESYNC] SKIPPED reason=NO_BROKER_BARS")
            return

        completed = self.aggregator.get_completed_bars()
        if completed:
            # Rebuild a bounded rolling feature window (causal, no lookahead).
            window = completed[-900:]
            last = window[-1]
            synthetic_tick = TickData(
                symbol=symbol,
                timestamp=last.timestamp,
                bid=last.close,
                ask=last.close + 0.20,
                volume=last.tick_volume,
            )
            fv = self.feature_engine.compute_from_bars(window, synthetic_tick)
            # BUG-185 PART-3: shared canonical record builder (see
            # _cold_start_warmup); refuses instead of fabricating.
            record = self._build_retrain_record(
                base50=fv.to_tensor_input(),
                fv=fv,
                bar=last,
                spread=0.20,
                context="broker_resync",
            )
            if record is not None:
                self._rolling_feature_records.append(record)

        self.sync_chart_state()
        logger.info(
            "[RESYNC] COMPLETE",
            symbol=symbol,
            bars=len(completed),
            last=last_seeded.timestamp.isoformat(),
        )
        self.evaluate_warmup_readiness(
            symbol,
            (
                await asyncio.to_thread(
                    self.adapter.get_historical_bars, symbol, "H1", self.H1_REQUIRED_BARS
                )
                or []
            ),
            (
                await asyncio.to_thread(
                    self.adapter.get_historical_bars, symbol, "H4", self.H4_REQUIRED_BARS
                )
                or []
            ),
        )

    def sync_chart_state(self) -> None:
        """Push the current aggregator series + SMC overlays to ServerState.

        Used after a reseed / reconnect so the UI chart (which prefers
        ServerState) always renders the synchronized broker candles, and by the
        REST layer as a lazy refresh before serving snapshots.
        """
        if self.server_state is None:
            return
        completed = self.aggregator.get_completed_bars()
        if not completed:
            return
        raw_atr = (
            self._rolling_feature_records[-1]["atr_m1"] if self._rolling_feature_records else 1.5
        )
        real_overlays = self.signal_policy.extract_live_chart_overlays(
            completed_bars=completed, atr_val=raw_atr
        )
        bars_list = []
        for b in completed[-900:]:
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

    async def _bootstrap_train_if_ready(self) -> None:
        if len(self._rolling_feature_records) < 300:
            return

        logger.info(
            "BOOTSTRAP: initial online fine-tune starting...",
            rows=len(self._rolling_feature_records),
        )
        df_hist = pl.DataFrame(list(self._rolling_feature_records))
        df_labeled = self.online_labeler.label_dataframe(df_hist)

        # BUG-182B: artifact-driven columns (see _trigger_async_online_fine_tune).
        feature_cols = list(self.effective_feature_cols)
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
        # BUG-185: same-contract swap today, but the rebind is cheap and
        # keeps the trainer bound if the artifact contract ever changes.
        self._rebind_trainer_to_bundle()

        self._run_model_diagnostics_and_summary(df_labeled=df_labeled, feature_cols=feature_cols)

        # PHASE 09 HARDENING: if the (possibly rolled-back) model is now in a
        # mono-class collapse, re-initialize it rather than serving the broken
        # baseline until the next rejected fine-tune.
        if self._bundle is not None:
            self._reinitialize_collapsed_model()

    # -------------------------
    # Runtime configuration (hot reload)
    # -------------------------

    def _sync_runtime_config(self) -> None:
        """Re-sync services against the CURRENT immutable snapshot.

        Called once per tick (cheap attribute assignments) and on every
        ConfigurationChanged event. All new evaluations use the new values.
        """
        snap = self.runtime_config.get_snapshot()
        try:
            self.signal_policy.algo_config = snap.to_algo_config()
            self.order_manager.algo_config = snap.to_algo_config()
            self.risk_engine.min_risk_reward_ratio = snap.min_risk_reward_ratio
            self.risk_engine.min_rr_high_confidence = snap.algo.min_rr_high_confidence
            self.risk_engine.high_confidence_threshold = snap.algo.high_confidence_threshold
            self.risk_engine.max_allowed_lots = snap.max_allowed_lots
            self.risk_engine.max_margin_usage_pct = snap.risk.max_margin_usage_pct
            # RiskConfig section: rebuild immutably so risk gates (spread,
            # lot, drawdown, concurrent positions, enforce SL) read the new
            # snapshot on the next evaluation (never partial field edits).
            self.risk_engine.config = self.risk_engine.config.model_copy(
                update={
                    "max_spread_points": snap.max_spread_points,
                    "risk_per_trade_pct": snap.risk_per_trade_pct,
                    "max_account_drawdown_pct": snap.max_account_drawdown_pct,
                    "max_concurrent_positions": snap.max_concurrent_positions,
                    "max_allowed_lots": snap.max_allowed_lots,
                    "enforce_stop_loss": snap.enforce_stop_loss,
                    "max_margin_usage_pct": snap.risk.max_margin_usage_pct,
                }
            )
            self.signal_policy.confidence_threshold = snap.confidence_threshold
            # Live SMC tunables: FVG mitigation depth + OB scan lookback
            fe = getattr(self, "feature_engine", None)
            if fe is not None:
                fe._fvg_mitigation_sensitivity = snap.fvg_mitigation_sensitivity
                fe._order_block_lookback_bars = snap.order_block_lookback_bars
            # News worker cadence (live-tunable where applicable)
            nw = getattr(self, "news_worker", None)
            if nw is not None and snap.news.worker_interval_sec > 0:
                nw.interval_sec = float(snap.news.worker_interval_sec)
            # News enabled toggle (Pro Hot Reload): hot-swap the worker/gate
            # next time _sync_runtime_config runs (either via apply or tick).
            desired_news = bool(snap.news.enabled)
            if desired_news != self._news_enabled:
                if desired_news:
                    try:
                        self._start_news_engine_from_snapshot(snap)
                        logger.info(
                            "[NEWS] event=HOT_RELOAD_ENABLED runtime_version=%d", snap.version
                        )
                    except Exception as ne:
                        logger.error("[NEWS] event=HOT_RELOAD_ENABLE_FAILED error=%s", ne)
                else:
                    try:
                        self._stop_news_engine_hot()
                        logger.info(
                            "[NEWS] event=HOT_RELOAD_DISABLED runtime_version=%d", snap.version
                        )
                    except Exception as ne:
                        logger.error("[NEWS] event=HOT_RELOAD_DISABLE_FAILED error=%s", ne)
            # News Auto Analysis (local deterministic, no API key) — live-tunable
            desired_auto = bool(getattr(snap.news, "auto_analysis_enabled", False))
            if desired_auto != getattr(self, "_news_auto_analysis_enabled", False):
                self._news_auto_analysis_enabled = desired_auto
                # propagate to worker gate (cheap, no restart)
                nw2 = getattr(self, "news_worker", None)
                if nw2 is not None and hasattr(nw2, "auto_analysis_enabled"):
                    nw2.auto_analysis_enabled = desired_auto
                logger.info(
                    "[NEWS_AUTO] event=HOT_RELOAD_TOGGLE enabled=%s runtime_version=%d",
                    desired_auto,
                    snap.version,
                )
            else:
                # keep worker in sync every tick (handles worker reconstructed)
                nw2 = getattr(self, "news_worker", None)
                if nw2 is not None and hasattr(nw2, "auto_analysis_enabled"):
                    nw2.auto_analysis_enabled = desired_auto
            # Rule matrix cache TTL (live-tunable; the engine uses
            # refresh_cache(force) with the TTL as a default — the attr
            # is set when the engine reads it each refresh)
            rm = getattr(self, "rule_matrix", None)
            if rm is not None and snap.rule_matrix.cache_ttl_seconds > 0:
                if hasattr(rm, "cache_ttl_seconds"):
                    rm.cache_ttl_seconds = float(snap.rule_matrix.cache_ttl_seconds)
        except Exception:
            logger.exception("[RUNTIME_CONFIG] service re-sync failed (isolated)")

    def apply_runtime_update(
        self,
        updates: dict,
        *,
        source: str = "WEB_UI",
        actor: str = "web",
    ) -> Any:
        """Apply a configuration update through the authoritative store.

        Returns a ConfigurationApplyReport (success / persisted / applied /
        version). The web layer and tests call this instead of rewriting
        live.yaml and hand-patching fields.
        """
        report = self.runtime_config.apply(updates, source=source, actor=actor)
        if report.success:
            self._sync_runtime_config()
        return report

    # -------------------------
    # Hot-path tick pipeline
    # -------------------------

    def _warm_liquidity_from_bars(
        self,
        bars: list[Any],
        *,
        atr: float | None = None,
        source: Any = None,
    ) -> None:
        """Causal-safe liquidity snapshot from COMPLETED bars.

        Called on every new-bar cadence (including during warmup, so the
        LiquidityGovernor never stays UNAVAILABLE/NOT_RUN/INVALID once
        bars exist) and after a broker reseed / cold-start warm from the
        seeded history. Pure numpy, no I/O, no DB, no execution authority
        (INV-020: liquidity is information-only). A failure is isolated and
        logged; it never disturbs trading and never fabricates a state.
        """
        gov = getattr(self, "liquidity_governor", None)
        if gov is None or not getattr(gov, "enabled", False):
            return
        if not bars:
            return
        try:
            from nexus_scalp.features.liquidity_runtime import SourceKind

            last = bars[-1]
            mid = float(getattr(last, "close", 0.0) or 0.0)
            decision_at = getattr(last, "timestamp", None)
            use_atr = float(atr) if (atr is not None and float(atr) > 0) else 1.5
            src = source if source is not None else SourceKind.LIVE_MARKET_STATE
            gov.compute_from_engine(
                bars=bars,
                mid_price=mid,
                atr=use_atr,
                decision_at=decision_at,
                source=src,
            )
        except Exception as liq_exc:  # isolated; trading unaffected
            logger.warning(
                "[LIQUIDITY] event=WARM_COMPUTE_FAILED error=%s (isolated; trading unaffected)",
                liq_exc,
            )

    def _process_tick_pipeline(self, tick: TickData, account: AccountInfo) -> None:
        try:
            # RUNTIME CONFIGURATION: re-sync services each tick. This is
            # cheap (two attribute assignments from an immutable snapshot)
            # and guarantees a UI save is reflected on the very next
            # evaluation without restarting or reading the DB per tick.
            self._sync_runtime_config()

            is_new_bar = self.aggregator.process_tick(tick)

            # cap bars (O(1) amortized)
            if len(self.aggregator._completed_bars) > 4000:
                self.aggregator._completed_bars = self.aggregator._completed_bars[-4000:]

            completed_bars = self.aggregator.get_completed_bars()
            fv = self.feature_engine.compute_from_bars(
                completed_bars=completed_bars, current_tick=tick
            )
            # TASK-02-70D-INTEGRATION: liquidity snapshot from COMPLETED bars.
            # BUG-169 (2026-08-31, live latency forensics): the governor is
            # IDEMPOTENT per completed-bar series — its only inputs are the
            # bars + their last close + the bar ATR, none of which change
            # between new bars. Recomputing it on EVERY tick burned
            # p50=67ms / p95=655ms / p99=982ms (max 5.0s) of the LOOP THREAD
            # per call (~12.5k calls/day), which was the dominant source of
            # the slow/sticky live decision loop (measured 2026-08-31 log).
            # Now: compute only on a new M1 bar (or first availability), and
            # else reuse the last snapshot. Information-freshness is
            # unchanged (the inputs literally cannot change between bars);
            # INV-020 (information-only, failure-isolated) still holds.
            if completed_bars:
                _liq_new_bar = is_new_bar or (
                    self.liquidity_governor is not None
                    and self.liquidity_governor.last_snapshot is None
                )
                if _liq_new_bar:
                    self._warm_liquidity_from_bars(
                        completed_bars,
                        atr=float(getattr(fv, "atr_m1", 0.0) or 0.0),
                    )

            if is_new_bar and completed_bars:
                self._on_new_bar(tick=tick, fv=fv, last_bar=completed_bars[-1])

            # Regime state (Module 1)
            # BUG-169: skip RE-EVALUATION for a duplicate tick (identical
            # bid/ask + timestamp). The metrics are functionally idempotent,
            # but classify_tick() PUSHES the duplicate into its rolling
            # rings (_ts/_log_ret/_ofi), double-counting it and skewing
            # tick_velocity + rv_5m + norm_ofi. This duplicates the dedup
            # predicate from SignalPolicy._evaluate_duplicate_tick on
            # purpose: the classifier must stay a pure per-tick consumer.
            _tick_dupe = tick.timestamp == getattr(self, "_regime_last_ts", None) or (
                float(tick.bid) == getattr(self, "_regime_last_bid", 0.0)
                and float(tick.ask) == getattr(self, "_regime_last_ask", 0.0)
                and float(tick.bid) > 0.0
            )
            if _tick_dupe:
                regime_state: MarketRegimeState = getattr(
                    self, "_regime_last_state", None
                ) or self.regime_classifier.classify_tick(
                    current_tick=tick,
                    is_macro_news_window=False,
                )
                # BUG-TDF-Q2 (TDF-R2 Q2/Q2b): a frozen/duplicate quote
                # stream can keep the reused state alive indefinitely.
                # Alarm-only freshness guard (BUG-169 dedup contract
                # preserved: duplicates are never re-pushed into the
                # classifier's rolling rings).
                self._assert_regime_state_freshness(tick=tick)
            else:
                regime_state = self.regime_classifier.classify_tick(
                    current_tick=tick,
                    is_macro_news_window=False,
                )
                self._regime_last_ts = tick.timestamp
                self._regime_last_bid = float(tick.bid)
                self._regime_last_ask = float(tick.ask)
                self._regime_last_state = regime_state
                # BUG-TDF-Q2: stamp when the cached state was last
                # PROVEN fresh by a successful classify_tick() call.
                self._regime_state_classified_at = time.time()

            # Manage open positions
            # NOTE (Phase 15 exit audit): `probs` and `regime_state` are threaded
            # into position management so the in-trade exit evaluation sees the
            # CURRENT model state and CURRENT regime. Previously the call omitted
            # both, which (a) disabled the AI direction-flip exit and (b) degraded
            # the adaptive evidence scores to static heuristics on the live path.
            # When inference is blocked by the warmup gate we still manage
            # positions (protective stops must never pause) but with probs=None.
            probs_for_mgmt = None
            if self._inference_enabled and self.warmup_state == "READY":
                try:
                    probs_for_mgmt = self._infer_probabilities(fv=fv)
                except Exception as infer_err:
                    logger.error(
                        "[INFERENCE] in-trade inference failed (isolated, positions still managed)",
                        error=str(infer_err),
                    )
                    probs_for_mgmt = None
            active_positions = self.order_manager.manage_active_positions(
                symbol=tick.symbol,
                current_tick=tick,
                feature_vector=fv,
                symbol_info=self._symbol_info,
                account=account,
                probs=probs_for_mgmt,
                regime_state=regime_state,
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

                    # Fail closed: with no inference (cold warmup or disabled)
                    # there must never be a trade decision, so a NO_TRADE proposal
                    # keeps the downstream pipeline contracts satisfied.
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

            # Inference (already computed for position management above; reuse it so the
            # model runs once per tick)
            if probs_for_mgmt is None and self._inference_enabled and self.warmup_state == "READY":
                probs = self._infer_probabilities(fv=fv)
            else:
                probs = probs_for_mgmt

            # Heartbeat radar logging: On EVERY M1 Bar completion or every 10 seconds of active ticks, force log.
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

            # =================================================================
            # PHASE 12: NEWS INTELLIGENCE GATE (bounded, isolated, optional)
            # -----------------------------------------------------------------
            # Applies a BOUNDED confidence adjustment from the current news
            # context. News can NEVER force a direction: alignment gives at
            # most max_confidence_boost (default 0.05), conflict lowers
            # confidence by at most max_confidence_penalty (default 0.10).
            # Position-protection actions are never gated; when the news
            # subsystem is disabled/unavailable this is a pure no-op.
            # =================================================================
            if self._news_enabled and self.news_gate is not None:
                try:
                    news_ctx = self.news_engine.current_context()
                    news_verdict = self.news_gate.evaluate(
                        context=news_ctx,
                        proposal_action=(
                            proposal.action.value
                            if hasattr(proposal.action, "value")
                            else str(proposal.action)
                        ),
                        strategy_direction=self._news_strategy_direction(proposal),
                        proposal_confidence=float(getattr(proposal, "confidence", 0.0) or 0.0),
                        regime_aligned=True,
                    )
                    self._last_news_gate = news_verdict
                    adjustment = news_verdict.confidence_adjustment
                    if adjustment != 0.0:
                        proposal = proposal.model_copy(
                            update={
                                "confidence": round(
                                    max(0.0, min(1.0, proposal.confidence + adjustment)), 4
                                )
                            }
                        )
                    logger.debug(
                        "[NEWS_GATE] decision=%s strategy=%s adjustment=%+.4f",
                        news_verdict.decision,
                        news_verdict.strategy_direction,
                        adjustment,
                    )
                except Exception as news_gate_err:
                    # News must never disturb trading: failure = no-op.
                    self._last_news_gate = None
                    logger.debug(
                        "[NEWS_GATE] event=FAILED (isolated, no-op)", error=str(news_gate_err)
                    )

            self.audit.log_signal(proposal)

            # =================================================================
            # BUG-169: TERMINAL OUTCOME FOR PRE-DISPATCH REJECTIONS.
            # -----------------------------------------------------------------
            # The Phase 08/09 gates convert an ENTRY proposal to NO_TRADE
            # BEFORE any dispatch. The experience row for that decision was
            # already written (_record_decision_experience), so without a
            # terminal outcome it hangs in the ledger as MISSING_OUTCOME
            # forever (295 rows / 22k log lines on 2026-08-31). Emit an
            # explicit NOT_DISPATCHED outcome for entry proposals that the
            # pre-trade stack rejected. Idempotent via the ledger's unique
            # key; failure-isolated (learning never disturbs trading).
            # =================================================================
            if (
                proposal.action == ActionType.NO_TRADE
                and str(getattr(proposal, "model_action", "") or "") != "NO_TRADE"
                and proposal.decision_stage
                in ("EXPERIENCE_INTELLIGENCE_GATE", "TRADE_INTELLIGENCE_GATE")
                and self.experience_engine is not None
            ):
                try:
                    from nexus_scalp.execution.terminal_outcome import (
                        emit_terminal_pending_outcome,
                    )
                    from nexus_scalp.experience.lifecycle import (
                        DecisionLifecycle as DecisionLifecycleAlias,
                    )

                    emit_terminal_pending_outcome(
                        experience_engine=self.experience_engine,
                        request_id=str(getattr(proposal, "request_id", "") or ""),
                        state=DecisionLifecycleAlias.NOT_DISPATCHED,
                        detail=f"pre-dispatch gate rejection: {proposal.rejection_reason or proposal.reason_code}",
                    )
                except Exception as _term_err:
                    logger.debug(
                        "[TERMINAL_OUTCOME] pre-dispatch emission skipped",
                        error=str(_term_err),
                    )

            # =====================================================================
            # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: SAFETY FRESHNESS GATE
            # ---------------------------------------------------------------------
            # Runs AFTER all model/experience/news/intelligence gates. If the
            # feature->inference->decision chain is proven STALE (frozen), the
            # proposal is converted to NO_TRADE / BLOCKED_BY_STALE so a frozen
            # intelligence state can NEVER masquerade as a live BUY/SELL.
            # is a pure downgrade to NO_TRADE; it relaxes NO existing guard and
            # fabricates NO confidence. It is the only production touchpoint of
            # the freshness model.
            # =====================================================================
            proposal, _fresh_blocked = self.live_freshness_gate(proposal)
            if _fresh_blocked:
                logger.warning(
                    "[FRESHNESS_GATE] event=BLOCKED reason=BLOCKED_BY_STALE "
                    "(inference chain frozen; proposal downgraded to NO_TRADE)"
                )

            # =====================================================================
            # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: FRESHNESS INSTRUMENTATION
            # ---------------------------------------------------------------------
            # Purely OBSERVATIONAL bookkeeping at the live sync point. Records
            # the authoritative stage timestamps, bumps monotonic sequence ids
            # ONLY when the substantive input/output actually changed (so the
            # UI/QA can prove inference progressed without trusting
            # state_version), and stores change-detection hashes. This does NOT
            # gate or block trading; gates live in `live_freshness_gate()`.
            # =====================================================================
            import hashlib

            now_utc = datetime.now(UTC)
            # Monotonic tick timestamp: strictly increasing ms of the newest
            # market tick on the live path.
            tick_ms = int(tick.timestamp.timestamp() * 1000.0)
            if tick_ms > self._monotonic_tick_ms:
                self._monotonic_tick_ms = tick_ms
                self._last_tick_timestamp = tick.timestamp
                self._tick_sequence += 1
                self._market_updates_total += 1
            # Deterministic raw-market hash (price/spread/regime, NOT timestamp)
            raw_market = (
                f"{tick.bid:.5f}|{tick.ask:.5f}|{tick.last:.5f}|"
                f"{getattr(tick, 'spread', '')}|{regime_state}"
            )
            raw_market_hash = hashlib.sha1(raw_market.encode()).hexdigest()[:16]
            # Feature change detection
            feat_vals = list(getattr(fv, "to_tensor_input", lambda: [])())
            feature_hash = hashlib.sha1(
                ("|".join(f"{v:.6g}" for v in feat_vals)).encode()
            ).hexdigest()[:16]
            self.last_feature_update = now_utc
            self._feature_builds_total += 1
            if feature_hash != self._last_feature_hash:
                self._feature_sequence += 1
                self._last_feature_hash = feature_hash
            # Model input + output change detection
            with self._bundle_lock:
                _b = self._bundle
            try:
                if _b is not None:
                    x_np = np.array(feat_vals, dtype=np.float32).reshape(1, -1)
                    x_scaled = _b.scaler.transform(x_np)
                    model_input_hash = hashlib.sha1(x_scaled.tobytes()).hexdigest()[:16]
                else:
                    model_input_hash = ""
            except Exception:
                model_input_hash = ""
            probs_list = probs.cpu().numpy().flatten().tolist() if probs is not None else []
            model_output_hash = hashlib.sha1(
                ("|".join(f"{v:.8g}" for v in probs_list)).encode()
            ).hexdigest()[:16]
            self.last_inference_timestamp = now_utc
            self.last_successful_inference = now_utc
            self._inference_runs_total += 1
            if model_input_hash and model_input_hash != self._last_model_input_hash:
                self._inference_sequence += 1
                self._last_model_input_hash = model_input_hash
            if model_output_hash != self._last_model_output_hash:
                self._last_model_output_hash = model_output_hash
            self._last_raw_market_hash = raw_market_hash
            # Decision stage
            self.last_decision_timestamp = getattr(proposal, "generated_at", now_utc)
            self._decision_updates_total += 1
            self._decision_sequence += 1

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

            # =================================================================
            # TASK-05-70D-SHADOW: 70D OBSERVATION HOOK (observability ONLY)
            # -----------------------------------------------------------------
            # Independent of the 50D shadow gate (BUG-105): runs on EVERY tick
            # once a validated 70D candidate is attached and enabled, building
            # the 70D vector from the SAME canonical state (50D + news +
            # liquidity). A failure here is isolated (INV-018).
            # =================================================================
            self._record_shadow70_observation(
                tick=tick,
                fv=fv,
                proposal=proposal,
            )

            # (Liquidity governor is pre-warmed on every tick/new-bar above)

            # Extract and update real SMC overlays for the live chart canvas.
            # Recomputed ONLY when the completed-bar series changes (new bar)
            # or on the first tick; between bars the series cannot change, so
            # the O(n) extraction + 900-bar serialization is CACHED (measured
            # ~6-7ms/tick at 900 bars vs ~0 for the cached path).
            if getattr(self, "server_state", None) is not None:
                snapshot_key = completed_bars[-1].timestamp if completed_bars else None
                # Also refresh on a 10s cadence so the forming bar's live
                # OHLC updates reach the UI even without a bar close.
                if (
                    self._last_chart_snapshot_key is None
                    or snapshot_key != self._last_chart_snapshot_key
                    or (time.time() - self._last_chart_snapshot_time) >= 10.0
                ):
                    real_overlays = self.signal_policy.extract_live_chart_overlays(
                        completed_bars=completed_bars, atr_val=fv.atr_m1
                    )
                    bars_list = []
                    for b in completed_bars[-900:]:
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
                    self._last_chart_snapshot_key = snapshot_key
                    self._last_chart_snapshot_bars = bars_list
                    self._last_chart_snapshot_overlays = real_overlays
                    self._last_chart_snapshot_time = time.time()
                    self.server_state.update_live_visuals(bars_list, real_overlays)
            policy_decision = proposal
            # =================================================================
            # BUG-212: SHADOW EXECUTION BOUNDARY (observation-only mutations).
            # -----------------------------------------------------------------
            # SHADOW means "live data, live prediction, NO execution". The
            # position-management pass above keeps running (protective
            # observation), but this engine must never MUTATE broker state
            # from the decision path: entries, lifecycle actions, AI
            # reversals and intelligent hedges are all downgraded to logged
            # NO_TRADE observations before any order authority is consulted.
            # The proposal itself stays recorded (audit + experience ledger
            # see the full counterfactual), so shadow evidence is preserved.
            # =================================================================
            if (
                self.config.execution.mode == ExecutionMode.SHADOW
                and policy_decision.action != ActionType.NO_TRADE
            ):
                _shadow_action = policy_decision.action
                policy_decision = proposal.model_copy(
                    update={
                        "action": ActionType.NO_TRADE,
                        "reason_code": "SHADOW_OBSERVATION_ONLY",
                        "rejection_reason": (
                            f"SHADOW mode is observation-only: {_shadow_action.value} suppressed"
                        ),
                        "final_action": "NO_TRADE",
                        "is_ai_reversal": False,
                        "reversal_action": None,
                    }
                )
                logger.info(
                    "[SHADOW_BOUNDARY] event=ORDER_MUTATION_SUPPRESSED "
                    "suppressed_action=%s ticket=%s",
                    _shadow_action.value,
                    getattr(proposal, "ticket", 0) or 0,
                )
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
                        # SETUP SNAPSHOT (2026-08-18): capture the full chart-state
                        # fingerprint the AI saw at dispatch (HTF/SMC/ICT structure,
                        # displacement, sessions, guardian) and attach it to the
                        # entry context so the closed-trade autopsy can attribute
                        # every trade to its exact setup.
                        setup_snapshot: dict = {}
                        try:
                            fv_snap = fv
                            session = (
                                "".join(
                                    seg
                                    for seg, flag in (
                                        ("tokyo", bool(getattr(fv_snap, "session_tokyo", False))),
                                        ("london", bool(getattr(fv_snap, "session_london", False))),
                                        ("ny", bool(getattr(fv_snap, "session_ny", False))),
                                        (
                                            "ov",
                                            bool(
                                                getattr(fv_snap, "session_overlap_london_ny", False)
                                            ),
                                        ),
                                    )
                                    if flag
                                )
                                or "?"
                            )
                            setup_snapshot = {
                                "execution_mode": str(
                                    getattr(policy_decision, "execution_mode", "")
                                ),
                                "model_action": str(getattr(policy_decision, "model_action", "")),
                                "htf_score": float(
                                    getattr(policy_decision, "htf_score", 0.0) or 0.0
                                ),
                                "smc_score": float(
                                    getattr(policy_decision, "smc_score", 0.0) or 0.0
                                ),
                                "conf_before": float(
                                    getattr(policy_decision, "confidence_before_filters", 0.0)
                                    or 0.0
                                ),
                                "conf_after": float(
                                    getattr(policy_decision, "confidence_after_filters", 0.0) or 0.0
                                ),
                                "buy_prob": float(
                                    getattr(policy_decision, "buy_probability", None) or 0.0
                                ),
                                "sell_prob": float(
                                    getattr(policy_decision, "sell_probability", None) or 0.0
                                ),
                                "disp": float(
                                    getattr(fv_snap, "live_tick_displacement", 0.0) or 0.0
                                ),
                                "atr": float(getattr(fv_snap, "atr_m1", 0.0) or 0.0),
                                "trend": float(getattr(fv_snap, "trend_strength", 0.0) or 0.0),
                                "sweep_sig": int(
                                    getattr(fv_snap, "liquidity_sweep_signal", 0) or 0
                                ),
                                "ob_type": int(getattr(fv_snap, "order_block_type", 0) or 0),
                                "fvg_bull": bool(getattr(fv_snap, "fvg_bullish_active", False)),
                                "fvg_bear": bool(getattr(fv_snap, "fvg_bearish_active", False)),
                                "choch_bull": bool(getattr(fv_snap, "choch_bullish", False)),
                                "choch_bear": bool(getattr(fv_snap, "choch_bearish", False)),
                                "broke_high": bool(getattr(fv_snap, "broke_previous_high", False)),
                                "broke_low": bool(getattr(fv_snap, "broke_previous_low", False)),
                                "z_score": float(
                                    getattr(fv_snap, "cross_asset_z_score", 0.0) or 0.0
                                ),
                                "h4": float(getattr(fv_snap, "htf_h4_trend", 0.0) or 0.0),
                                "h1": float(getattr(fv_snap, "htf_h1_momentum", 0.0) or 0.0),
                                "m30": float(getattr(fv_snap, "htf_m30_structure", 0.0) or 0.0),
                                "m15": float(getattr(fv_snap, "htf_m15_confirmation", 0.0) or 0.0),
                                "session": session,
                                "guardian": str(getattr(policy_decision, "guardian_status", "")),
                                "rr": float(
                                    getattr(policy_decision, "risk_reward_ratio", 0.0) or 0.0
                                ),
                            }
                        except Exception as snap_err:
                            logger.warning("[ENTRY] setup snapshot failed", error=str(snap_err))
                        success = self.order_manager.dispatch_order(
                            policy_decision, dynamic_volume, setup_snapshot=setup_snapshot
                        )
                        logger.info(
                            f"[info] DISPATCH ORDER action={policy_decision.action.value} price={policy_decision.proposed_entry} volume={dynamic_volume}"
                        )

                        if success:
                            risk_usd = account.equity * (
                                self.config.risk.risk_per_trade_pct / 100.0
                            )
                            with contextlib.suppress(Exception):
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
                decision_ctx, trade_id, experience_id = self._position_decision_context(
                    pos.ticket, pos.symbol
                )
                self.intelligence_lifecycle.observe_position(
                    ticket=pos.ticket,
                    snapshot=snapshot,
                    performance=perf,
                    market=market,
                    decision=decision_ctx,
                    trade_id=trade_id,
                    experience_id=experience_id,
                    at=tick.timestamp,
                )
        except Exception as obs_err:
            logger.error("[POSITION_TRACK] observation failed (isolated)", error=str(obs_err))
            self.emit_incident_telemetry(
                event_type="POSITION_TRACK_FAILED",
                component="execution",
                severity="MEDIUM",
                correlation_id="position-track",
            )

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

    def _position_decision_context(
        self, ticket: int, symbol: str
    ) -> tuple[DecisionContext, str, str]:
        """Resolves the decision identity that produced this position, if known.

        Returns (decision_context, trade_id, experience_id). The order id
        (when bound) IS the canonical trade/execution identity: it is
        propagated into the immutable lifecycle timeline so every event can
        be correlated back to its decision (TASK-3 / BUG-086).
        """
        try:
            om = self.order_manager
            strategy_id = om._entry_reasons.get(ticket, "")
            order_id = om._entry_order_ids.get(ticket, "")
            feature_schema = self.FEATURE_SCHEMA_ID
            ctx = DecisionContext(
                strategy_id=strategy_id or f"unknown_{symbol}",
                strategy_version="1.0.0",
                feature_schema_id=feature_schema,
                model_version=str(getattr(self.config.model, "feature_schema_version", "v1.0")),
                confidence=float(om._entry_confidences.get(ticket, 0.0)),
                probability=float(om._entry_confidences.get(ticket, 0.0)),
            )
            trade_id = order_id or ""
            experience_id = ""  # resolved by the outcome layer, not known at open
            return ctx, trade_id, experience_id
        except Exception:
            return DecisionContext(), "", ""

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
                    # BUG-212: SHADOW observation-only — an intelligent hedge
                    # is an order mutation; log the counterfactual and skip
                    # the broker write.
                    if self.config.execution.mode == ExecutionMode.SHADOW:
                        logger.info(
                            "[SHADOW_BOUNDARY] event=ORDER_MUTATION_SUPPRESSED "
                            "suppressed_action=HEDGE_LIMIT original_ticket=%s",
                            pos.ticket,
                        )
                        continue
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
                        with contextlib.suppress(Exception):
                            self.notifier.notify_generic_message(
                                title="Intelligent Hedging Activated",
                                message=(
                                    f"Position {pos.ticket} is in drawdown (Hold Score: {hold_score}). "
                                    f"Placed hedging order {hedge_order.order_type.value} of "
                                    f"{hedge_order.volume} lots at {hedge_order.price}."
                                ),
                            )

    def _on_new_bar(self, tick: TickData, fv, last_bar) -> None:
        # BUG-061: candle-close gate — feed the completed bar into the local
        # candle-intelligence subsystem and capture its decision (entry/hold/
        # fast-exit bias). Failure is isolated; never disturbs the tick path.
        ci = getattr(self, "candle_intel", None)
        if ci is not None:
            try:
                regime_name = getattr(self._last_regime_state, "regime_type", None)
                regime_name = getattr(regime_name, "value", "UNKNOWN") if regime_name else "UNKNOWN"
                regime_state = RegimeState(
                    symbol=tick.symbol,
                    timeframe="M1",
                    timestamp=last_bar.timestamp,
                    regime=str(regime_name),
                    atr=float(getattr(fv, "atr_m1", 0.0) or 0.0),
                    spread=float(max(0.0, tick.ask - tick.bid)),
                )
                out = ci.ingest_bar(
                    symbol=tick.symbol,
                    timeframe="M1",
                    timestamp=last_bar.timestamp,
                    open_=float(last_bar.open),
                    high=float(last_bar.high),
                    low=float(last_bar.low),
                    close=float(last_bar.close),
                    volume=float(getattr(last_bar, "tick_volume", 0.0) or 0.0),
                    is_complete=True,
                    regime_state=regime_state,
                    holding_position=bool(self.order_manager._position_states),
                )
                self._last_candle_decision = out.to_dict() if out else None
            except Exception as ci_err:
                logger.error("[CANDLE_INTEL] bar feed failed (isolated)", error=str(ci_err))

        # ---------------------------------------------------------------------
        # Build the canonical 50D feature record for THIS bar (always available,
        # independent of MSLIE). Used by the Market Radar detector below and the
        # rolling retrain buffer. NOTE: rec must be defined BEFORE the radar block
        # (BUG-139: prior nesting inside the mslie_engine conditional left `rec`
        # unbound when mslie_engine was None -> BAR_DETECT_FAILED).
        # BUG-185 PART-3: the canonical per-bar retrain record is built by the
        # shared builder — full scalp_v3 geometry when a 70D champion serves
        # (Base 0..49 | News 50..59 | Liquidity 60..69), 50D base otherwise.
        # The builder REFUSES (None) when the real liquidity snapshot is not
        # VALID — never zero-fills — and its width guard turns any residual
        # contract split into a structured FEATURE_CONTRACT_MISMATCH (SKIP),
        # not a raw IndexError.
        rec = self._build_retrain_record(
            base50=fv.to_tensor_input(),
            fv=fv,
            bar=last_bar,
            spread=(tick.ask - tick.bid),
            context="new_bar_record",
        )
        if rec is None:
            # 70D record refused (liquidity not VALID yet): keep the legacy
            # 50D observability record so the radar/UI keep working; it is
            # simply NOT appended to the retrain buffer by this path (the
            # width guard below keeps starvation loud).
            rec = {
                f"feat_{i}": float(v)
                for i, v in enumerate(
                    self._validate_50d_tensor(
                        fv.to_tensor_input(), context="new_bar_record_fallback_50d"
                    )
                )
            }
            rec.update(
                close=last_bar.close,
                high=last_bar.high,
                low=last_bar.low,
                open=last_bar.open,
                spread=(tick.ask - tick.bid),
                atr_m1=fv.atr_m1,
            )
        if self._governance_reference_vector is None:
            _ref = self._validate_50d_tensor(fv.to_tensor_input(), context="governance_reference")
            self._governance_reference_vector = [float(v) for v in _ref]

        # ---------------------------------------------------------------------
        # Market Radar (Hunter SetupDetector) - live, bar-close cadence (BUG-138).
        # Runs on the SAME completed-bar feature record as the sample-maker uses,
        # but here for the LIVE path. Pure + causal; failure-isolated. Stores the
        # ranked setup list as _last_market_radar for the Intel Hub / Web Panel.
        try:
            radar_rec = rec
            if "feat_0" not in radar_rec:
                radar_rec = (
                    self._rolling_feature_records[-1] if self._rolling_feature_records else None
                )
            if radar_rec is not None:
                detected = self.setup_detector.detect(radar_rec, timestamp=last_bar.timestamp)
                ranked = sorted(detected, key=lambda s: s.quality, reverse=True)
                best = ranked[0] if ranked else None
                _regime_val = (
                    getattr(getattr(self._last_regime_state, "regime_type", None), "value", None)
                    or "UNKNOWN"
                )
                _news_state_val = None
                try:
                    if getattr(self, "news_engine", None) is not None:
                        _nc = self.news_engine.current_context()
                        if _nc is not None:
                            _ns = getattr(_nc, "state", None)
                            _news_state_val = getattr(_ns, "value", None) or str(_ns)
                except Exception:
                    _news_state_val = None
                self._last_market_radar = {
                    "symbol": tick.symbol,
                    "timestamp": last_bar.timestamp.isoformat(),
                    "bar_timestamp": last_bar.timestamp.isoformat(),
                    "regime": str(_regime_val),
                    "candidate_count": len(ranked),
                    "best_setup": best.to_contract() if best else None,
                    "setups": [s.to_contract() for s in ranked[:5]],
                    "state": (
                        "SETUP_READY"
                        if best and best.quality >= self.setup_detector.min_quality
                        else ("WATCHING" if ranked else "NO_SETUP")
                    ),
                    "news_state": _news_state_val,
                    "decision_reason": self._last_proposal.reason_code
                    if getattr(self, "_last_proposal", None)
                    else None,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
        except Exception as radar_err:
            logger.warning("[RADAR] event=BAR_DETECT_FAILED error=%s", radar_err)

        # ---------------------------------------------------------------------
        # MSLIE: market perception on the bar-close cadence (pure numpy, no
        # I/O, no DB — INV-001). The engine produces the structured
        # MarketIntelligenceFeatureVectorV1 for the debug UI / AI models.
        # Failure is isolated: perception can never disturb the tick path.
        # =====================================================================
        ms = getattr(self, "mslie_engine", None)
        if ms is not None:
            try:
                completed_bars = self.aggregator.get_completed_bars()
                if completed_bars:
                    vector = ms.analyze_market(
                        completed_bars,
                        decision_at=last_bar.timestamp,
                        mid_price=float(tick.bid),
                        atr=float(getattr(fv, "atr_m1", 0.0) or 0.0),
                    )
                    self._last_mslie_vector = vector
            except Exception as ms_err:
                logger.warning(
                    "[MSLIE] event=BAR_FEED_FAILED error=%s (isolated; trading unaffected)",
                    ms_err,
                )

        self._rolling_feature_records.append(rec)
        self._bars_since_last_retrain += 1

        # BUG-169: width guard for the online fine-tune path. The buffer
        # records carry the 50D tensor (feat_0..feat_49, class contract);
        # feeding them to a 70-input model head crashed with
        # "mat1 and mat2 shapes cannot be multiplied (10x50 and 70x128)" on
        # EVERY retrain window while the 70D champion was loaded (60
        # failures on 2026-08-31) and each crash burned a scaler-save
        # attempt against the artifact the engine holds (WinError 5).
        # Gate: fine-tune only when the trainer's bound width matches the
        # actual record width; the __init__ rebind covers the 70D case
        # via FEATURE_COLS on the effective contract.
        # BUG-185: a record width that disagrees with the rebound trainer
        # width is a CONTRACT SPLIT (buffer built 50D vs trainer bound 70D),
        # not a routine case - surface it loudly once per hour instead of
        # silently starving the 70D online-learning loop.
        if len(rec) - 6 != self.trainer.num_features or getattr(
            self, "_online_train_disabled", False
        ):
            if self._bars_since_last_retrain >= self._retrain_interval_bars and (
                not getattr(self, "_online_train_width_warn_at", 0.0)
                or time.time() - self._online_train_width_warn_at >= 3600.0
            ):
                self._online_train_width_warn_at = time.time()
                # BUG-185: CRITICAL, not WARNING - this split starves the
                # online-learning loop for the loaded contract entirely.
                logger.critical(
                    "[ONLINE_TRAIN] SKIPPED width-contract split "
                    "record_width=%s trainer_width=%s (BUG-169 guard, BUG-185 "
                    "record-contract violation - buffer builder did not follow "
                    "the loaded bundle contract)",
                    len(rec) - 6,
                    self.trainer.num_features,
                )
            return

        # AGENT-8 BUG-243 (runtime forensics 2026-09-05): defensive row-width
        # filter immediately before the DataFrame materialization boundary.
        # The width guard above is check-then-use: the buffer is APPEND-ONLY
        # and a restart with a DIFFERENT champion width (50D <-> 70D hot-swap)
        # leaves mixed-width rows in the deque. polars unions heterogeneous
        # dicts BY NAME, materializing the missing columns as None (proven:
        # probe -> feat_50..feat_69 nulls), and neither _validate_training_frame
        # (labels only) nor _filter_trainable_rows (label_evaluated/is_purged
        # only) inspects feature nulls. The trainer's nan_to_num then silently
        # trains on zero-fabricated rows - the exact "invalid record becomes
        # zero-fill" class the record-builder invariant forbids, entering via
        # the DATAFRAME boundary instead of the builder. Guard = keep only
        # rows whose feat_* width matches the bound trainer contract.
        if self._rolling_feature_records:
            _widths = {
                sum(1 for k in r if str(k).startswith("feat_"))
                for r in self._rolling_feature_records
            }
            if len(_widths) > 1:
                _expected = int(self.trainer.num_features)
                _before = len(self._rolling_feature_records)
                self._rolling_feature_records = deque(
                    (
                        r
                        for r in self._rolling_feature_records
                        if sum(1 for k in r if str(k).startswith("feat_")) == _expected
                    ),
                    maxlen=_before,
                )
                logger.warning(
                    "[ONLINE_TRAIN] event=BUFFER_WIDTH_FILTER dropped=%s kept=%s "
                    "expected_width=%s widths_seen=%s (mixed-width rows would "
                    "have become None->0.0 fabrications in the training frame)",
                    _before - len(self._rolling_feature_records),
                    len(self._rolling_feature_records),
                    _expected,
                    sorted(_widths),
                )

        if (
            self._bars_since_last_retrain
            and len(self._rolling_feature_records) >= 300
            and not self._retrain_inflight
        ):
            try:
                loop = asyncio.get_running_loop()
                self._retrain_task = loop.create_task(self._trigger_async_online_fine_tune())
            except RuntimeError:
                pass

    def _validate_feature_vector(self, features: Sequence[float], context: str) -> list[float]:
        """Schema-gated validation dispatching to 50D or 70D gate."""
        eff = int(self.effective_feature_dim)
        if eff == 70 and len(features) == 70:
            from nexus_scalp.features.schema_contract import (
                feature_schema_hash,
                validate_70d_vector,
            )

            return validate_70d_vector(
                list(features), schema_hash=feature_schema_hash(), context=context
            )
        return self.__class__._validate_50d_tensor(features, context=context)

    def _build_live_feature_vector(self, fv) -> tuple[list[float], dict[str, float]]:
        """Assembles the canonical live tensor (50D or 70D) for this tick.

        50D CHAMPION (scalp_v1/50D): returns the 50D vector; liquidity is
        never injected. 70D CHAMPION (validated 70D model): assembles
        0..49 Base + 50..59 News + 60..69 Liquidity (causal, VALID only).
        STALE/INVALID liquidity raises so the caller can degrade safely.
        """
        import time as _time

        _t0 = _time.perf_counter()
        base50 = fv.to_tensor_input()
        base50 = self._validate_50d_tensor(base50, context="live_base50")
        _t_base = _time.perf_counter()

        eff_dim = int(self.effective_feature_dim)
        if eff_dim != 70:
            return base50, {
                "feature_ms": round((_t_base - _t0) * 1e3, 3),
                "liquidity_ms": 0.0,
                "news_ms": 0.0,
                "assembly_ms": 0.0,
            }

        # News 10D (indices 50..59): CANONICAL projection of the live context.
        # BUG-190 (fidelity audit): a raw CurrentNewsContext.model_dump() has
        # DIFFERENT key names than the canonical training-frame schema
        # (active_event_count vs active_high_impact_events, bullish_score/
        # bearish_score vs bullish/bearish_pressure, state-as-string vs
        # news_state encoding, novelty absent) - feeding it straight into
        # news_10d_from_context zeroes/loses 4 of 10 slots. The canonical
        # named mapping (vectorize_news_context -> build_news_10, the same
        # mapping shadow70 and the debug feature matrix already use) is the
        # single projection for live inference.
        news10: list[float]
        try:
            from nexus_scalp.shadow.shadow70.news_provider import build_news_10

            news_ctx = None
            if (
                getattr(self, "_news_enabled", False)
                and getattr(self, "news_engine", None) is not None
            ):
                try:
                    news_ctx = self.news_engine.current_context()
                except Exception:
                    news_ctx = None
            if news_ctx is None:
                news10 = [0.0] * 10
            else:
                from nexus_scalp.governance.alignment import vectorize_news_context

                news10, _ = build_news_10(vectorize_news_context(news_ctx))
        except Exception:
            news10 = [0.0] * 10
        _t_news = _time.perf_counter()

        # Liquidity 10D (indices 60..69): real, causal, causality-checked.
        liq10: list[float] | None = None
        gov = getattr(self, "liquidity_governor", None)
        if gov is not None:
            snap = getattr(gov, "last_snapshot", None)
            causal = getattr(gov, "causal_state", lambda: "INVALID")()
            if snap is not None and causal == "VALID":
                try:
                    vec = list(snap.features)
                    if len(vec) == 10 and all(-3.0 <= float(v) <= 3.0 for v in vec):
                        liq10 = [float(v) for v in vec]
                except Exception:
                    liq10 = None
        _t_liq = _time.perf_counter()

        if liq10 is None:
            raise RuntimeError(
                "70D inference requested but liquidity snapshot is not VALID "
                "(stale/missing) - refusing to feed fabricated values into the 70D model"
            )

        try:
            from nexus_scalp.features.liquidity_runtime import build_70d_vector

            vec70 = build_70d_vector(base50, family_10=news10, liquidity_10=liq10)
        except Exception as e:
            raise RuntimeError(f"70D assembly failed: {e}") from e
        _t_asm = _time.perf_counter()
        try:
            from nexus_scalp.features.schema_contract import (
                feature_schema_hash,
                validate_70d_vector,
            )

            validate_70d_vector(vec70, schema_hash=feature_schema_hash(), context="live_70d")
        except Exception as e:
            raise RuntimeError(f"70D contract validation failed: {e}") from e
        return vec70, {
            "feature_ms": round((_t_base - _t0) * 1e3, 3),
            "news_ms": round((_t_news - _t_base) * 1e3, 3),
            "liquidity_ms": round((_t_liq - _t_news) * 1e3, 3),
            "assembly_ms": round((_t_asm - _t_liq) * 1e3, 3),
        }

    # ==================================================================
    # NEXUS-LIVE-INFERENCE-FROZEN-STATE-G29: LIVE-FRESHNESS TRUTH MODEL
    # Delegates to LiveFreshnessService (Cluster 3 extraction).
    # ==================================================================

    def _build_freshness_snapshot(self):  # type: ignore[no-untyped-def]
        from nexus_scalp.application.live_freshness import LiveFreshnessSnapshot

        return LiveFreshnessSnapshot(
            freshness_max_age_sec=float(self._freshness_max_age_sec),
            last_tick_timestamp=self._last_tick_timestamp,
            last_feature_update=self.last_feature_update,
            last_inference_timestamp=self.last_inference_timestamp,
            last_decision_timestamp=self.last_decision_timestamp,
            tick_sequence=self._tick_sequence,
            feature_sequence=self._feature_sequence,
            inference_sequence=self._inference_sequence,
            decision_sequence=self._decision_sequence,
            monotonic_tick_ms=self._monotonic_tick_ms,
            last_raw_market_hash=self._last_raw_market_hash,
            last_feature_hash=self._last_feature_hash,
            last_model_input_hash=self._last_model_input_hash,
            last_model_output_hash=self._last_model_output_hash,
            market_updates_total=self._market_updates_total,
            feature_builds_total=self._feature_builds_total,
            inference_runs_total=self._inference_runs_total,
            inference_failures_total=self._inference_failures_total,
            decision_updates_total=self._decision_updates_total,
            stale_state_detected_total=self._stale_state_detected_total,
        )

    def _stage_freshness(
        self, stamp: datetime | None, max_age_sec: float
    ) -> tuple[str, float | None]:
        from nexus_scalp.application.live_freshness import LiveFreshnessService

        return LiveFreshnessService.stage_freshness(stamp, max_age_sec)

    def compute_live_freshness(self) -> dict[str, Any]:
        from nexus_scalp.application.live_freshness import LiveFreshnessService

        snap = self._build_freshness_snapshot()
        fresh = LiveFreshnessService().compute_freshness(snap)
        if fresh.get("overall") == "STALE":
            self._stale_state_detected_total += 1
            fresh["telemetry"]["stale_state_detected_total"] = self._stale_state_detected_total
        return fresh

    def live_freshness_gate(self, proposal: Any) -> tuple[Any, bool]:
        from nexus_scalp.application.live_freshness import LiveFreshnessService

        fresh = self.compute_live_freshness()
        if fresh.get("overall") != "STALE":
            return proposal, False
        self._stale_state_detected_total += 1
        out, blocked = LiveFreshnessService.gate_proposal(fresh, proposal)
        return out, blocked

    def diagnose_freshness(self) -> dict[str, Any]:
        from nexus_scalp.application.live_freshness import LiveFreshnessService

        snap = self._build_freshness_snapshot()
        return LiveFreshnessService.diagnose(
            snap,
            adapter=self.adapter,
            aggregator=self.aggregator,
            feature_engine=self.feature_engine,
            build_vector_fn=self._build_live_feature_vector,
            get_bundle_fn=lambda: self._bundle,
            run_inference_fn=self._run_inference_tensor,
            symbol=self.config.execution.symbol,
        )

    def _run_inference_tensor(self, x_scaled: Any) -> torch.Tensor:
        """Helper: run the model on an already-scaled tensor (diagnostic)."""
        import torch as _torch

        x = _torch.tensor(x_scaled, dtype=_torch.float32)
        x = _torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
        with self._bundle_lock:
            model = self._bundle.model
        return model(x)

    def _infer_probabilities(self, fv) -> torch.Tensor:
        import time as _time

        # --- honest staged latency trace (monotonic, TASK: latency forensics) ---
        from nexus_scalp.features.latency_tracer import LatencyStage, LatencyTracer

        _trace = LatencyTracer(prediction_id=f"inf_{_time.perf_counter_ns()}")
        _trace.mark(LatencyStage.T0_MARKET_EVENT)
        _trace.mark(LatencyStage.T1_FEATURE_START)

        # BUG-125: Canonical live tensor: 50D for the production Champion,
        # 70D when a validated 70D model is hot-swapped. Assembly does
        # per-family telemetry bookkeeping and validates the liquidity snapshot.
        try:
            x_vec, asm_timings = self._build_live_feature_vector(fv)
            self._last_live_tensor_dim = len(x_vec)
            self._last_live_tensor_schema = self.effective_feature_schema_id
            self._last_70d_assembly_timings = asm_timings
        except RuntimeError as asm_err:
            if int(self.effective_feature_dim) == 70:
                # OBS-PERF-RESILIENCE: a 70D assembly failure BLOCKS inference
                # for this tick. That DEGRADED->BLOCKED transition must be
                # visible in telemetry, not only in a log line: bump the
                # failure gauge and emit an incident event (bounded by the
                # incident pipeline's own rate limiting).
                self._inference_failures_total = getattr(self, "_inference_failures_total", 0) + 1
                self.emit_incident_telemetry(
                    event_type="INFERENCE_BLOCKED_70D_ASSEMBLY",
                    component="inference",
                    error_code="FEATURE_UNAVAILABLE",
                    severity="HIGH",
                    correlation_id="tick-pipeline",
                )
                logger.warning(
                    "[INFERENCE] 70D assembly failed - inference blocked for this tick",
                    error=str(asm_err),
                )
                self._last_70d_assembly_timings = {}
                self._last_live_tensor_dim = 70
                self._last_live_tensor_schema = self.effective_feature_schema_id
                raise
            # Non-70D defensive fallback
            logger.warning(
                "[INFERENCE] feature assembly failed - falling back to 50D", error=str(asm_err)
            )
            x_vec = self._validate_50d_tensor(
                fv.to_tensor_input(), context="live_inference_fallback_50d"
            )
            self._last_70d_assembly_timings = {}
            self._last_live_tensor_dim = len(x_vec)
            self._last_live_tensor_schema = "scalp_v1"
        _trace.mark(LatencyStage.T2_FEATURE_DONE)
        x_np = np.array(x_vec, dtype=np.float32).reshape(1, -1)

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            raise RuntimeError("Model bundle not initialized")

        x_np = bundle.scaler.transform(x_np)
        _trace.mark(LatencyStage.T3_SCALER_DONE)
        seq_x = None
        try:
            seq_x = self._maybe_build_live_sequence_tensor(
                x_scaled_now=x_np[0].tolist(), bar_ts=None
            )
        except Exception:
            seq_x = None
        if seq_x is not None:
            x = seq_x
        else:
            x = torch.tensor(x_np, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
        _trace.mark(LatencyStage.T4_TENSOR_DONE)

        # Debug/forensics: keep the exact model input the live path consumed
        # (post-scaler, pre-softmax). Read-only observability (INV-018);
        # never used for execution. SAMPLED (every 64th) to keep the hot
        # path allocation-free; full capture available in debug mode.
        # Debug/forensics input capture: sampled (every 64th) to keep
        # the hot path allocation-free; full capture in debug mode.
        _dbg_every = getattr(self, "_latency_dbg_every", 64) or 64
        try:
            if (self._inference_count % _dbg_every) == 0:
                self._last_model_input_tensor = x.detach().cpu().numpy().reshape(-1).tolist()
            else:
                self._last_model_input_tensor = None
        except Exception:
            self._last_model_input_tensor = None

        # HONEST Model Forward stage (T5..T6) — nothing else in between.
        _trace.mark(LatencyStage.T5_MODEL_START)
        bundle.model.eval()
        # Latency fix: intra-op multithreading on a 267k-param net is pure
        # overhead under host contention (~60ms vs 0.25ms single-threaded,
        # same logits — verified). Pin to 1 thread for the forward and
        # restore; safe under the bundle lock (no concurrent model call).
        _prior_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            with torch.inference_mode():
                logits = bundle.model(x, return_logits=True)
                # MODEL_CLASS_CONTRACT v1 (Fix #3): WAIT (index 3) is a legacy
                # policy bridge — it is MASKED before softmax so it cannot
                # steal probability mass from the trained 3 classes.  3-wide
                # logits pass through unchanged; 4-wide logits have WAIT
                # forced to -1e4 (≈0 prob) while keeping the on-disk 4-head
                # geometry intact.  No shape change, no calibration drift on
                # the trained slice.
                from nexus_scalp.model_lifecycle.model_class_contract import (
                    masked_softmax,
                )

                probs = masked_softmax(logits)
        finally:
            torch.set_num_threads(_prior_threads)
        _trace.mark(LatencyStage.T6_MODEL_DONE)

        self._inference_count = getattr(self, "_inference_count", 0) + 1
        _trace.mark(LatencyStage.T7_DECODE_DONE)
        _trace.mark(LatencyStage.T8_CONFIDENCE_DONE)
        _trace.mark(LatencyStage.T10_PUBLISHED)
        self._last_inference_latency_ms = _trace.model_ms()
        # keep the honest staged breakdown for the API/UI
        self._last_latency_breakdown = _trace.to_dict()
        # OBS-PERF-RESILIENCE: feed the bounded rolling window and alert once
        # per regression epoch. Fully exception-isolated — observability
        # failures can never disturb inference (INV-018).
        try:
            detector = self._latency_regression
            if detector is None:
                from nexus_scalp.observability.latency_regression import (
                    LatencyRegressionDetector,
                )

                detector = self._latency_regression = LatencyRegressionDetector()
            detector.observe_breakdown(self._last_latency_breakdown)
            if detector.should_alert():
                p95 = detector.summary().get("e2e_ms", {}).get("p95_ms")
                logger.warning(
                    "[LATENCY_REGRESSION] event=E2E_P95_REGRESSED "
                    "p95_ms=%s budget_p95_ms=%s epochs=%s",
                    p95,
                    detector.summary().get("budget_p95_ms"),
                    detector.regression_epochs_total,
                )
                self.emit_incident_telemetry(
                    event_type="INFERENCE_LATENCY_REGRESSION",
                    component="inference",
                    error_code="SLOW_INFERENCE",
                    severity="MEDIUM",
                    correlation_id="latency-watch",
                )
        except Exception as _lat_err:  # never disturb the hot path
            logger.debug("[LATENCY_REGRESSION] observe failed", error=str(_lat_err))
        self._last_model_forward_ms = _trace.model_ms()
        self._last_feature_ms = _trace.feature_ms()
        self._last_e2e_ms = _trace.e2e_ms()
        return probs

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
        if self._shadow_challenger is None and self._governance_shadow is None:
            return
        try:
            engine = self.shadow_engine
            # CHG-0046 D1: the shadow compares against the model that
            # ACTUALLY served this tick — the loaded bundle's authoritative
            # contract (effective_*), never the class bootstrap constants
            # (which lag at scalp_v1/50D while a 70D bundle serves).
            live_schema_id = str(self.effective_feature_schema_id)
            live_dim = int(self.effective_feature_dim)
            x50 = fv.to_tensor_input() if hasattr(fv, "to_tensor_input") else [0.0] * live_dim
            # CHG-0046 D5: deterministic full-vector fingerprint (the salted
            # 5-element python hash() was irreproducible across processes and
            # insensitive to 90% of the vector — same-input proof impossible).
            from nexus_scalp.shadow.compat import vector_fingerprint

            feature_hash = vector_fingerprint(x50)
            regime_str = getattr(getattr(regime_state, "regime", None), "value", "UNKNOWN")
            if isinstance(regime_str, str) is False and regime_str is not None:
                regime_str = str(regime_str)
            news_ctx: Any = None
            if self._news_enabled and self.news_engine is not None:
                try:
                    news_ctx = self.news_engine.current_context()
                except Exception:
                    news_ctx = None
            champion_action = (
                proposal.action.value if hasattr(proposal.action, "value") else str(proposal.action)
            )
            champ_probs = [
                float(v)
                for v in (self._last_probs.tolist() if self._last_probs is not None else [])
            ]
            champ_ref_dict: dict[str, Any] = {
                "model_id": self.champion_manager.model_id,
                "model_version": self.champion_manager.model_version,
                # CHG-0046 D1: bundle-authoritative identity, not class
                # bootstrap constants (which say scalp_v1/50D while the
                # loaded artifact serves scalp_v3/70D).
                "feature_schema_id": live_schema_id,
                "feature_dimension": live_dim,
            }
            with contextlib.suppress(Exception):
                champ = self.champion_manager.champion_or_none()
                if champ is not None:
                    champ_ref_dict["model_id"] = champ.model_id
                    champ_ref_dict["model_version"] = champ.model_version
                    champ_ref_dict["artifact_hash"] = champ.artifact_hash
            if self._governance_shadow is not None and engine.active_run_id:
                # TASK-6: compute the 10 REAL scalp_v2 extras from the same
                # causal bar window the Champion used (features/schema_augment,
                # TASK-5 contract). A 60D Challenger must never receive
                # zero-filled extras (INV-009 / no-silent-pad rule).
                extras_60d = None
                try:
                    from nexus_scalp.features.schema_augment import compute_60d_extras

                    bars = self.aggregator.get_completed_bars()
                    if bars and len(bars) >= 5:
                        opens = np.asarray([float(b.open) for b in bars[-60:]], dtype=np.float32)
                        highs = np.asarray([float(b.high) for b in bars[-60:]], dtype=np.float32)
                        lows = np.asarray([float(b.low) for b in bars[-60:]], dtype=np.float32)
                        closes = np.asarray([float(b.close) for b in bars[-60:]], dtype=np.float32)
                        vols = np.asarray(
                            [float(getattr(b, "tick_volume", 0.0) or 0.0) for b in bars[-60:]],
                            dtype=np.float32,
                        )
                        extras_60d = compute_60d_extras(
                            opens=opens,
                            highs=highs,
                            lows=lows,
                            closes=closes,
                            volumes=vols,
                        )
                except Exception as e60:
                    logger.debug("[MODEL_SHADOW] 60D extras unavailable (isolated)", error=str(e60))
                self._governance_shadow.compare(
                    champion_vector=x50,
                    reference_vector=self._governance_reference_vector,
                    news_context=(news_ctx.model_dump() if news_ctx is not None else None),
                    champion_ref=champ_ref_dict,
                    champion_action=champion_action,
                    champion_confidence=float(getattr(proposal, "confidence", 0.0)),
                    champion_probabilities=champ_probs,
                    timestamp=tick.timestamp,
                    symbol=tick.symbol,
                    timeframe="M1",
                    regime=regime_str,
                    session=getattr(proposal, "session", "") or "ALL",
                    run_id=engine.active_run_id,
                    decision_id=getattr(proposal, "request_id", ""),
                    champion_latency_ms=float(self._last_inference_latency_ms or 0.0),
                    feature_context_id=feature_hash,
                    extras_60d=extras_60d,
                )
            if self._shadow_challenger is not None:
                from nexus_scalp.shadow.models import ShadowModelRef

                champ_ref = ShadowModelRef(
                    model_id=champ_ref_dict.get("model_id", ""),
                    model_version=champ_ref_dict.get("model_version", ""),
                    feature_schema_id=live_schema_id,
                    feature_dimension=live_dim,
                    artifact_hash=champ_ref_dict.get("artifact_hash", ""),
                    is_champion=True,
                )
                engine.set_champion_ref(champ_ref)
                engine.record_shadow_decision(
                    timestamp=tick.timestamp,
                    symbol=tick.symbol,
                    timeframe="M1",
                    feature_hash=feature_hash,
                    feature_schema_id=live_schema_id,
                    feature_dimension=live_dim,
                    regime=regime_str,
                    session=getattr(proposal, "session", "") or "ALL",
                    configuration_version=str(
                        getattr(self.config.model, "feature_schema_version", "")
                    ),
                    champion_ref=champ_ref,
                    champion_action=champion_action,
                    champion_confidence=float(getattr(proposal, "confidence", 0.0)),
                    champion_probabilities=champ_probs,
                    champion_strategy_id="",
                    decision_id=getattr(proposal, "request_id", ""),
                    feature_vector=x50,
                    # CHG-0046 D3: capture BOTH sides' risk geometry at record
                    # time. Champion geometry = the real proposal the policy
                    # emitted; shadow geometry is filled by the engine from
                    # the challenger action (side-neutral ATR geometry below
                    # once RiskEngine-level sizing is mirrored — the shadow
                    # NEVER consults RiskEngine itself).
                    champion_entry=float(proposal.proposed_entry),
                    champion_sl=float(proposal.stop_loss),
                    champion_tp=float(proposal.take_profit),
                    spread_usd=float(tick.spread_points),
                )
        except Exception as e:
            # Shadow is observability only: a failure here NEVER disturbs live.
            logger.error("[SHADOW] event=RECORD_FAILURE (isolated)", error=str(e))

    def _record_shadow70_observation(
        self,
        tick: TickData,
        fv: Any,
        proposal: TradeProposal,
    ) -> None:
        """BUG-105: 70D shadow observation (observability ONLY, INV-018).

        Runs on EVERY tick (independent of the 50D shadow/Challenger gate —
        the previous placement inside _record_shadow_decision's except block
        made it dead code on the happy path). Builds the canonical 70D vector
        (BASE 0..49 from the live 50D features, NEWS 50..59 from the same
        news context the Champion consumed, LIQUIDITY 60..69 from the
        liquidity producer) and records a SIMULATED observation. Fully
        failure-isolated: any fault logs and returns; the Champion path is
        never disturbed.
        """
        rt70 = getattr(self, "_shadow70_runtime", None)
        if (
            rt70 is None
            or rt70.state.value != "READY"
            or not getattr(self, "_shadow70_enabled", False)
        ):
            return
        try:
            from nexus_scalp.features.liquidity_runtime import (
                build_70d_vector,
            )
            from nexus_scalp.shadow.shadow70.liq_provider import build_liquidity_10

            # CHG-0046 D1b: the 70D observation inherits the bundle's
            # AUTHORITATIVE base width, not the hard-coded 50 — a 0-filled
            # fallback must match the ACTUAL base block the champion used.
            _base_dim = int(self.effective_feature_dim) - 20
            base50 = [0.0] * max(1, _base_dim)
            if fv is not None:
                v = fv.to_tensor_input() if hasattr(fv, "to_tensor_input") else None
                if v is not None and len(v) == _base_dim:
                    base50 = list(v)
            feature_hash = getattr(fv, "feature_hash", "") or ""
            regime_str = getattr(getattr(self, "_last_regime_state", None), "regime", None)
            regime_str = getattr(regime_str, "value", "UNKNOWN") or "UNKNOWN"

            # news vector from the same context the Champion saw
            news10 = [0.0] * 10
            news_ctx: Any = None
            if self._news_enabled and self.news_engine is not None:
                try:
                    news_ctx = self.news_engine.current_context()
                except Exception:
                    news_ctx = None
            if news_ctx is not None:
                try:
                    from nexus_scalp.governance.alignment import (
                        vectorize_news_context,
                    )
                    from nexus_scalp.shadow.shadow70.news_provider import (
                        build_news_10,
                    )

                    news10, _ = build_news_10(vectorize_news_context(news_ctx))
                except Exception:
                    news10 = [0.0] * 10

            # CHG-0046 D8: record the governor's CAUSAL state alongside the
            # snapshot. The champion consumes a governor snapshot ONLY when
            # causal_state == VALID (else inference is blocked); the shadow
            # accepts a fresh-but-invalid snapshot and labels it. The
            # liquidity_state column now carries that truth so an operator
            # can distinguish a like-for-like comparison from an
            # INPUT_MISMATCH (the champion saw no liquidity at all).
            liquidity_calc_version = ""
            liquidity_causal_state = ""
            liq10 = [0.0] * 10
            gov = getattr(self, "liquidity_governor", None)
            if gov is not None:
                try:
                    liquidity_causal_state = str(
                        gov.causal_state() if callable(getattr(gov, "causal_state", None)) else ""
                    )
                except Exception:
                    liquidity_causal_state = ""
            try:
                liq10, liquidity_calc_version = build_liquidity_10(self, tick)
            except Exception:
                liq10, liquidity_calc_version = [0.0] * 10, ""

            # canonical schema identity for THIS observation (the old hook
            # passed "" which silently skipped schema verification)
            from nexus_scalp.features.schema_contract import feature_schema_hash

            schema_hash = feature_schema_hash()

            vector70 = build_70d_vector(base50, family_10=news10, liquidity_10=liq10)

            champion_action = (
                proposal.action.value if hasattr(proposal.action, "value") else str(proposal.action)
            )
            champ_probs = [
                float(v)
                for v in (self._last_probs.tolist() if self._last_probs is not None else [])
            ]
            obs = rt70.observe(
                vector70=vector70,
                champion_action=champion_action,
                champion_probabilities=champ_probs,
                champion_confidence=float(getattr(proposal, "confidence", 0.0)),
                snapshot_id=feature_hash or f"snap_{tick.timestamp.isoformat()}",
                timestamp=tick.timestamp,
                symbol=tick.symbol,
                timeframe="M1",
                regime=regime_str,
                session=getattr(proposal, "session", "") or "ALL",
                news_context=(news_ctx.model_dump() if news_ctx is not None else None),
                news_state=str(getattr(news_ctx, "state", "") or "")
                if isinstance(news_ctx, object)
                else "",
                # CHG-0046 D8: truthful liquidity provenance — the governor's
                # causal state + how the 10 values were produced. An
                # INVALID/stale state means the CHAMPION would have blocked
                # inference this tick; the shadow row is labeled, not silent.
                liquidity_state=liquidity_causal_state
                or ("unavailable" if liquidity_calc_version == "unavailable" else "UNKNOWN"),
                liquidity_calculation_version=liquidity_calc_version,
                liquidity_features_10=liq10,
                base_feature_hash=feature_hash,
                feature_schema_hash=schema_hash,
                sample_source="LIVE",
                decision_id=getattr(proposal, "request_id", ""),
            )
            hm = getattr(self, "_shadow70_health", None)
            if hm is not None and obs.valid:
                hm.update(vector70, stale=False)
            dm = getattr(self, "_shadow70_drift", None)
            if dm is not None and obs.valid:
                dm.update(vector70)
            wk = getattr(self, "_shadow70_worker", None)
            if wk is not None:
                if not getattr(self, "_shadow70_worker_started", False):
                    wk.start()
                    self._shadow70_worker_started = True
                if not wk.enqueue(obs):
                    pass  # backpressure already telemetried by the worker
        except Exception as e70:
            logger.error("[SHADOW70] hook failed (isolated, Champion unaffected)", error=str(e70))

    # -------------------------
    # Async retraining worker
    # -------------------------

    @staticmethod
    def _retrain_swap_decision(
        _self: LiveEngine | None,
        *,
        dispatched_for_path: str | Path | None,
        candidate: Any | None = None,
        current_bundle: Any | None = None,
    ) -> dict[str, Any]:
        """STALENESS DECISION for an async retrain completion (agent 10).

        An async fine tune is dispatched against the BUNDLE that served the
        buffer (bundle.artifact_path at dispatch). While it trains, a
        concurrent hot_swap / promotion / rollback / collapse recovery can
        publish a NEWER bundle. A late retrain completion must then be
        DISCARDED — otherwise stale weights overwrite a newer valid model
        (verified by RED tests). Missing bundle => stale.
        """

        def _norm(x: Any) -> str:
            return str(x or "").replace("\\", "/")

        norm = _norm
        dispatched = norm(dispatched_for_path)
        cur = norm(getattr(current_bundle, "artifact_path", None))
        if dispatched and cur and dispatched != cur:
            return {"swap": False, "reason": "STALE_RETRAIN_RESULT"}
        # Also stale when the serving bundle vanished mid-flight
        if dispatched and not cur:
            return {"swap": False, "reason": "STALE_RETRAIN_RESULT"}
        return {"swap": True, "reason": ""}

    async def _trigger_async_online_fine_tune(self) -> None:
        if self._retrain_inflight:
            return
        self._retrain_inflight = True
        dispatched_for: Path | None = None
        try:
            logger.info("ASYNC RETRAIN START", buffer_size=len(self._rolling_feature_records))
            # AGENT-10: capture the serving artifact identity at DISPATCH so
            # a stale completion (hot swap / promotion raced ahead) can be
            # discarded end-to-end instead of overwriting the newer model.
            with self._bundle_lock:
                dispatched_for = (
                    Path(self._bundle.artifact_path) if self._bundle is not None else None
                )

            df = pl.DataFrame(list(self._rolling_feature_records))
            df_labeled = self.online_labeler.label_dataframe(df)
            # BUG-182B: bind columns to the LOADED bundle's contract, not the
            # class bootstrap (the two differ whenever a 70D artifact serves).
            feature_cols = list(self.effective_feature_cols)

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

            # BUG-235/236 (MLFIX-T3-CLOSE): the trainer attaches an explicit
            # PersistDecision to every returned model. Honor it: a rejected /
            # zero-improvement / gate-failed candidate must NEVER be persisted
            # (no scaler reload, no atomic save, no bundle swap, no trainer
            # rebind, no provenance re-registration, no "SUCCESS" claim) —
            # otherwise degenerate paper-model labels self-perpetuate through
            # the live artifact.
            decision = decision_of(updated_model)
            if decision is not None and not decision.persist:
                logger.info(
                    f"ASYNC RETRAIN SKIPPED: {decision.detail or 'candidate rejected'} "
                    f"(reason={decision.reason})",
                    persist=False,
                )
                # No improvement was persisted: the retrain clock resets so
                # the engine retries after a full interval instead of
                # hammering the gate every bar.
                self._bars_since_last_retrain = 0
                return

            # AGENT-10 (STALE RETRACE): the buffer was trained against
            # the DISPATCH-time bundle. Discard a completion that is now stale
            # (a concurrent hot_swap/promotion published a newer model).
            staleness = self._retrain_swap_decision(
                None,
                dispatched_for_path=dispatched_for,
                candidate=updated_model,
                current_bundle=self._bundle,
            )
            if not staleness["swap"]:
                logger.warning(
                    "[ASYNC_RETRAIN_REFUSED] event=STALE_RETRAIN_RESULT "
                    "dispatched_for=%s current_bundle=%s (newer model wins)",
                    dispatched_for,
                    getattr(self._bundle, "artifact_path", None),
                )
                self._bars_since_last_retrain = 0
                return
            # Legacy fallback (pre-PersistDecision trainers): the model
            # carries only the old boolean tag.
            if decision is None and getattr(updated_model, "_finetune_accepted", True) is False:
                if getattr(updated_model, "_finetune_zero_improvement", False):
                    _legacy_detail = "zero improvement over baseline; baseline kept"
                    _legacy_reason = "ZERO_IMPROVEMENT_BASELINE_KEPT"
                else:
                    _legacy_detail = "quality gate rejected the candidate; baseline kept"
                    _legacy_reason = "QUALITY_GATE_FAILED"
                logger.info(
                    f"ASYNC RETRAIN SKIPPED: {_legacy_detail} (reason={_legacy_reason})",
                    persist=False,
                )
                self._bars_since_last_retrain = 0
                return

            # Refresh scaler + persist weights (reached ONLY for accepted
            # candidates — no wasted IO on a rejected one).
            scaler = self._load_scaler_artifacts(bundle.artifact_path)

            # BUG-141 residual (BUG-243): the persist is END-TO-END atomic.
            # A BUG-141 width-contract refusal (or I/O failure) must refuse
            # EVERYTHING: no bundle swap, no provenance re-registration, no
            # "SUCCESS" claim — otherwise the in-memory serving identity
            # diverges from the artifact bytes on disk (the disk keeps the
            # old checkpoint while memory serves retrained weights).
            saved = self._save_model_weights_atomic(updated_model, bundle.artifact_path)
            if not saved:
                logger.error(
                    "[ASYNC_RETRAIN_REFUSED] event=PERSIST_REFUSED "
                    "reason=BUG141_WIDTH_CONTRACT_OR_IO (baseline kept, disk==memory)",
                    path=str(bundle.artifact_path),
                )
                self._bars_since_last_retrain = 0
                return

            with self._bundle_lock:
                self._bundle = ModelBundle(
                    model=updated_model, scaler=scaler, artifact_path=bundle.artifact_path
                )
            # BUG-185: rebind is a cheap no-op when the width is unchanged.
            self._rebind_trainer_to_bundle()

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
        sample_x_np = bundle.scaler.transform(sample_x_np)
        x = torch.tensor(sample_x_np, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)

        with torch.inference_mode():
            p = bundle.model(x)
        test1_pass = not (torch.isnan(p).any() or torch.isinf(p).any())

        # Test 2/3: calibrated class distribution
        test_df = df_labeled.tail(100)
        test_x_np = test_df.select(feature_cols).to_numpy().astype(np.float32, copy=False)
        test_x_np = bundle.scaler.transform(test_x_np)
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
            test_x_np = bundle.scaler.transform(test_x_np)
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
            # BUG-182B: artifact-driven columns (see _trigger_async_online_fine_tune).
            feature_cols = list(self.effective_feature_cols)
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
            fresh = ScalpNet(
                num_features=self._declared_contract_dim_for_path(model_path) or self.FEATURE_DIM,
                # BUG-243: declared head, not hardcoded 4.
                num_classes=self._declared_head_classes_for_path(
                    model_path.with_suffix(".meta.json")
                ),
            )
            fresh.eval()
            with self._bundle_lock:
                self._bundle = ModelBundle(
                    model=fresh,
                    scaler=self._bundle.scaler if self._bundle else None,
                    artifact_path=model_path,
                )
            # BUG-185: the fresh model was seeded at the PATH-declared
            # contract width - rebind the trainer to it.
            self._rebind_trainer_to_bundle()
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

    def _update_runtime_mode(self) -> None:
        """Derives the REAL runtime execution mode from connection + config.

        Task section 8: dashboard MODE must be authoritative. Possible:
        PAPER / SHADOW / LIVE / REPLAY / STOPPED / DEGRADED. When config says
        LIVE but MT5 is not connected, the mode reports DEGRADED (the UI shows
        LIVE_CONFIGURED / MT5_DISCONNECTED - never LIVE_READY).
        """
        mode = ""
        try:
            mode = str(self.config.execution.mode.value or "").upper()
        except Exception:
            mode = ""
        try:
            connected = bool(self.adapter.is_connected())
        except Exception:
            connected = False

        if mode == "LIVE":
            if connected:
                snap = self._account_snapshot
                allowed = True
                try:
                    allowed = bool(getattr(snap, "trade_allowed", True))
                except Exception:
                    allowed = True
                if allowed is False:
                    self._runtime_mode = "LIVE / TRADE_BLOCKED"
                else:
                    self._runtime_mode = "LIVE"
            else:
                self._runtime_mode = "LIVE_CONFIGURED / MT5_DISCONNECTED"
        elif not mode:
            self._runtime_mode = "STOPPED"
        else:
            self._runtime_mode = mode
        # STATE-SEMANTICS (C-004, 2026-09-02): edge-triggered [MODE] logging.
        # The periodic 5s re-evaluation updates _runtime_mode silently;
        # a log line is emitted only when the TRUTH CHANGES (or once at
        # first evaluation). Steady-state repetition of an identical mode
        # line is not information (BUG-070-4 class, ~2k lines/day).
        if self._runtime_mode != getattr(self, "_last_logged_runtime_mode", None):
            self._last_logged_runtime_mode = self._runtime_mode
            logger.info(
                "[MODE] runtime_mode=%s configured_mode=%s",
                self._runtime_mode,
                mode,
            )

    # ------------------------------------------------------------------
    # BUG-212: boot-time adapter/mode alignment (hard simulation boundary).
    # ------------------------------------------------------------------
    def align_adapter_to_boot_mode(
        self,
        adapter: IMT5Port,
        mode: ExecutionMode | None = None,
    ) -> IMT5Port:
        """Align the execution adapter with the EFFECTIVE boot mode.

        BUG-212: the primary launcher (NexusTradingForexBot.py) historically
        bound DirectMT5Adapter for every win32 boot regardless of mode, so a
        PAPER boot stayed wired to the real terminal. The engine itself must
        own the boundary: whenever the effective mode is PAPER the
        simulation adapter is REQUIRED and a real broker adapter passed by
        any caller is replaced BEFORE the first tick (same boot rule as
        engine_boot.py's BUG-148 guard; SHADOW keeps its live adapter per
        the shadow-observation contract). Returns the adapter that should
        be used (the same object when no change is needed) without
        connecting or disconnecting anything: the caller decides the
        connect lifecycle.
        """
        from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

        effective = mode or self.config.execution.mode
        try:
            effective = ExecutionMode(str(effective).strip().upper())
        except ValueError:
            logger.warning(
                "[MODE] align_adapter_to_boot_mode: unknown mode %r (no change)",
                effective,
            )
            return adapter

        # BOOT rule mirrors the engine_boot.py BUG-148 guard exactly:
        # PAPER boots REQUIRE the simulation adapter. SHADOW boots KEEP the
        # live-data (prediction) adapter by contract - shadow evidence must
        # observe the REAL feed/positions; its no-mutation guarantee is
        # enforced by the decision-path boundary (BUG-212 SHADOW_BOUNDARY),
        # not by adapter identity. (set_execution_mode's PAPER+SHADOW swap
        # is the HOT-switch behavior and stays unchanged.)
        wants_simulation = effective == ExecutionMode.PAPER
        is_simulation = isinstance(adapter, PaperMT5Adapter)

        # BUG-232: the mirror-image guard. A LIVE boot must NEVER run on the
        # simulation adapter. This is the exact production failure of
        # 2026-09-03 18:48: the launcher bound PaperMT5Adapter from the
        # YAML-PAPER default, the engine re-bound execution.mode to the
        # persisted LIVE value, and the old alignment logic only handled
        # PAPER<-real — leaving the paper simulator (seed price 2000.00,
        # login 9990001) wired under a LIVE badge. A LIVE boot that finds a
        # paper adapter swaps in the real broker adapter from config before
        # the first tick.
        if not wants_simulation and is_simulation and effective == ExecutionMode.LIVE:
            import sys as _sys

            replacement_real: IMT5Port | None = None
            if _sys.platform == "win32":
                try:
                    from nexus_scalp.adapters.mt5.mt5_adapter import (
                        HAS_NATIVE_MT5,
                        DirectMT5Adapter,
                    )

                    if HAS_NATIVE_MT5:
                        mt5_cfg = getattr(self.config, "mt5", None)
                        replacement_real = DirectMT5Adapter(
                            account=getattr(mt5_cfg, "account", None),
                            password=getattr(mt5_cfg, "password", None),
                            server=getattr(mt5_cfg, "server", None),
                            timeout=getattr(mt5_cfg, "timeout_ms", 5000),
                            retries=getattr(mt5_cfg, "retries", 3),
                        )
                except Exception as build_err:
                    logger.error(
                        "[MODE] BUG-232 LIVE-boot paper->real adapter build failed: %s",
                        build_err,
                    )
                    replacement_real = None
            if replacement_real is None:
                from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

                replacement_real = RemoteMT5GatewayAdapter()
            logger.warning(
                "[MODE] BUG-232 LIVE boot realigned to real-broker boundary "
                "effective_mode=%s previous_adapter=%s replacement=%s",
                effective.value,
                type(adapter).__name__,
                type(replacement_real).__name__,
            )
            return replacement_real

        if wants_simulation and not is_simulation:
            replacement: IMT5Port = PaperMT5Adapter(
                symbol=self.config.execution.symbol,
                initial_balance=float(getattr(self, "_last_balance", 0.0) or 0.0) or 10000.0,
            )
            logger.warning(
                "[MODE] BUG-212 adapter realigned to simulation boundary "
                "effective_mode=%s previous_adapter=%s",
                effective.value,
                type(adapter).__name__,
            )
            return replacement
        return adapter

    def set_execution_mode(self, mode: ExecutionMode, *, source: str = "WEB_UI") -> dict:
        """BUG-148: HOT execution-mode switch (operator authority, UI + CLI).

        Records the explicit operator choice (beats any persisted value for
        this process lifetime), re-derives the runtime badge truthfully, and
        swaps the execution adapter when the new mode requires a different
        execution boundary (PAPER/SHADOW -> simulation; LIVE -> real broker).

        Trading safety: swapping the adapter NEVER enables live order
        dispatch by itself — order authority remains RiskEngine +
        OrderLifecycleManager. In PAPER the adapter is a simulation, so no
        real order can ever be placed regardless of what the UI shows.
        """
        from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

        if not isinstance(mode, ExecutionMode):
            return {"success": False, "reason": "INVALID_MODE"}
        old_mode = self.config.execution.mode
        self._mode_override = mode
        self.config.execution.mode = mode
        logger.info(
            "[MODE] HOT_SWAP_REQUESTED source=%s old=%s new=%s",
            source,
            old_mode.value,
            mode.value,
        )

        # Adapter boundary swap: PAPER/SHADOW => simulation adapter (safe);
        # LIVE => real MT5 adapter. The adapter is rebuilt only when its
        # execution boundary actually changes (never mid-order: dispatch
        # runs on this same loop thread, so the swap is sequential).
        wants_simulation = mode in (ExecutionMode.PAPER, ExecutionMode.SHADOW)
        is_simulation = isinstance(self.adapter, PaperMT5Adapter)
        swapped = False
        try:
            if wants_simulation and not is_simulation:
                old_adapter = self.adapter
                if hasattr(old_adapter, "disconnect"):
                    old_adapter.disconnect()
                new_adapter = PaperMT5Adapter(
                    initial_balance=float(getattr(self, "_last_balance", 0.0) or 0.0) or 10000.0,
                    # BUG-232: the simulation must track the ACTIVE symbol.
                    # The old hot-swap built the paper adapter without a
                    # symbol, so it fell back to EURUSD conventions while the
                    # engine traded XAUUSD (wrong digits/spread/seed).
                    symbol=self.config.execution.symbol,
                )
                self.adapter = new_adapter
                self.order_manager.adapter = new_adapter
                self.order_manager.mt5_adapter = new_adapter
                # BUG-226: provenance follows the adapter so ledger rows and
                # account snapshots written under simulation are tagged PAPER.
                new_adapter.current_account_source = "PAPER"
                new_adapter.connect()
                swapped = True
            elif not wants_simulation and is_simulation:
                if hasattr(self.adapter, "disconnect"):
                    self.adapter.disconnect()
                from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

                mt5_cfg = getattr(self.config, "mt5", None)
                new_adapter_direct: IMT5Port = DirectMT5Adapter(
                    account=getattr(mt5_cfg, "account", None),
                    password=getattr(mt5_cfg, "password", None),
                    server=getattr(mt5_cfg, "server", None),
                    timeout=getattr(mt5_cfg, "timeout_ms", 5000),
                    retries=getattr(mt5_cfg, "retries", 3),
                )
                self.adapter = new_adapter_direct
                self.order_manager.adapter = new_adapter_direct
                self.order_manager.mt5_adapter = new_adapter_direct
                # BUG-226: back to the real broker — provenance returns to LIVE.
                new_adapter_direct.current_account_source = "LIVE"
                new_adapter_direct.connect()
                swapped = True
        except Exception as swap_err:
            logger.error("[MODE] adapter swap failed (isolated): %s", swap_err)
            return {
                "success": False,
                "reason": "ADAPTER_SWAP_FAILED",
                "detail": str(swap_err),
                "mode": mode.value,
            }

        # BUG-232: ATOMIC STATE TRANSITION — a hot-swap must invalidate every
        # piece of state derived from the OLD adapter's market data before the
        # new pipeline is allowed to act. The BUG-231 production incident
        # (stale PAPER-geometry SELL_LIMIT at 2000.08 dispatched to the real
        # 4442 broker) is exactly this hole: the adapter changed but the
        # signal policy's cached price/last-order state, the aggregator's
        # paper bars, and in-flight proposals survived the swap.
        if swapped:
            try:
                self._invalidate_cross_mode_state(old_mode, mode)
            except Exception as invalidation_err:
                logger.error(
                    "[MODE] cross-mode state invalidation failed (isolated): %s",
                    invalidation_err,
                )

        self._update_runtime_mode()
        return {
            "success": True,
            "mode": mode.value,
            "previous_mode": old_mode.value,
            "adapter_swapped": swapped,
            "runtime_mode": self._runtime_mode,
        }

    def _invalidate_cross_mode_state(
        self, old_mode: ExecutionMode, new_mode: ExecutionMode
    ) -> None:
        """BUG-232: drop PAPER-derived state when leaving simulation (and
        vice versa) so no stale tick/price/proposal can cross the boundary.

        Isolated by contract: never raises, never blocks the swap result.
        """
        import time as _time

        now_iso = datetime.now(UTC).isoformat()
        old_is_paper = old_mode in (ExecutionMode.PAPER, ExecutionMode.SHADOW)
        new_is_paper = new_mode in (ExecutionMode.PAPER, ExecutionMode.SHADOW)
        if old_is_paper == new_is_paper:
            return  # same boundary class — nothing cross-mode to invalidate

        # 1) Bump the session generation: every stale-tick / stale-proposal
        #    check compares against this. Anything stamped with the previous
        #    generation is rejected downstream.
        old_gen = getattr(self, "_mode_session_generation", 0)
        self._mode_session_generation = old_gen + 1
        logger.warning(
            "[MODE] BUG-232 state invalidation old=%s new=%s generation=%s->%s",
            old_mode.value,
            new_mode.value,
            old_gen,
            self._mode_session_generation,
        )

        # 2) Signal policy caches: last executed price/time and last active
        #    direction are PAPER-geometry state. Clear them so the next
        #    proposal can only be derived from the NEW adapter's tick.
        policy = getattr(self, "signal_policy", None)
        if policy is not None:
            for attr in (
                "last_order_price",
                "last_order_time",
                "_last_active_direction",
                "_last_active_direction_time",
            ):
                with contextlib.suppress(Exception):
                    setattr(policy, attr, None)
            with contextlib.suppress(Exception):
                policy._last_executed_price = 0.0

        # 3) Drop any engine-staged pending proposals/ticks stamped before
        #    the swap (defensive: their tick provenance is the old adapter).
        for attr in ("_pending_proposals", "_latest_tick", "_last_tick"):
            with contextlib.suppress(Exception):
                if hasattr(self, attr):
                    setattr(self, attr, None)

        # 3b) BUG-231 continuation: the M1 bar aggregator still holds bars
        #     minted from the OLD adapter's synthetic ticks (paper random-walk
        #     @2000 for metals). Without a purge, the next completed-bar
        #     window mixes stale paper bars with fresh live bars and the
        #     feature/predictive-limit geometry stays 2000-relative (observed
        #     live 2026-09-03 14:48-16:19 UTC, audit_signals 1069937..1076824).
        #     A empty aggregator re-warms from the NEW adapter's history via
        #     the existing BUG-054 reseed path in _cold_start_warmup /
        #     _resync_from_broker.
        aggregator = getattr(self, "aggregator", None)
        if aggregator is not None:
            try:
                # reseed([]) atomically clears all history (BUG-054 contract);
                # an empty aggregator re-warms from the NEW adapter's history
                # via _cold_start_warmup / _resync_from_broker.
                aggregator.reseed([])
                logger.warning(
                    "[MODE] BUG-231 aggregator history purged (paper bars "
                    "must not cross the execution boundary)"
                )
            except Exception as agg_err:
                logger.warning(
                    "[MODE] aggregator purge failed (non-fatal, next reseed will realign): %s",
                    agg_err,
                )
            with contextlib.suppress(Exception):
                self.warmup_state = "WARMING_UP"
                self._warmup_attempt = 0
                logger.info(
                    "[MODE] warmup state reset to WARMING_UP — HTF/feature "
                    "chain will re-derive from the new adapter's bars via "
                    "the 15s periodic readiness re-evaluation"
                )

        # 4) Reset the tick-stagnation clock so the watchdog does not
        #    immediately "reconnect" while the new adapter warms up.
        self._last_tick_processed_time = _time.time()

        # 5) BUG-232: drop the cached account snapshot. It was captured from
        #    the OLD adapter; serving it under the new mode made the UI show
        #    a paper account (login 9990001 / 10000.0) after a PAPER->LIVE
        #    swap. The next tick loop refreshes it from the new adapter.
        self._account_snapshot = None

        logger.info(
            "[MODE] BUG-232 cross-mode state invalidated at=%s swap=%s->%s",
            now_iso,
            old_mode.value,
            new_mode.value,
        )

    def _notify_startup(self, account: AccountInfo | None) -> None:
        if not account:
            return
        with contextlib.suppress(Exception):
            self.notifier.notify_startup(
                symbol=self.config.execution.symbol,
                mode=self.config.execution.mode.value,
                balance=account.balance,
                equity=account.equity,
            )

    def _update_survival_state(self, account: AccountInfo, current_pos_count: int) -> None:
        # RUNTIME CONFIG (BUG-132): the survival guard must use the SAME
        # max_account_drawdown_pct the user sees / persists (runtime snapshot)
        # -- NOT the bootstrap AppConfig default. A UI save to 95% never took
        # effect here because self.config.risk stayed at the YAML default
        # (2.0%), so a (post-withdrawal) drawdown >2% killed a live engine
        # even though the persisted limit was 95%. The snapshot is
        # authoritative; fall back to bootstrap only when detached.
        store = getattr(self, "runtime_config", None)
        if store is not None:
            dd_limit_pct = float(store.get_snapshot().risk.max_account_drawdown_pct)
        else:
            dd_limit_pct = self.config.risk.max_account_drawdown_pct
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
                with contextlib.suppress(Exception):
                    self.notifier.notify_survival_mode_changed(active=False, drawdown_pct=0.0)

        elif account.equity < self._peak_equity and self._peak_equity > 0:
            drawdown_pct = ((self._peak_equity - account.equity) / self._peak_equity) * 100.0
            if drawdown_pct > (dd_limit_pct * 0.5) and not self._survival_mode_active:
                self._survival_mode_active = True
                logger.warning("SURVIVAL MODE ON", drawdown_pct=round(drawdown_pct, 2))
                with contextlib.suppress(Exception):
                    self.notifier.notify_survival_mode_changed(
                        active=True, drawdown_pct=drawdown_pct
                    )

            if drawdown_pct > dd_limit_pct:
                logger.critical("MAX DRAWDOWN EXCEEDED; HALTING", dd_pct=round(drawdown_pct, 2))
                with contextlib.suppress(Exception):
                    self.notifier.notify_kill_switch_activated(
                        f"Max Drawdown Exceeded ({drawdown_pct:.2f}%)"
                    )
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
