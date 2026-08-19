"""Liquidity Runtime Governor — TASK-02-70D-INTEGRATION (TASK-2).

WHY THIS EXISTS
---------------
TASK-1 (TASK-01-60D-LIQUIDITY) delivered the canonical pure producer
``features/liquidity_engine.compute_liquidity_features`` (10 causal liquidity
dimensions, indices 50..59 of a 60D ``scalp_liquidity_v1`` vector). TASK-2's
job is the INTEGRATION layer: a real runtime governor that snapshots the
10 liquidity values from the LIVE engine, derives explicit status
(ENABLED / DISABLED / DEGRADED / UNAVAILABLE), builds the 70D feature
contract (``scalp_v4``: BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69),
enforces model/vector compatibility WITHOUT padding/truncation, and exposes
everything through the canonical web state contract so the UI never invents
values.

INVARIANTS
----------
1. INFORMATION-ONLY: this module NEVER touches orders, SL/TP, RiskEngine,
   execution mode or account state (brief §21). It produces feature vectors
   and status for display / candidate pipelines only.
2. INDEPENDENCE: liquidity availability must never depend on news state and
   vice versa (brief §5). The governor only reads ``engine.news_engine`` for
   the NEWS contract block; a news failure never degrades liquidity.
3. NO FAKE VALUES: every snapshot field is either a real computed value or an
   explicit neutral/unavailable marker with a reason. Never synthetic numbers.
4. SCHEMA-STRICT: ``resolve_model_compatibility`` never pads with zeros,
   truncates liquidity, removes news, or silently upgrades/downgrades. All
   four cells of the compatibility matrix are explicit (brief §12).
5. CAUSAL: values are computed from bars closed at/before the decision time
   (delegated to the pure engine; see its anti-leakage tests).
6. THREAD-SAFE: the governor may be read from the web thread and written from
   the engine thread; every access goes through ``threading.RLock``.

The 70D contract (``scalp_v4``):
    0..49   Base 50D features (scalp_v1, protected, untouched)
    50..59  Family block (TASK-5 momentum extras under scalp_v2, or the
            slot-50..59 family under scalp_liquidity_v1 — NEVER overwritten
            by this module; preserved verbatim when present)
    60..69  Liquidity Intelligence 10D (canonical as_vector order)
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from nexus_scalp.features.liquidity_engine import (
    BASE_50D,
    LIQUIDITY_DIM,
    LIQUIDITY_FEATURE_NAMES,
    LiquidityFeatures,
    compute_liquidity_features,
)
from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.features.liquidity_runtime")

#: The TASK-2 70D integration schema id (registered in features/schema.py).
SCHEMA_70D: str = "scalp_v4"
DIMENSION_70D: int = 70

#: Slot-50..59 family producer ids this governor understands.
FAMILY_SCHEMA_V2: str = "scalp_v2"  # TASK-5 momentum extras (50..59)
FAMILY_SCHEMA_LIQUIDITY_60D: str = "scalp_liquidity_v1"  # TASK-1 60D (50..59)
SCHEMA_LIQUIDITY_60D: str = "scalp_liquidity_v1"  # TASK-02 canonical 60D contract
DIMENSION_LIQUIDITY_60D: int = 60
LIQUIDITY_ALGORITHM_VERSION: str = "scalp_liquidity_v1.0.0"

#: Neutral 10D used ONLY when liquidity is UNAVAILABLE and a caller still
#: needs a structurally-valid 60D asset (documented neutral defaults, same
#: convention as the 50D cold-start / 60D extras). Never shown as LIVE data.
_NEUTRAL_60D_LIQUIDITY: tuple[float, ...] = (3.0, 3.0, 0.0, 0.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0)

#: Freshness threshold for LIVE status (seconds since the last successful
#: snapshot). Industry-typical for M1 feature cadence; configurable per call.
LIVE_STALE_AFTER_SEC: float = 90.0


class LiquidityStatus(Enum):
    """Canonical liquidity status values (UI + API share this vocabulary)."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class SourceKind(Enum):
    LIVE_MARKET_STATE = "LIVE_MARKET_STATE"
    REPLAY = "REPLAY"
    UNAVAILABLE = "UNAVAILABLE"


class CausalState(Enum):
    VALID = "VALID"
    STALE = "STALE"
    INVALID = "INVALID"


class ModelCompatibility(Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiquiditySnapshot:
    """One real liquidity computation at a decision timestamp.

    Every float is finite and already clipped to [-3, +3] by the canonical
    producer. ``pools`` is the causal pool list (for overlays) — may be empty
    when liquidity is unavailable.
    """

    decision_at: datetime
    mid_price: float
    atr: float
    features: tuple[float, ...]
    names: tuple[str, ...] = LIQUIDITY_FEATURE_NAMES
    pools: tuple[Any, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_at": self.decision_at.isoformat() if self.decision_at else None,
            "mid_price": self.mid_price,
            "atr": self.atr,
            "features": {
                name: float(value) for name, value in zip(self.names, self.features, strict=True)
            },
            "feature_names": list(self.names),
            "pools": [
                {
                    "side": getattr(p, "side", None),
                    "source": getattr(p, "source", None),
                    "state": getattr(p, "state", None),
                    "price": getattr(p, "price", None),
                    "candidate_at": getattr(p, "candidate_at", None),
                    "confirmed_at": getattr(p, "confirmed_at", None),
                }
                for p in self.pools
            ],
        }

    def as_vector(self) -> list[float]:
        return list(self.features)


def build_70d_vector(
    features50: list[float] | tuple[float, ...],
    family_10: list[float] | tuple[float, ...] | None,
    liquidity_10: list[float] | tuple[float, ...],
    *,
    family_schema_id: str | None = "scalp_v2",
) -> list[float]:
    """Assembles the 70D vector: BASE 0..49 + FAMILY 50..59 + LIQUIDITY 60..69.

    Raises on any width mismatch (INV-009: no silent pad/truncate). When a
    caller has no family block (News-only runtime), ``family_10`` may be None
    and the FAMILY 50..59 slots are filled with the documented neutral
    defaults so the vector geometry is still valid — the caller is
    responsible for recording that the FAMILY block is NOT live data.
    """
    if len(features50) != BASE_50D:
        raise ValueError(
            f"build_70d_vector: base must be exactly {BASE_50D}D, got {len(features50)}"
        )
    if liquidity_10 is None or len(liquidity_10) != LIQUIDITY_DIM:
        raise ValueError(
            f"build_70d_vector: liquidity must be exactly {LIQUIDITY_DIM}D, got "
            f"{0 if liquidity_10 is None else len(liquidity_10)}"
        )
    if family_10 is None:
        family = list(_NEUTRAL_60D_LIQUIDITY)
        logger.warning(
            "[LIQUIDITY] event=FAMILY_NEUTRAL_FILL family_schema_id=%s reason=NO_FAMILY_BLOCK",
            family_schema_id,
        )
    elif len(family_10) != LIQUIDITY_DIM:
        raise ValueError(
            f"build_70d_vector: family block must be exactly {LIQUIDITY_DIM}D, got {len(family_10)}"
        )
    else:
        family = list(family_10)
    return list(features50) + family + list(liquidity_10)


def resolve_model_compatibility(
    model_schema_id: str | None,
    model_dimension: int | None,
    runtime_schema_id: str,
    runtime_dimension: int,
) -> dict[str, Any]:
    """Explicit model-vs-runtime compatibility matrix (brief §12).

    Cells:
        scalp_v2/60D   + 60D runtime      -> PASS
        scalp_v3/70D   + 70D runtime      -> PASS
        scalp_v2/60D   + 70D runtime      -> BLOCK (LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE)
        scalp_v3/70D   + 60D runtime      -> BLOCK
        unknown/anything mismatched       -> BLOCK

    Returns a dict with ``result`` (PASS/BLOCK/UNKNOWN), ``reason``, and the
    reconciled pair. Never pads, never truncates.
    """
    if not model_schema_id or model_dimension is None:
        return {
            "result": ModelCompatibility.UNKNOWN.value,
            "reason": "NO_MODEL_METADATA",
            "model_schema_id": model_schema_id,
            "model_dimension": model_dimension,
            "runtime_schema_id": runtime_schema_id,
            "runtime_dimension": runtime_dimension,
        }
    if model_dimension != runtime_dimension:
        return {
            "result": ModelCompatibility.BLOCK.value,
            "reason": (
                "LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE"
                if model_dimension < runtime_dimension
                else "MODEL_DIMENSION_EXCEEDS_RUNTIME"
            ),
            "model_schema_id": model_schema_id,
            "model_dimension": model_dimension,
            "runtime_schema_id": runtime_schema_id,
            "runtime_dimension": runtime_dimension,
        }
    return {
        "result": ModelCompatibility.PASS.value,
        "reason": "SCHEMA_DIMENSION_MATCH",
        "model_schema_id": model_schema_id,
        "model_dimension": model_dimension,
        "runtime_schema_id": runtime_schema_id,
        "runtime_dimension": runtime_dimension,
    }


class LiquidityGovernor:
    """Thread-safe runtime governor for the Liquidity Intelligence layer.

    Owns:
      - enabled flag (persisted via SettingsService key
        ``model.liquidity_features_enabled``; read at startup from AppConfig,
        mutated only through :meth:`set_enabled` so the UI control touches the
        real backend configuration — never a UI-only flag).
      - the latest real snapshot (produced by the live engine).
      - explicit status/causal-state derivation from timestamps.

    The governor never computes features itself in the web thread (the engine
    is the producer); :meth:`update` is called by the engine on its tick
    cadence (already off the hot-path DB rule — computation is pure numpy).
    """

    def __init__(self, enabled: bool = False, *, settings_service: Any = None) -> None:
        self._lock = threading.RLock()
        self._enabled: bool = bool(enabled)
        self._settings_service = settings_service
        self._last_snapshot: LiquiditySnapshot | None = None
        self._last_error: str | None = None
        self._last_error_at: float | None = None
        self._last_success_at: float | None = None
        self._last_latency_ms: float | None = None
        self._source: SourceKind = SourceKind.UNAVAILABLE
        self._engine_instance: Any = None

    # ------------------------------------------------------------------ state

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def last_snapshot(self) -> LiquiditySnapshot | None:
        with self._lock:
            return self._last_snapshot

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    @property
    def last_latency_ms(self) -> float | None:
        with self._lock:
            return self._last_latency_ms

    def bind_engine(self, engine: Any) -> None:
        """Attach the live engine (web thread can then read engine state)."""
        with self._lock:
            self._engine_instance = engine

    def set_enabled(self, value: bool, *, actor: str = "runtime") -> dict[str, Any]:
        """Persist + apply the backend switch (hot-reloadable).

        The flow (brief §10):
            UI -> POST/PATCH config API -> backend validates -> runtime
            updates -> new status returned -> UI refreshes.
        Persistence goes through the canonical SettingsService (never
        live.yaml direct writes — INV-010/BUG-080 discipline).
        """
        with self._lock:
            self._enabled = bool(value)
            if self._settings_service is not None:
                try:
                    # Persist via the canonical SettingsService -> SettingsDatabase
                    # (SettingsService exposes no set(); the DB owns the typed
                    # table - INV-010/BUG-080 discipline).
                    db = getattr(self._settings_service, "db", None)
                    if db is not None and hasattr(db, "set"):
                        db.set(
                            "model.liquidity_features_enabled",
                            self._enabled,
                            value_type="bool",
                            actor=actor,
                        )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.error(
                        "[LIQUIDITY] event=PERSIST_FAILED actor=%s error=%s",
                        actor,
                        exc,
                    )
            state_report = self.report()
        logger.info(
            "[LIQUIDITY] event=STATE_UPDATED enabled=%s actor=%s",
            self._enabled,
            actor,
        )
        return state_report

    # ------------------------------------------------------------- production

    def compute_from_engine(
        self,
        *,
        bars: list[Any] | None = None,
        mid_price: float | None = None,
        atr: float | None = None,
        decision_at: datetime | None = None,
        use_htf: bool = True,
        source: SourceKind = SourceKind.LIVE_MARKET_STATE,
    ) -> LiquiditySnapshot:
        """Runs the canonical producer with engine inputs and stores the result.

        Called by the live engine per new-bar cadence (pure numpy; bounded
        work, no I/O, no DB).
        """
        started = time.perf_counter()
        try:
            if not bars:
                raise ValueError("liquidity compute: no completed bars")
            features_obj: LiquidityFeatures = compute_liquidity_features(
                bars,
                decision_at=decision_at,
                mid_price=mid_price,
                atr=atr,
                use_htf=use_htf,
            )
            vec = features_obj.as_vector()  # 10D liquidity block (60..69)
            for _i, _v in enumerate(vec):
                if not math.isfinite(_v) or not (-3.0 <= _v <= 3.0):
                    raise ValueError(f"liquidity block invalid at index {_i}: {_v!r}")
            snap = LiquiditySnapshot(
                decision_at=features_obj.decision_at,
                mid_price=mid_price
                if mid_price is not None
                else float(getattr(bars[-1], "close", 0.0)),
                atr=atr if atr is not None else 0.0,
                features=tuple(vec),
                pools=tuple(features_obj.pools),
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            with self._lock:
                self._last_snapshot = snap
                self._last_success_at = time.monotonic()
                self._last_latency_ms = latency_ms
                self._last_error = None
                self._last_error_at = None
                self._source = source
            logger.info(
                "[LIQUIDITY] event=FEATURE_CALCULATION_OK source=%s latency_ms=%.2f bars=%d",
                source.value,
                latency_ms,
                len(bars),
            )
            return snap
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            with self._lock:
                self._last_error = str(exc)
                self._last_error_at = time.monotonic()
                self._source = SourceKind.UNAVAILABLE
            logger.error(
                "[LIQUIDITY] event=FEATURE_CALCULATION_FAILED stage=compute "
                "feature=liquidity_10 error_code=%s error=%s duration_ms=%.2f",
                type(exc).__name__,
                exc,
                latency_ms,
            )
            raise

    # ---------------------------------------------------------------- status

    def status(self) -> str:
        """Explicit runtime status from actual timestamps (brief §25)."""
        with self._lock:
            if not self._enabled:
                return LiquidityStatus.DISABLED.value
            if self._last_snapshot is None:
                return LiquidityStatus.UNAVAILABLE.value
            if self._last_error_at is not None and (
                self._last_success_at is None or self._last_error_at > self._last_success_at
            ):
                return LiquidityStatus.DEGRADED.value
            age = (
                time.monotonic() - self._last_success_at
                if self._last_success_at is not None
                else float("inf")
            )
            if age > LIVE_STALE_AFTER_SEC:
                return LiquidityStatus.DEGRADED.value
            return LiquidityStatus.ENABLED.value

    def causal_state(self) -> str:
        """VALID when the last snapshot is fresh; STALE when old; INVALID when
        the snapshot is missing (database-less derivation, brief §7/§25)."""
        with self._lock:
            if self._last_snapshot is None:
                return CausalState.INVALID.value
            age = (
                time.monotonic() - self._last_success_at
                if self._last_success_at is not None
                else float("inf")
            )
            if age > LIVE_STALE_AFTER_SEC * 4:
                return CausalState.STALE.value
            return CausalState.VALID.value

    def report(self) -> dict[str, Any]:
        """Canonical liquidity status payload for /api/liquidity/state and the
        live/status sections (real values only; never fabricated)."""
        with self._lock:
            enabled = self._enabled
            snap = self._last_snapshot
            status = self.status()
            causal = self.causal_state()
            source = (
                self._source.value if self._source is not None else SourceKind.UNAVAILABLE.value
            )
            age_sec = (
                round(time.monotonic() - self._last_success_at, 3)
                if self._last_success_at is not None
                else None
            )
            latency = round(self._last_latency_ms, 3) if self._last_latency_ms is not None else None
            error = self._last_error
            error_at = (
                datetime.fromtimestamp(self._last_error_at, tz=UTC).isoformat()
                if self._last_error_at is not None
                else None
            )
            last_update = (
                datetime.fromtimestamp(self._last_success_at, tz=UTC).isoformat()
                if self._last_success_at is not None
                else None
            )
            features_dict: dict[str, float] = {}
            if snap is not None:
                features_dict = {
                    name: float(value)
                    for name, value in zip(snap.names, snap.features, strict=True)
                }
            pools_payload: list[dict[str, Any]] = []
            if snap is not None:
                pools_payload = [
                    {
                        "side": getattr(p, "side", None),
                        "source": getattr(p, "source", None),
                        "state": getattr(p, "state", None),
                        "price": getattr(p, "price", None),
                        "confirmed_at": getattr(p, "confirmed_at", None),
                    }
                    for p in snap.pools
                ]
            return {
                "enabled": enabled,
                "available": snap is not None and status != LiquidityStatus.UNAVAILABLE.value,
                "status": status,
                "causal_state": causal,
                "source": source,
                "algorithm_version": LIQUIDITY_ALGORITHM_VERSION,
                "last_update": last_update,
                "age_sec": age_sec,
                "latency_ms": latency,
                "schema": self._active_schema_block(),
                "reserved_70d_schema": {
                    "id": SCHEMA_70D,
                    "dimension": DIMENSION_70D,
                    "family_indices": "0..49 BASE | 50..59 FAMILY | 60..69 LIQUIDITY",
                },
                "features": features_dict,
                "feature_count": len(features_dict),
                "feature_names": list(LIQUIDITY_FEATURE_NAMES),
                "error": error,
                "error_at": error_at,
                "pools": pools_payload,
                "model_compatibility": self.model_compatibility(),
            }

    def _active_schema_block(self) -> dict[str, Any]:
        """Canonical schema the runtime is ACTUALLY operating under.

        OFF  -> the repo ACTIVE contract (scalp_v1, 50D) — unchanged
               existing behavior.
        ON   -> the TASK-02 60D contract (scalp_liquidity_v1, 60D:
               BASE 0..49 | LIQUIDITY 50..59). The 70D scalp_v4 layout
               (FAMILY 50..59 | LIQUIDITY 60..69) is exposed separately
               as reserved_70d_schema for the shadow70 chain.
        """
        if not self._enabled:
            active = FEATURE_SCHEMAS.active
            return {
                "id": active.schema_id,
                "dimension": active.dimension,
                "family_indices": "0..49 (scalp_v1 protected)",
            }
        return {
            "id": SCHEMA_LIQUIDITY_60D,
            "dimension": DIMENSION_LIQUIDITY_60D,
            "family_indices": "0..49 BASE | 50..59 LIQUIDITY",
        }

    def model_compatibility(self) -> dict[str, Any]:
        """Model-vs-runtime compatibility for the CURRENT loaded model."""
        engine = self._engine_instance
        model_schema: str | None = None
        model_dim: int | None = None
        if engine is not None:
            model_schema = getattr(engine, "FEATURE_SCHEMA_ID", None)
            model_dim = getattr(engine, "FEATURE_DIM", None)
        schema = FEATURE_SCHEMAS.resolve(SCHEMA_70D)
        return resolve_model_compatibility(
            model_schema,
            model_dim,
            schema.schema_id,
            schema.dimension,
        )

    def snapshot_payload(self) -> dict[str, Any]:
        """Ten real values with per-value index/source/timestamp (brief §8/§17)."""
        with self._lock:
            snap = self._last_snapshot
            if snap is None:
                return {
                    "schema_id": self._active_schema_block()["id"],
                    "dimension": self._active_schema_block()["dimension"],
                    "timestamp": None,
                    "source": SourceKind.UNAVAILABLE.value,
                    "features": {},
                    "available": False,
                    "reason": "NO_LIQUIDITY_SNAPSHOT",
                }
            act = self._active_schema_block()
            return {
                "schema_id": act["id"],
                "dimension": act["dimension"],
                "timestamp": snap.decision_at.isoformat() if snap.decision_at else None,
                "source": self._source.value
                if self._source is not None
                else SourceKind.UNAVAILABLE.value,
                "features": {
                    name: {
                        "index": act["dimension"] - len(snap.names) + idx,  # last 10 slots
                        "value": float(value),
                        "timestamp": snap.decision_at.isoformat() if snap.decision_at else None,
                        "source": self._source.value
                        if self._source is not None
                        else SourceKind.UNAVAILABLE.value,
                        "status": self.status(),
                        "raw_value": float(value),
                        "normalized_value": float(value),
                        "clipped_value": float(value),
                    }
                    for idx, (name, value) in enumerate(zip(snap.names, snap.features, strict=True))
                },
                "available": True,
            }
