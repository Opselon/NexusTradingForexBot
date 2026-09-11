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
import os
import signal
import threading
import time
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
from nexus_scalp.adapters.database.audit_repository import (
    AuditRepository,
    RuntimeRiskStateReadError,
)
from nexus_scalp.adapters.database.broker_history import session_spread_percentile
from nexus_scalp.candle_intelligence import CandleIntelligenceEngine

# RUNTIME CONFIGURATION (hot reload): the authoritative runtime provider.
# Consumers read the current immutable snapshot; live.yaml is bootstrap-only.
from nexus_scalp.configuration import RuntimeConfigStore
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ExecutionMode, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeProposal,
)
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import PreTradeExperienceDecision
from nexus_scalp.experience.provenance import ModelRegistry, fingerprint_artifact
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.liquidity_runtime import LiquidityGovernor
from nexus_scalp.features.regime_classifier import MarketRegimeClassifier, MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector, ScalpFeatureEngine
from nexus_scalp.features.schema import active_columns, active_dimension, active_schema
from nexus_scalp.governance import (
    GovernanceShadowRuntime,
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
from nexus_scalp.model_lifecycle.dataset_snapshot import TrainingDatasetSnapshotStore
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
from nexus_scalp.risk.runtime_safety import (
    AccountFreshness,
    BootDecision,
    HotPathErrorCircuit,
    PersistedRiskState,
    resolve_boot_decision,
)
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
    #: RUNTIME RESILIENCE (Agent-7 failure injection): True when a scaler
    #: sidecar FILE existed but failed to load / validate (corrupt npz,
    #: wrong width, unreadable). A corrupt scaler must NEVER silently serve
    #: raw unscaled features to the model (T24: wrong distribution -> wrong
    #: predictions); the inference path refuses to serve such a bundle.
    #: Relanded after the f3f53f69 containment revert removed it together
    #: with the absorbed 2fc1d84c carrier (FI-4 regression net was left red).
    corrupt: bool = False

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
        self._live_sequence_trained_mode = st.trained_mode

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
            trained_mode=getattr(self, "_live_sequence_trained_mode", "2d"),
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
        self._live_sequence_trained_mode = state.trained_mode
        logger.info(
            "[MODEL] event=SERVING_MODE_BOUND",
            trained_mode=state.trained_mode,
            seq_len=state.seq_len,
        )

    def _maybe_build_live_sequence_tensor(self, x_scaled_now, bar_ts=None):
        from nexus_scalp.application.live_sequence import LiveSequenceService, LiveSequenceState

        state = LiveSequenceState(
            buffer=self._live_sequence_buffer,
            seq_len=self._live_sequence_seq_len,
            max_gap_us=self._live_sequence_max_gap_us,
            last_bar_ts_us=self._live_last_bar_ts_us,
            gap_invalid=self._live_sequence_gap_invalid,
            trained_mode=getattr(self, "_live_sequence_trained_mode", "2d"),
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
            trained_mode=getattr(self, "_live_sequence_trained_mode", "2d"),
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
            trained_mode=getattr(self, "_live_sequence_trained_mode", "2d"),
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
        # MISSION 5: compact operational digest throttle (composition-root
        # state; MaintenanceCycle owns the logic and reads/writes these).
        self._operational_digest_interval_sec: float = 24 * 3600.0
        self._last_operational_digest_time: float = 0.0
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

        # TASK-STORAGE-HYGIENE: runtime storage conductor (log compression +
        # byte budget + WAL checkpoint + updater cache/backup sweeps). Built
        # lazily by MaintenanceCycle from cfg.storage; throttled 10-min cycle
        # runs OFF the tick path via asyncio.to_thread, never on it.
        self._storage_guard: Any = None
        self._storage_cycle_interval_sec: float = 600.0
        self._last_storage_cycle_time: float = 0.0

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

        # =====================================================================
        # PERSISTED RUNTIME SAFETY STATE (P0, runtime-safety mission).
        # Canonical durable safety decision (RUNNING/HALTED/KILL_SWITCH) +
        # hot-path error circuit + account freshness guard. Pure policy lives
        # in risk/runtime_safety.py; persistence lives in AuditRepository
        # (runtime_risk_state single-row store). A safety halt triggered by a
        # real trading event MUST survive process restart until explicitly
        # released (nexus risk release); DEGRADED is session-local by design.
        # =====================================================================
        self._runtime_risk_state: str = "RUNNING"
        self._runtime_risk_detail: str = ""
        self._halt_reason: str = ""
        self._halt_triggered_at: str = ""
        self._hot_path_circuit = HotPathErrorCircuit()
        # Same config-derived default as the G29 freshness block below (which
        # assigns _freshness_max_age_sec later in __init__ — init-order safe).
        self._account_age_max_sec: float = float(
            (
                getattr(config, "freshness", None) is not None
                and getattr(config.freshness, "max_age_sec", 30.0)
            )
            or 30.0
        )
        self._account_freshness: str = "MISSING"
        self._account_last_successful_refresh: float = 0.0
        self._account_stale_blocked_total: int = 0
        # Consecutive-loss governance: derived from CANONICAL finalized ledger
        # outcomes (audit_ledger), never from volatile memory — survives
        # restart and cannot be fabricated from rejected/unfilled events.
        self._consecutive_loss_threshold: int = 3
        self._consecutive_loss_freeze_hours: float = 1.0
        self._loss_freeze_active: bool = False

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

        # MISSION 5: Telegram OPERATIONAL CONTROL SURFACE (inbound commands).
        # The bus issues authenticated INTENTS only — every mutation routes
        # through apply_command_intent() -> existing authority layers
        # (RiskEngine kill switch / governance rollback). INV-010 preserved:
        # the bus never calls the broker adapter. Disabled unless both
        # credentials AND the halt token are present (fail-closed control).
        self._command_bus: Any = None
        _halt_token = str(os.environ.get("NEXUS_TELEGRAM_CMD_TOKEN", "") or "")
        if config.telegram.enabled and bot_token and admin_id and _halt_token:
            from nexus_scalp.observability.tg_command_bus import TelegramCommandBus

            self._command_bus = TelegramCommandBus(
                bot_token=bot_token,
                admin_id=admin_id,
                target=self,
                halt_token=_halt_token,
            )
            logger.info(
                "[TG_CMD] event=BUS_CONSTRUCTED token_required=%s",
                bool(_halt_token),
            )
        elif config.telegram.enabled:
            logger.info(
                "[TG_CMD] event=BUS_DISABLED reason=NO_CMD_TOKEN "
                "(set NEXUS_TELEGRAM_CMD_TOKEN to enable operator commands)"
            )

        # BUG-061: local candle-intelligence subsystem (candle-close gate).
        # Isolated DB (candle_intel.db); H5 (audit rev2): the construction now
        # HONORS AppConfig.candle_intel — the 2026-09-07 Phase-3 decision doc
        # disabled this subsystem (enabled=False default, zero consumers,
        # 33.5k orphan rows/21d), but this site hardcoded enabled=True and
        # defeated the ruling. With no explicit config the subsystem stays
        # OFF; re-enable via candle_intel.enabled=true.
        _ci_cfg = getattr(self.config, "candle_intel", None)
        if _ci_cfg is not None and getattr(_ci_cfg, "enabled", False):
            try:
                self.candle_intel = CandleIntelligenceEngine(_ci_cfg)
            except Exception as ci_err:
                self.candle_intel = None
                logger.error("[CANDLE_INTEL] init failed (isolated)", error=str(ci_err))
        else:
            self.candle_intel = None
            logger.info(
                "[CANDLE_INTEL] disabled (config.candle_intel absent or enabled=false; "
                "H5 audit rev2)"
            )
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

        # =====================================================================
        # LEARNING-LOOP CLOSURE (P1): config-gated LearningCycleOrchestrator.
        # Default behavior: learning disabled -> the orchestrator exists but
        # refuses every cycle (fail-closed). When learning.enabled=true the
        # lifecycle may trigger ONLY under all existing guards (dataset-state
        # watermark, bounded concurrency, snapshot integrity, handoff
        # safeguards). Shadow attachment is delegated through the narrow
        # _attach_learning_candidate callback (no LiveEngine object is passed
        # into the orchestrator). Online fine-tune remains governed by
        # _online_finetune_enabled (default False) — unrelated to this.
        # =====================================================================
        from nexus_scalp.model_lifecycle.learning_config import LearningConfig
        from nexus_scalp.model_lifecycle.learning_loop import LearningCycleOrchestrator

        self.learning_config: LearningConfig = self.config.learning or LearningConfig()
        self.learning_cycle_orchestrator = LearningCycleOrchestrator(
            audit_repo=self.audit,
            ledger=self.experience_ledger,
            orchestrator=self.model_lifecycle_orchestrator,
            config=self.learning_config,
            snapshot_store=TrainingDatasetSnapshotStore(),
        )

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
            # OPERATOR RULING (2026-09-09): symbol whitelist from execution
            # config (default ["XAUUSD"]) — policy refuses candidates for
            # symbols outside it.
            enabled_symbols=config.execution.enabled_symbols,
        )
        # OBS-TRACE (2026-09-09): give the policy a zero-I/O provider for the
        # SERVING model identity so every EXEC_TRACE log line binds the
        # decision to model_id / version / artifact fingerprint. Reads only
        # in-memory bundle metadata; a missing bundle stamps
        # MODEL_IDENTITY_UNAVAILABLE (honest absence, never a fake hash).
        self.signal_policy.model_identity_fn = self._serving_model_identity
        # TASK-AUDREV-C3 gate (b) runtime wiring (NSE-Swarm 2026-09-11): bind
        # the read-only session spread-percentile provider so the gate is
        # LIVE. Before this, the C3 (b) policy hook existed (76eb23b9) but
        # NOTHING ever set session_spread_percentile_fn — the gate was a
        # permanent no-op in production (dead wiring; the audit's "cheapest
        # remaining real-P&L win" was silently disabled). Contract honored:
        #   * INV-001: the provider runs a bounded read-only SELECT inside
        #     the policy's per-evaluation call ONLY when a candidate is
        #     live-spread-positive — never a write, never a cached handle;
        #   * honest-unknown: a thin session sample (< min_samples) returns
        #     None and the policy treats the gate as a no-op (never 0.0);
        #   * reads the DURABLE audit_paper_executions copy (survives
        #     restarts), not the adapter's in-memory ledger (export source);
        #   * failure-isolated: a provider exception would surface inside the
        #     policy's evaluate — the broker_history implementation already
        #     returns None on sqlite3.Error, and this wrapper additionally
        #     clamps any unexpected fault to None so trading never breaks.
        self.signal_policy.session_spread_percentile_fn = self._session_spread_percentile_provider  # type: ignore[assignment]
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
            # BUG-256 (Agent-15 capital-protection fix): register THIS engine
            # as the persisted-safety-state authority for the dispatch layer.
            # DispatchEngine consults order_manager._trading_blocked_by_safety_state;
            # without this provider the persisted HALTED/KILL_SWITCH half of
            # the dispatch gate was inert (only the RiskEngine kill-switch
            # flag gated), so a drawdown halt that stopped the loop would not
            # stop a dispatch arriving through any other live path (hedge
            # router, recovery dispatch, web/CLI route regression).
            safety_state_provider=self._trading_blocked_by_safety_state,
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
        # TRAIN/SERVE PARITY (P0 2026-09-09): serving mode of the LOADED bundle
        # ("2d" default; "sequence" only for sequence-trained artifacts).
        self._live_sequence_trained_mode: str = "2d"
        self._retrain_interval_bars: int = 50
        self._bars_since_last_retrain: int = 0
        self._retrain_task: asyncio.Task | None = None
        self._retrain_inflight: bool = False
        # LEARNING-LOOP (Phase 6/10): config-driven, fail-closed online
        # fine-tune controls. The engine keeps its self-improving loop ONLY
        # when config.learning.online_finetune.enabled=True; the default
        # configuration leaves it disabled so the champion artifact is
        # immutable between governed promotions.
        self._online_finetune_enabled: bool = bool(
            getattr(getattr(self.config, "learning", None), "online_finetune", None)
            and self.config.learning
            and self.config.learning.enabled
            and self.config.learning.online_finetune.enabled
        )
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

    def _session_spread_percentile_provider(
        self,
        symbol: str,
        now_utc: datetime,
        percentile: float,
    ) -> float | None:
        """TASK-AUDREV-C3 gate (b): read-only session spread-percentile provider.

        Binds broker_history.session_spread_percentile to the audit DB the
        engine already owns. Called by SignalPolicy ONLY when a candidate is
        live-spread-positive (INV-001: still zero synchronous writes; a single
        bounded SELECT over audit_paper_executions with a same-UTC-day 4h
        window). Honest-unknown semantics: thin samples return None and the
        policy treats the gate as a no-op (never 0.0 fail-open). Failure
        isolation: any unexpected fault clamps to None — a spread-gate fault
        must never break a trading evaluation.
        """
        if not self.audit._is_sqlite or not self.audit._db_path:
            return None
        try:
            # Same connection surface the repository itself uses (URI-aware,
            # bounded timeout); the SELECT is read-only and sub-millisecond
            # on the indexed audit_paper_executions table.
            conn = self.audit._connect_sqlite(5.0)
            try:
                return session_spread_percentile(
                    conn,
                    symbol,
                    now_utc,
                    percentile,
                )
            finally:
                conn.close()
        except Exception as spread_err:
            logger.warning(
                "[SPREAD_GATE] event=SESSION_PCT_PROVIDER_FAILED (isolated) error=%s",
                spread_err,
            )
            return None

    def _serving_model_identity(self) -> tuple[str, str, str]:
        """OBS-TRACE (2026-09-09): identity of the bundle currently serving.

        Returns (model_id, model_version, artifact_fingerprint) from the
        loaded bundle's on-disk artifact (sha256 prefix via
        fingerprint_artifact) — or honest empty strings when no bundle is
        loaded / the artifact is absent (EXEC_TRACE stamps
        MODEL_IDENTITY_UNAVAILABLE, never a placeholder identity).
        """
        b = None
        with contextlib.suppress(Exception):
            with self._bundle_lock:
                b = self._bundle
        if b is None:
            return "", "", ""
        fp = ""
        with contextlib.suppress(Exception):
            fp = fingerprint_artifact(b.artifact_path)
        mid = getattr(getattr(b, "model", None), "model_id", "") or ""
        return (
            str(mid or ""),
            str(getattr(b, "model_version", "") or ""),
            str(fp or ""),
        )

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

    # ------------------------------------------------------------------
    # P1 seam L9: model-bundle load/verify/persist delegates. Implementation
    # lives in application/live/model_bundle_store.py (ModelBundleStore);
    # methods are invoked UNBOUND with the engine as the state surface so
    # instance monkeypatching / harness contracts keep working.
    # NOTE: __init__ keeps calling self._load_or_create_bundle(...) — the
    # BUG-182B init-order source contract (rebind AFTER load) is preserved.
    # ------------------------------------------------------------------

    def _load_or_create_bundle(self, **kw):
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._load_or_create_bundle(self, **kw)

    def _verify_champion_registry_binding(
        self, model_path, actual_bytes_hash=None
    ):  # P0-2 trust anchor delegate (engine surface -> ModelBundleStore seam)
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._verify_champion_registry_binding(
            self, model_path, actual_bytes_hash
        )

    @staticmethod
    def _artifact_meta_coherence(*args, **kwargs):
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._artifact_meta_coherence(*args, **kwargs)

    def _expected_num_features_for_artifact(self, *args, **kwargs):
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._expected_num_features_for_artifact(self, *args, **kwargs)

    def _load_or_initialize_model_weights(self, *args, **kwargs):
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._load_or_initialize_model_weights(self, *args, **kwargs)

    def _load_scaler_artifacts(self, *args, **kwargs):
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._load_scaler_artifacts(self, *args, **kwargs)

    def _save_model_weights_atomic(self, *args, **kwargs):
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._save_model_weights_atomic(self, *args, **kwargs)

    # ------------------------------------------------------------------
    # P1 seam L10: warmup delegates. Implementation lives in
    # application/live/warmup.py (WarmupService); methods are invoked UNBOUND
    # with the engine as the state surface (harness contract preserved).
    # ------------------------------------------------------------------

    def evaluate_warmup_readiness(self, *args, **kwargs):
        from nexus_scalp.application.live.warmup import WarmupService

        return WarmupService.evaluate_warmup_readiness(self, *args, **kwargs)

    async def _cold_start_warmup(self, *args, **kwargs):
        from nexus_scalp.application.live.warmup import WarmupService

        return await WarmupService.cold_start_warmup(self, *args, **kwargs)

    def _evaluate_champion_registry_sync(self, *args, **kwargs):
        """Delegate: pure registry-sync decision (owned by ChampionSync, L11)."""
        from nexus_scalp.application.live.champion_sync import ChampionSync

        return ChampionSync.evaluate_champion_registry_sync(self, *args, **kwargs)

    def _sync_champion_registry_state(self, *args, **kwargs) -> None:
        """Delegate: registry truth sync (owned by ChampionSync, L11)."""
        from nexus_scalp.application.live.champion_sync import ChampionSync

        ChampionSync.sync_champion_registry_state(self, *args, **kwargs)

    def _detect_model_collapse(self, *args, **kwargs):
        """Delegate: collapse detection (owned by ModelHealth, L12)."""
        from nexus_scalp.application.live.model_health import ModelHealth

        return ModelHealth.detect_model_collapse(self, *args, **kwargs)

    async def _reinitialize_collapsed_model(self, *args, **kwargs):
        """Delegate: collapsed-model recovery (owned by ModelHealth, L12)."""
        from nexus_scalp.application.live.model_health import ModelHealth

        return await ModelHealth.reinitialize_collapsed_model(self, *args, **kwargs)

    def set_execution_mode(self, *args, **kwargs) -> dict:
        """Delegate: operator mode switch (owned by RuntimeModeService, L13)."""
        from nexus_scalp.application.live.runtime_mode import RuntimeModeService

        return RuntimeModeService.set_execution_mode(self, *args, **kwargs)

    def _invalidate_cross_mode_state(self, *args, **kwargs) -> None:
        """Delegate: cross-mode state invalidation (RuntimeModeService, L13)."""
        from nexus_scalp.application.live.runtime_mode import RuntimeModeService

        return RuntimeModeService.invalidate_cross_mode_state(self, *args, **kwargs)

    async def hot_swap_model(self, new_artifact_path: str, *, source: str = "WEB_UI") -> dict:
        """Delegate: atomic serving-artifact swap (owned by HotSwapService, L4)."""
        eng = self._hot_swap
        return await eng.swap_model(new_artifact_path, source=source)

    @property
    def _hot_swap(self):
        """Lazily composed hot-swap service (P1 seam L4)."""
        eng = getattr(self, "_hot_swap_instance", None)
        if eng is None:
            from nexus_scalp.application.live.hot_swap import HotSwapService

            eng = HotSwapService(self)
            self._hot_swap_instance = eng
        return eng

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

    async def stop(self) -> None:
        self._running = False

    async def run_loop(self) -> None:
        """Delegate: async run loop (owned by RuntimeLoop, P1 seam L8)."""
        eng = self._runtime_loop
        await eng.run()

    @property
    def _runtime_loop(self):
        """Lazily composed runtime loop (P1 seam L8)."""
        eng = getattr(self, "_runtime_loop_instance", None)
        if eng is None:
            from nexus_scalp.application.live.runtime_loop import RuntimeLoop

            eng = RuntimeLoop(self)
            self._runtime_loop_instance = eng
        return eng

    async def _service_pipeline_workers(self, *, now_t: float) -> None:
        """BUG-169 duplicate-tick heartbeat: the time-throttled housekeeping
        duties MUST still run when the pipeline is skipped. Single owner is
        MaintenanceCycle (P1 seam L3); this shim keeps the call site."""
        await self._maintenance.run_cycle(now_t=now_t)

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
                probe = torch.load(model_path, map_location="cpu", weights_only=True)
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

    # -------------------------
    # Warmup + bootstrap training
    # -------------------------

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

    async def _resync_from_broker(self, symbol: str) -> None:
        """Broker-authoritative reseed after downtime / reconnect (BUG-054).

        * Re-fetches 20000 M1 bars (~14 days, or the engine's configured chart window).
        * Reseeds the aggregator (duplicate/stale minutes are dropped and the
          forming bar continues the broker's latest minute).
        * Recomputes the feature window so models/regime see a continuous
          series instead of a gap.
        * Pushes a fresh 900-bar snapshot + SMC overlays to ServerState so the
          UI immediately paints real broker candles.
        """
        chart_count = 20000
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
        # NOTE: the delegate is async (ModelHealth.reinitialize_collapsed_model
        # performs atomic bundle IO); this caller is async (_bootstrap_train_if_ready)
        # so we await it directly. A dropped coroutine here would silently skip
        # collapse recovery while the log claimed the hardening ran.
        if self._bundle is not None:
            await self._reinitialize_collapsed_model()

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
            # TASK-AUDREV-C3 gate (b) hot-reload parity (NSE-Swarm 2026-09-11):
            # the session-percentile gate toggle + threshold ride the runtime
            # snapshot like every other algo key. Without this, an operator
            # flipping algo.spread_session_gate_enabled / percentile in the
            # web UI silently waited for a restart (dead knob while live.yaml
            # said otherwise).
            self.signal_policy.spread_session_gate_enabled = bool(
                snap.algo.spread_session_gate_enabled
            )
            self.signal_policy.spread_session_percentile = float(
                snap.algo.spread_session_percentile
            )
            # AGENT-18 (D4): the operator symbol whitelist rides the runtime
            # snapshot — keep the policy gate in lockstep (hot-reload parity
            # with every other runtime key; a UI whitelist update must never
            # silently wait for a restart while live.yaml says otherwise).
            snap_symbols = tuple(snap.execution.enabled_symbols) or ("XAUUSD",)
            if tuple(self.signal_policy.enabled_symbols) != snap_symbols:
                self.signal_policy.enabled_symbols = list(snap_symbols)
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
            # P1 seam L14: pre-policy stage (runtime-config sync, liquidity
            # warmup, regime classification + freshness stamps, position
            # management, lifecycle timeline, warmup gate) moved to
            # application/live/tick_pipeline.py (TickPipeline).
            is_new_bar = self.aggregator.process_tick(tick)
            completed_bars = self.aggregator.get_completed_bars()
            (_continue, fv, proposal, probs, regime_state, active_positions, current_pos_count) = (
                self._tick_pipeline.run_pre_policy_stages(
                    tick=tick,
                    account=account,
                    is_new_bar=is_new_bar,
                    completed_bars=completed_bars,
                )
            )
            if not _continue:
                return
            # P1 seam L7: post-policy stages (PHASE 08/09 gates, PHASE 12 news
            # gate, BUG-169 terminal outcome, G29 freshness gate + instrumentation,
            # PHASE 11 shadow recording, BUG-105 70D hook, chart overlays) moved
            # to application/live/tick_pipeline.py (TickPipeline).
            proposal = self._tick_pipeline.run_post_policy_stages(
                tick=tick,
                account=account,
                fv=fv,
                probs=probs,
                regime_state=regime_state,
                proposal=proposal,
                active_positions=active_positions,
                current_pos_count=current_pos_count,
                completed_bars=completed_bars,
                is_new_bar=is_new_bar,
            )
            policy_decision = proposal
            # P1 seam L2: decision execution stage (BUG-212 shadow boundary,
            # reversal/entry dispatch, lifecycle actions, hedging, survival
            # audit) — implementation moved to application/live/decision_executor.py.
            self._decision_executor.execute_decision_stage(
                tick=tick,
                account=account,
                fv=fv,
                probs=probs,
                regime_state=regime_state,
                proposal=proposal,
                policy_decision=policy_decision,
                active_positions=active_positions,
                current_pos_count=current_pos_count,
            )
        except Exception as pipeline_err:
            # =================================================================
            # HOT-PATH CONSECUTIVE-ERROR CIRCUIT BREAKER (P1, runtime-safety
            # mission). A systematically broken pipeline must not run
            # LIVE-but-disabled forever: every failure feeds the
            # HotPathErrorCircuit; tripping degrades the engine (survival
            # mode = no NEW entries) while manage_active_positions keeps
            # protecting existing positions. Explicit recovery only.
            # =================================================================
            now_t = time.time()
            tripped = self._hot_path_circuit.record_error(now_t, pipeline_err)
            logger.error(
                "Hot-path tick pipeline exception "
                "consecutive=%d/%d window=%.0fs total=%d error_type=%s",
                self._hot_path_circuit.consecutive_error_count,
                self._hot_path_circuit.max_consecutive_errors,
                self._hot_path_circuit.error_window_sec,
                self._hot_path_circuit.total_errors,
                self._hot_path_circuit.last_error_type,
                exc_info=True,
            )
            if tripped and not self._survival_mode_active:
                self._survival_mode_active = True
                self.emit_incident_telemetry(
                    event_type="HOT_PATH_ERROR_CIRCUIT_TRIPPED",
                    component="tick_pipeline",
                    error_code="CONSECUTIVE_ERRORS",
                    severity="HIGH",
                    correlation_id="tick-pipeline",
                )
                logger.critical(
                    "[SAFETY_STATE] HOT-PATH CIRCUIT TRIPPED: %d consecutive errors "
                    "in %.0fs — new entries BLOCKED (DEGRADED); position protection "
                    "continues; explicit recovery required",
                    self._hot_path_circuit.consecutive_error_count,
                    self._hot_path_circuit.error_window_sec,
                )
                with contextlib.suppress(Exception):
                    self.notifier.notify_error(
                        "Hot-Path Circuit Breaker",
                        f"{self._hot_path_circuit.consecutive_error_count} consecutive "
                        "tick-pipeline errors — new trades blocked (DEGRADED)",
                    )

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
                    # BUG-252: forward the engine's authoritative peak so the
                    # drawdown risk cut engages (AccountInfo has no field).
                    peak_equity=getattr(self, "_peak_equity", None),
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

    def _validate_feature_vector(self, features, context: str) -> list:
        """Delegate: schema-gated validation (InferenceService, L6); unbound
        call keeps the harness/stand-in test contract on the real logic."""
        from nexus_scalp.application.live.inference import InferenceService

        return InferenceService.validate_feature_vector(self, features, context=context)

    def _build_live_feature_vector(self, fv) -> tuple:
        """Delegate: canonical live tensor assembly (InferenceService, L6)."""
        from nexus_scalp.application.live.inference import InferenceService

        return InferenceService.build_live_feature_vector(self, fv)

    def _infer_probabilities(self, fv):
        """Delegate: staged-latency inference (InferenceService, L6)."""
        from nexus_scalp.application.live.inference import InferenceService

        return InferenceService.infer_probabilities(self, fv)

    @property
    def _inference_service(self):
        """Lazily composed inference service (P1 seam L6)."""
        eng = getattr(self, "_inference_service_instance", None)
        if eng is None:
            from nexus_scalp.application.live.inference import InferenceService

            eng = InferenceService(self)
            self._inference_service_instance = eng
        return eng

    def _on_new_bar(self, tick: TickData, fv, last_bar) -> None:
        """Delegate: completed-bar processing (owned by BarHandler, L5)."""
        eng = self._bar_handler
        eng.on_new_bar(tick=tick, fv=fv, last_bar=last_bar)

    @property
    def _bar_handler(self):
        """Lazily composed bar handler (P1 seam L5)."""
        eng = getattr(self, "_bar_handler_instance", None)
        if eng is None:
            from nexus_scalp.application.live.bar_handler import BarHandler

            eng = BarHandler(self)
            self._bar_handler_instance = eng
        return eng

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

    @property
    def _decision_executor(self):
        """Lazily composed decision executor (P1 seam L2)."""
        eng = getattr(self, "_decision_executor_instance", None)
        if eng is None:
            from nexus_scalp.application.live.decision_executor import DecisionExecutor

            eng = DecisionExecutor(self)
            self._decision_executor_instance = eng
        return eng

    @property
    def _maintenance(self):
        """Lazily composed maintenance cycle (P1 seam L3)."""
        eng = getattr(self, "_maintenance_instance", None)
        if eng is None:
            from nexus_scalp.application.live.maintenance import MaintenanceCycle

            eng = MaintenanceCycle(self)
            self._maintenance_instance = eng
        return eng

    @property
    def _tick_pipeline(self):
        """Lazily composed post-policy pipeline (P1 seam L7)."""
        eng = getattr(self, "_tick_pipeline_instance", None)
        if eng is None:
            from nexus_scalp.application.live.tick_pipeline import TickPipeline

            eng = TickPipeline(self)
            self._tick_pipeline_instance = eng
        return eng

    def apply_command_intent(self, intent: dict) -> dict:
        """Authenticated operator intent boundary (Telegram command bus).

        INV-010: the Telegram layer never mutates canonical state itself;
        intents route through the EXISTING authority layers here
        (RiskEngine kill switch / governance rollback). Owned by
        application/command_intent.py (seam extraction, keep-this-file-thin).
        """
        from nexus_scalp.application.command_intent import apply_command_intent as _apply

        return _apply(self, intent)

    def _record_shadow_decision(
        self,
        tick: TickData,
        fv: Any,
        regime_state: MarketRegimeState,
        proposal: TradeProposal,
    ) -> None:
        """Delegate: 50D shadow recording (owned by ShadowRecorder, L1)."""
        from nexus_scalp.application.live.shadow_recorder import ShadowRecorder

        ShadowRecorder(self).record_shadow_decision(tick, fv, regime_state, proposal)

    def _record_shadow70_observation(
        self,
        tick: TickData,
        fv: Any,
        proposal: TradeProposal,
    ) -> None:
        """Delegate: 70D shadow observation (owned by ShadowRecorder, L1)."""
        from nexus_scalp.application.live.shadow_recorder import ShadowRecorder

        ShadowRecorder(self).record_shadow70_observation(tick, fv, proposal)

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
            # P1 ARTIFACT TRUST: the accepted persist rewrote model.pt IN
            # PLACE. Any integrity metadata beside it (manifest.json /
            # model.meta.json declaring model_sha256) now describes the OLD
            # bytes — the next cold load would fail closed with
            # HASH_MISMATCH. Refresh the sidecar digests BEFORE the bundle
            # swap so ACTIVE_ARTIFACT <=> ACTIVE_MANIFEST <=> ACTIVE_HASH
            # always refer to the same version (atomic pair semantics; a
            # sidecar refresh failure refuses the whole activation).
            if not self._refresh_artifact_integrity_metadata(bundle.artifact_path):
                logger.error(
                    "[ASYNC_RETRAIN_REFUSED] event=MANIFEST_REFRESH_REFUSED "
                    "reason=SIDECAR_DIGEST_UPDATE_FAILED (baseline kept on disk "
                    "is the NEW weights, in-memory baseline still serving)",
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

    # ------------------------------------------------------------------
    # LEARNING-LOOP HANDOFF (P1): narrow callback for the learning cycle.
    # ------------------------------------------------------------------

    def request_learning_cycle(
        self,
        *,
        trigger: str = "operator",
        num_epochs: int = 3,
        hyperparameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Runs ONE governed learning cycle behind config.learning (P1).

        Contract (narrow — no LiveEngine object crosses the boundary):
          * learning disabled -> refused with an explicit reason (default).
          * a cycle in flight -> refused (idempotent, no stacking).
          * online fine-tune unaffected: _online_finetune_enabled stays the
            ONLY gate for the direct serving-artifact path (default False).
          * shadow attach is delegated to _attach_learning_candidate, which
            reuses the EXISTING attach path (load_challenger +
            ShadowEngine.attach_challenger/start_run) — no parallel attach
            implementation, and ONLY when learning.shadow.enabled=true.
          * promotion is NEVER triggered here; the challenger merely becomes
            registry-CHALLENGER (shadow-eligible) and waits for the operator.
        """
        if self._retrain_inflight:
            return {"cycle": None, "blocked": "another training is in flight"}
        self._retrain_inflight = True
        try:
            return self.learning_cycle_orchestrator.run_cycle(
                trigger=trigger,
                shadow_attach=self._attach_learning_candidate
                if self.learning_config.shadow.enabled
                else None,
                num_epochs=num_epochs,
                hyperparameters=hyperparameters,
            )
        finally:
            self._retrain_inflight = False

    def _attach_learning_candidate(
        self,
        cycle_id: str,
        candidate_model_id: str,
        artifact_path: str,
    ) -> str:
        """Shadow attach for a VALIDATED challenger (learning-loop P1).

        Reuses the existing attach path exactly as /api/models/shadow/attach:
        10-gate load gate -> load_challenger -> ShadowEngine.attach_challenger
        -> start_run. Raises on any failure so the cycle records BLOCKED.
        """
        from nexus_scalp.governance.load_gate import ModelLoadGate, read_manifest_file
        from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry
        from nexus_scalp.shadow.challenger import load_challenger
        from nexus_scalp.shadow.models import ShadowModelRef

        path = Path(artifact_path)
        scaler = Path(str(path) + ".scaler.npz")
        registry = ModelLifecycleRegistry(audit_repo=self.audit, model_registry=self.model_registry)
        row = registry.get_status(candidate_model_id, candidate_model_id)
        status = str((row or {}).get("lifecycle_status", ""))
        if status not in ("CHALLENGER", "CANDIDATE"):
            raise RuntimeError(f"candidate registry status invalid: {status or 'MISSING'}")
        manifest = read_manifest_file(path.parent / "model.json") or {}
        gate = ModelLoadGate(db_path=self.audit._db_path).evaluate(
            artifact_path=path,
            scaler_path=scaler,
            model_id=candidate_model_id,
            model_version=candidate_model_id,
            manifest=manifest,
            lifecycle_state=status,
        )
        if not gate.passed:
            raise RuntimeError(f"candidate load gate rejected: {gate.failing_gate}")
        runtime = load_challenger(
            artifact_path=path,
            scaler_path=scaler,
            model_id=candidate_model_id,
            model_version=candidate_model_id,
            live_schema_id=self.effective_feature_schema_id,
            live_dimension=int(self.effective_feature_dim),
        )
        self._shadow_challenger = runtime
        self.shadow_engine.attach_challenger(runtime)
        champ = self.champion_manager.champion_or_none()
        champ_ref = (
            ShadowModelRef(
                model_id=champ.model_id,
                model_version=champ.model_version,
                feature_schema_id=champ.feature_schema_id,
                feature_dimension=champ.feature_dimension,
                artifact_hash=champ.artifact_hash,
                is_champion=True,
            )
            if champ
            else ShadowModelRef(model_id="none", model_version="")
        )
        return self.shadow_engine.start_run(
            run_id=None,
            champion=champ_ref,
            challenger_ref=runtime.ref or ShadowModelRef(model_id="none", model_version=""),
        )

    @staticmethod
    def _refresh_artifact_integrity_metadata(model_path: Path) -> bool:
        """P1: after an in-place accepted persist, re-bind every integrity
        sidecar (manifest.json / model.meta.json) to the NEW weight digest.

        Sidecars are updated ATOMICALLY (tmp+replace) and only ever gain a
        fresh model_sha256 — provenance fields are preserved. Returns False
        (refusing activation) when a declared sidecar cannot be refreshed,
        so the engine never activates a pair whose manifest still describes
        the previous artifact.
        """
        import hashlib as _hashlib
        import json as _json

        digest = ""
        try:
            h = _hashlib.sha256()
            with open(model_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            digest = h.hexdigest()
        except OSError as e:
            logger.error("[ARTIFACT_META] event=DIGEST_COMPUTE_FAILED", error=str(e))
            return False
        refreshed_any = False
        for sidecar_name in ("manifest.json", "model.meta.json"):
            sidecar = model_path.parent / sidecar_name
            if not sidecar.exists():
                continue
            try:
                record = _json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                logger.error(
                    "[ARTIFACT_META] event=SIDECAR_UNREADABLE sidecar=%s error=%s",
                    sidecar_name,
                    str(e),
                )
                return False
            if not isinstance(record, dict):
                logger.error("[ARTIFACT_META] event=SIDECAR_INVALID sidecar=%s", sidecar_name)
                return False
            if not (record.get("model_sha256") or record.get("artifact_hash")):
                continue  # sidecar declares no weight digest: nothing to rebind
            changed = False
            for key in ("model_sha256", "artifact_hash"):
                if key in record and str(record[key]).lower() != digest:
                    record[key] = digest
                    changed = True
            if not changed:
                refreshed_any = True
                continue
            tmp = sidecar.with_name(sidecar.name + ".tmp")
            try:
                tmp.write_text(_json.dumps(record, indent=2), encoding="utf-8")
                tmp.replace(sidecar)
            except OSError as e:
                logger.error(
                    "[ARTIFACT_META] event=SIDECAR_WRITE_FAILED sidecar=%s error=%s",
                    sidecar_name,
                    str(e),
                )
                with contextlib.suppress(Exception):
                    tmp.unlink(missing_ok=True)
                return False
            refreshed_any = True
            logger.info(
                "[ARTIFACT_META] event=SIDECAR_REBOUND sidecar=%s sha256=%s",
                sidecar_name,
                digest[:12],
            )
        logger.info(
            "[ARTIFACT_META] event=INTEGRITY_METADATA_REFRESHED artifact=%s sha256=%s sidecars=%s",
            model_path.name,
            digest[:12],
            refreshed_any,
        )
        return True

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

    def _persist_runtime_risk_state(
        self,
        *,
        state: str,
        reason: str,
        source: str,
        account: AccountInfo | None = None,
        release_required: bool = True,
    ) -> bool:
        """Persists the canonical safety decision through the audit store.

        Crash-safe: the store performs one atomic single-row upsert (old
        valid state or new valid state — never half-written). Returns False
        on persistence failure; the in-memory state is still updated by the
        caller so the running process fails safe even if persistence failed
        (which is logged CRITICAL by the store).
        """
        try:
            return bool(
                self.audit.set_runtime_risk_state(
                    state=state,
                    reason=reason,
                    source=source,
                    balance=float(getattr(account, "balance", 0.0) or 0.0),
                    equity=float(getattr(account, "equity", 0.0) or 0.0),
                    peak_equity=float(getattr(self, "_peak_equity", 0.0) or 0.0),
                    release_required=release_required,
                    consecutive_losses=int(getattr(self, "_consecutive_losses", 0) or 0),
                )
            )
        except Exception as persist_err:
            logger.critical(
                "RUNTIME SAFETY STATE PERSIST FAILED state=%s error=%s", state, persist_err
            )
            return False

    def _apply_persisted_halt(self, decision: BootDecision) -> None:
        """Adopts a resolved boot decision into the live engine (fail closed).

        HALTED / KILL_SWITCH: trading is refused for the whole process
        lifetime — the in-memory gate mirrors the persisted row and only
        release_runtime_risk_state (operator CLI / audited API) may lift it.
        """
        self._runtime_risk_state = decision.state
        self._runtime_risk_detail = decision.detail
        if decision.state in ("HALTED", "KILL_SWITCH"):
            # Do NOT start the trading loop: restore-first contract. The
            # engine idles (run_loop returns before arming _running) and the
            # operator sees the persisted safety state, not a silent start.
            self._running = False
            self._halt_reason = decision.detail
            logger.critical(
                "[SAFETY_STATE] persisted=%s trading=REFUSED detail=%s "
                "(explicit release required: nexus risk release --confirm)",
                decision.state,
                decision.detail,
            )
            with contextlib.suppress(Exception):
                self.notifier.notify_kill_switch_activated(
                    f"Persisted {decision.state} restored at startup — "
                    "trading disabled until explicit release"
                )
            self.emit_incident_telemetry(
                event_type="PERSISTED_SAFETY_STATE_RESTORED",
                component="runtime_safety",
                error_code=decision.state,
                severity="CRITICAL",
                correlation_id="startup",
            )

    def _restore_runtime_risk_state(self) -> BootDecision:
        """Boot resolution: restore persisted safety state BEFORE trading.

        Called at the very start of run_loop. Never recalculates drawdown —
        only the persisted decision decides.

        AGENT-17 BOOT-TRUST CONTRACT (2026-09-10, reland on main): a FAILED
        read of the persisted state (corrupt image / lock storm / unavailable
        audit DB) is NOT 'no persisted state'. The store raises
        ``RuntimeRiskStateReadError``; this boot path resolves it to a
        fail-closed ``DB_READ_UNCERTAIN`` decision so the engine idles until
        the durable state can actually be trusted. Database uncertainty is
        NEVER decoded as RUNNING.
        """
        try:
            row = self.audit.get_runtime_risk_state()
        except RuntimeRiskStateReadError as err:
            logger.critical("[SAFETY_STATE] persisted state READ FAILED — failing CLOSED (%s)", err)
            decision = BootDecision(
                trading_allowed=False,
                state="DB_READ_UNCERTAIN",
                detail=f"PERSISTED_STATE_READ_FAILED: {err}",
            )
        else:
            decision = resolve_boot_decision(PersistedRiskState.from_row(row))
        self._apply_persisted_halt(decision)
        if decision.state == "RUNNING":
            # Mirror the RUNNING decision back durably (single canonical row,
            # first boot writes it; later boots keep provenance fresh).
            self._persist_runtime_risk_state(
                state="RUNNING",
                reason="",
                source="BOOT",
                release_required=False,
            )
            logger.info("[SAFETY_STATE] boot decision=RUNNING (trading permitted)")
        return decision

    def trigger_runtime_halt(
        self,
        *,
        reason: str,
        source: str,
        state: str = "HALTED",
        account: AccountInfo | None = None,
    ) -> None:
        """The ONE canonical halt entrypoint for real trading events.

        Order matters (HALT INVARIANT):
        1. persist the safety state durably (crash-safe atomic upsert),
        2. stop new trading (in-memory gate + loop flag),
        3. record an incident/audit event,
        4. expose observable runtime state (attributes read by the UI),
        5. the persisted row guarantees survival across restart.
        """
        # 1) PERSIST (first — a crash after this point still leaves the
        #    decision durable).
        persisted = self._persist_runtime_risk_state(
            state=state, reason=reason, source=source, account=account
        )
        if not persisted:
            logger.critical(
                "SAFETY HALT persistence failed — halting in-memory anyway "
                "(fail safe, operator MUST be notified)"
            )
        # 2) STOP NEW TRADING (in-memory gate mirrors the persisted decision).
        self._runtime_risk_state = state
        self._halt_reason = reason
        self._halt_triggered_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._runtime_risk_detail = reason
        self._running = False
        # 3) RECORD INCIDENT (observability-only; never mutates risk).
        self.emit_incident_telemetry(
            event_type="RUNTIME_SAFETY_HALT",
            component="runtime_safety",
            error_code=state,
            severity="CRITICAL",
            correlation_id=str(source or "runtime"),
        )
        # 4) NOTIFY + OBSERVE.
        logger.critical(
            "[SAFETY_STATE] event=HALT state=%s reason=%s source=%s persisted=%s",
            state,
            reason,
            source,
            persisted,
        )
        with contextlib.suppress(Exception):
            self.notifier.notify_kill_switch_activated(f"{state}: {reason}")

    def runtime_risk_state(self) -> str:
        """Observable canonical safety state (UI/health surface).

        DEGRADED (session-local: hot-path circuit / stale account / loss
        freeze) is derived on the fly and never overrides a persisted halt.
        """
        if self._runtime_risk_state in ("HALTED", "KILL_SWITCH"):
            return self._runtime_risk_state
        if (
            self._loss_freeze_active
            or self._hot_path_circuit.is_tripped(time.time())
            or (self._account_freshness == AccountFreshness.STALE.value)
        ):
            return "DEGRADED"
        return self._runtime_risk_state

    def release_persisted_safety_state(self, *, actor: str, note: str = "") -> bool:
        """In-process explicit release (audited, durable, observable).

        Wraps AuditRepository.release_runtime_risk_state; used by the CLI
        release command. A normal restart never reaches this method.
        """
        released = bool(self.audit.release_runtime_risk_state(actor=actor, note=note))
        if released:
            self._runtime_risk_state = "RUNNING"
            self._halt_reason = ""
            self._runtime_risk_detail = ""
            self._hot_path_circuit.reset()
            self._loss_freeze_active = False
            logger.info("[SAFETY_STATE] event=RELEASED actor=%s note=%s", actor, note)
            self.emit_incident_telemetry(
                event_type="RUNTIME_SAFETY_RELEASED",
                component="runtime_safety",
                severity="HIGH",
                correlation_id=str(actor or "operator"),
            )
        return released

    def _trading_blocked_by_safety_state(self) -> bool:
        """True when the persisted safety state refuses new trading."""
        return self._runtime_risk_state in ("HALTED", "KILL_SWITCH")

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
                # PERSISTED SAFETY HALT (P0): the old behavior only set
                # self._running = False — a restart silently FORGOT the
                # drawdown event and resumed trading. The canonical halt
                # entrypoint persists the decision FIRST (crash-safe), then
                # stops trading, records the incident and exposes state.
                self.trigger_runtime_halt(
                    reason=f"Max drawdown exceeded: {drawdown_pct:.2f}% > limit "
                    f"{dd_limit_pct:.2f}%",
                    source="SURVIVAL_DRAWDOWN_GUARD",
                    state="HALTED",
                    account=account,
                )

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
