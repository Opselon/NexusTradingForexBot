"""Runtime 70D Feature Hook — guarded, observability-only (TASK-03-70D-PARITY).

WHY THIS EXISTS
---------------
The live engine (application/live_engine.py) is the HOT path and is heavily
worked by parallel agents. This module provides the canonical 70D live
assembly + validation as a STANDALONE guarded hook that the live engine can
call WITHOUT changing its trading path:

    LiveEngine (canonical state: completed bars, news context)
        -> runtime_70d_snapshot()
        -> Feature70Snapshot (canonical 70D vector + provenance)
        -> InferenceValidator (full chain, cached metadata)
        -> [FEATURE_CONTRACT] structured trace / metrics
        -> model compatibility check (never feeds a mismatched model)

INVARIANTS
----------
1. OBSERVABILITY-ONLY: never touches orders/SL/TP/RiskEngine/execution.
2. NO DB on the hot path: schema metadata + validator cached at construction
   (INV-001); per-snapshot validation is pure (no file/db/network).
3. NO FAKE VALUES: a missing family raises or returns an explicit
   FEATURE_UNAVAILABLE status — never a fabricated vector.
4. THREAD-SAFE: `threading.RLock` around mutable runtime state (toggles).

The hook is NOT wired into live_engine in this commit — TASK-05's shadow70
already owns the live 70D observation hook (INV-018) and this module is the
canonical assembly/validation provider BOTH paths can share.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.features.features70 import (
    Feature70Snapshot,
    FeatureSourceState,
    assemble_70d,
)
from nexus_scalp.features.inference_validator import (
    InferenceValidator,
    RejectionCode,
    ScalerContract,
    ValidationResult,
    compatible_model_schema,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.features.runtime70")

TRACE_EVENT: str = "[FEATURE_CONTRACT]"


@dataclass
class Runtime70Config:
    """Immutable constructor config for the runtime hook."""

    symbol: str = "XAUUSD"
    timeframe: str = "M1"
    expected_schema_id: str = "scalp_v3"
    expected_dimension: int = 70
    max_age_seconds: float = 30.0
    scaler_dimension: int | None = 70
    scaler_hash: str = ""


@dataclass
class Runtime70Result:
    """Outcome of one live 70D snapshot attempt."""

    ok: bool
    snapshot: Feature70Snapshot | None = None
    validation: ValidationResult | None = None
    rejection_code: RejectionCode | None = None
    reason: str = ""
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "rejection_code": self.rejection_code.value if self.rejection_code else None,
            "reason": self.reason,
            "timings_ms": self.timings_ms,
        }
        if self.snapshot is not None:
            out["snapshot"] = self.snapshot.as_dict()
        if self.validation is not None:
            out["validation"] = self.validation.to_dict()
        return out


class Runtime70Hook:
    """Guarded canonical 70D live hook (thread-safe, observability-only)."""

    def __init__(
        self,
        config: Runtime70Config | None = None,
        *,
        news_provider: Any = None,
        liquidity_enabled: bool = True,
        news_enabled: bool = True,
    ) -> None:
        self.config = config or Runtime70Config()
        self._lock = threading.RLock()
        self._news_provider = news_provider
        self._liquidity_enabled = liquidity_enabled
        self._news_enabled = news_enabled
        self._model_id = ""
        self._model_version = ""
        self._model_schema_id = ""
        self._model_dimension: int | None = None
        # Cached immutable schema metadata (brief 31 — never rebuilt per tick).
        self._validator = InferenceValidator(
            expected_schema_id=self.config.expected_schema_id,
            expected_dimension=self.config.expected_dimension,
            scaler=(
                ScalerContract(dimension=self.config.scaler_dimension, hash=self.config.scaler_hash)
                if self.config.scaler_dimension is not None
                else None
            ),
            max_age_seconds=self.config.max_age_seconds,
        )

    # -- runtime state ------------------------------------------------------
    def set_model(
        self,
        schema_id: str,
        dimension: int,
        *,
        model_id: str = "",
        model_version: str = "",
    ) -> dict[str, Any]:
        """Attaches the currently loaded model's contract (explicit)."""
        with self._lock:
            self._model_schema_id = schema_id
            self._model_dimension = dimension
            self._model_id = model_id
            self._model_version = model_version
        return self.model_compatibility()

    def set_toggles(
        self, *, liquidity_enabled: bool | None = None, news_enabled: bool | None = None
    ) -> dict[str, Any]:
        with self._lock:
            if liquidity_enabled is not None:
                self._liquidity_enabled = liquidity_enabled
            if news_enabled is not None:
                self._news_enabled = news_enabled
        return self.to_state_dict()

    # -- canonical live snapshot --------------------------------------------
    def compute_snapshot(
        self,
        *,
        completed_bars: list[Any],
        base50: list[float] | None = None,
        news_context: dict[str, float] | None = None,
        base50_provider: Any = None,
        timestamp_utc: datetime | None = None,
        context: str = "live",
    ) -> Runtime70Result:
        """Builds + validates one canonical live 70D snapshot.

        ``completed_bars`` are the live engine's canonical completed bars;
        ``base50`` may be supplied by the caller (already computed by the live
        engine) or computed by ``base50_provider`` (callable(bars, tick)).
        """
        t0 = time.perf_counter()
        with self._lock:
            liq_enabled = self._liquidity_enabled
            news_enabled = self._news_enabled
            model_schema_id = self._model_schema_id
            model_dim = self._model_dimension
            model_id = self._model_id
            model_version = self._model_version

        # --- model compatibility (brief 26) --------------------------------
        if model_schema_id and model_dim is not None:
            compat = compatible_model_schema(
                model_schema_id,
                model_dim,
                self.config.expected_schema_id,
                self.config.expected_dimension,
            )
            if compat["result"] != "PASS":
                return Runtime70Result(
                    ok=False,
                    rejection_code=RejectionCode.SCHEMA_MISMATCH,
                    reason=f"MODEL_INPUT_UNAVAILABLE: {compat['reason']}",
                )

        # --- base 50D -------------------------------------------------------
        if base50 is None:
            if base50_provider is None:
                return Runtime70Result(
                    ok=False,
                    rejection_code=RejectionCode.SCHEMA_MISMATCH,
                    reason="BASE50_PROVIDER_REQUIRED",
                )
            base50 = list(base50_provider())
        if len(base50) != 50:
            return Runtime70Result(
                ok=False,
                rejection_code=RejectionCode.DIMENSION_MISMATCH,
                reason=f"BASE_50D_EXPECTED_GOT_{len(base50)}",
            )
        t_base = time.perf_counter()

        # --- news 10D ------------------------------------------------------
        news_status: FeatureSourceState
        if not news_enabled:
            news10 = [0.0] * 10
            news_status = FeatureSourceState.FEATURE_DISABLED
        elif news_context is None:
            news10 = [0.0] * 10
            news_status = FeatureSourceState.FEATURE_UNAVAILABLE
        else:
            from nexus_scalp.features.features70 import news_10d_from_context

            news10 = news_10d_from_context(news_context)
            news_status = FeatureSourceState.FEATURE_AVAILABLE
        t_news = time.perf_counter()

        # --- liquidity 10D ---------------------------------------------------
        liquidity_status: FeatureSourceState
        if not liq_enabled:
            liq10 = [0.0] * 10
            liquidity_status = FeatureSourceState.FEATURE_DISABLED
        elif not completed_bars:
            liq10 = [0.0] * 10
            liquidity_status = FeatureSourceState.FEATURE_UNAVAILABLE
        else:
            from nexus_scalp.features.liquidity_engine import compute_liquidity_features
            from nexus_scalp.features.scalp_features import ScalpFeatureEngine

            engine = ScalpFeatureEngine(symbol=self.config.symbol)
            last = completed_bars[-1]
            from nexus_scalp.domain.models import TickData

            tick = TickData(
                symbol=self.config.symbol,
                timestamp=getattr(last, "timestamp", timestamp_utc or datetime.now(UTC)),
                bid=getattr(last, "close", 0.0),
                ask=getattr(last, "close", 0.0) + 0.20,
                volume=getattr(last, "tick_volume", 0),
            )
            fv = engine.compute_from_bars(completed_bars, tick)
            decision = tick.timestamp
            if decision.tzinfo is None:
                decision = decision.replace(tzinfo=UTC)
            liquid = compute_liquidity_features(
                completed_bars,
                decision_at=decision,
                mid_price=float(getattr(last, "close", 0.0)),
                atr=fv.atr_m1,
            )
            liq10 = list(liquid.as_vector())
            liquidity_status = FeatureSourceState.FEATURE_AVAILABLE
        t_liq = time.perf_counter()

        # --- assemble + validate -------------------------------------------
        snap = assemble_70d(
            base50=base50,
            news10=news10,
            liquidity10=liq10,
            symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            timestamp_utc=timestamp_utc or datetime.now(UTC),
            news_available=news_status == FeatureSourceState.FEATURE_AVAILABLE,
            liquidity_available=liquidity_status == FeatureSourceState.FEATURE_AVAILABLE,
            news_status=news_status,
            liquidity_status=liquidity_status,
            model_id=model_id,
            model_version=model_version,
        )
        validation = self._validator.validate(
            snap.feature_vector,
            actual_schema_id=snap.schema_id,
            news_status=snap.news_status.value,
            liquidity_status=snap.liquidity_status.value,
            timestamp_utc=snap.timestamp_utc,
            context=context,
        )
        t_val = time.perf_counter()
        timings = {
            "base_calculation_ms": round((t_base - t0) * 1e3, 3),
            "news_calculation_ms": round((t_news - t_base) * 1e3, 3),
            "liquidity_calculation_ms": round((t_liq - t_news) * 1e3, 3),
            "vector_assembly_ms": round((t_val - t_liq) * 1e3, 3),
            "schema_validation_ms": round((time.perf_counter() - t_val) * 1e3, 3),
            "total_ms": round((time.perf_counter() - t0) * 1e3, 3),
        }

        if not validation.ok:
            logger.warning(
                "%s event=REJECTED reason=%s expected=%s actual=%s correlation_id=%s "
                "model_id=%s model_version=%s",
                TRACE_EVENT,
                validation.code.value if validation.code else "UNKNOWN",
                self.config.expected_schema_id,
                snap.schema_id,
                context,
                model_id,
                model_version,
            )
            return Runtime70Result(
                ok=False,
                snapshot=snap,
                validation=validation,
                rejection_code=validation.code,
                reason=validation.reason,
                timings_ms=timings,
            )

        logger.info(
            "%s schema=%s dimension=%d schema_hash=%s news=%s liquidity=%s status=VALID "
            "total_ms=%.1f",
            TRACE_EVENT,
            snap.schema_id,
            len(snap.feature_vector),
            snap.schema_hash(),
            snap.news_status.value,
            snap.liquidity_status.value,
            timings["total_ms"],
        )
        return Runtime70Result(ok=True, snapshot=snap, validation=validation, timings_ms=timings)

    # -- state / API (brief 27: UI shows the same state the engine uses) ----
    def to_state_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": self.config.expected_schema_id,
                "dimension": self.config.expected_dimension,
                "liquidity_enabled": self._liquidity_enabled,
                "news_enabled": self._news_enabled,
                "model_id": self._model_id,
                "model_version": self._model_version,
                "model_schema_id": self._model_schema_id,
                "model_dimension": self._model_dimension,
                "schema_hash": self._validator.expected_schema_hash,
            }

    def model_compatibility(self) -> dict[str, Any]:
        with self._lock:
            return compatible_model_schema(
                self._model_schema_id or None,
                self._model_dimension,
                self.config.expected_schema_id,
                self.config.expected_dimension,
            )
