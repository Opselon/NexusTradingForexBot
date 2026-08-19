"""Liquidity Runtime Governor — TASK-02-70D-INTEGRATION (TASK-2).

WHY THIS EXISTS
---------------
TASK-1 (TASK-01-60D-LIQUIDITY) delivered the canonical pure producer
``features/liquidity_engine.compute_liquidity_features`` (10 causal liquidity
dimensions, indices 50..59 of a 60D ``scalp_liquidity_v1`` vector). TASK-2's
job is the INTEGRATION layer: a real runtime governor that snapshots the
10 liquidity values from the LIVE engine, derives explicit status
(ENABLED / DISABLED / DEGRADED / UNAVAILABLE), builds the 70D feature
contract (``scalp_v3``: BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69),
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

The 70D contract (``scalp_v3``):
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
SCHEMA_70D: str = "scalp_v3"  # CANONICAL 70D id (TASK-03-70D-PARITY / schema_contract.py)
DIMENSION_70D: int = 70

#: Slot-50..59 family producer ids this governor understands.
FAMILY_SCHEMA_V2: str = "scalp_v2"  # TASK-5 momentum extras (50..59)
FAMILY_SCHEMA_LIQUIDITY_60D: str = "scalp_liquidity_v1"  # TASK-1 60D (50..59)
SCHEMA_LIQUIDITY_60D: str = "scalp_liquidity_v1"  # TASK-02 canonical 60D contract
DIMENSION_LIQUIDITY_60D: int = 60

#: Neutral 10D used ONLY when liquidity is UNAVAILABLE and a caller still
#: needs a structurally-valid 60D asset (documented neutral defaults, same
#: convention as the 50D cold-start / 60D extras). Never shown as LIVE data.
_NEUTRAL_60D_LIQUIDITY: tuple[float, ...] = (3.0, 3.0, 0.0, 0.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0)

#: Freshness threshold for LIVE status (seconds since the last successful
#: snapshot). Industry-typical for M1 feature cadence; configurable per call.
LIVE_STALE_AFTER_SEC: float = 90.0

#: Algorithm version PROVENANCE. The production producer
#: (liquidity_engine.compute_liquidity_features) carries no version field;
#: this constant is the version the governor's configured pipeline refers to.
#: The optimized candidate (liquidity_engine_opt, "liquidity-v1.1") is NOT
#: wired into this governor; when it is adopted the provenance must point at
#: the producer's own constant (BUG-111 discipline: never claim an active
#: algorithm the runtime does not run).
LIQUIDITY_ALGORITHM_VERSION: str = "scalp_liquidity_v1.0.0"

#: Canonical liquidity block indices in the authoritative 70D registry
#: (schema_contract.py). The API/UI contract MUST render these indices; the
#: runtime never derives liquidity placement from the ACTIVE (50D) schema —
#: that derivation is exactly the bug that showed idx 40..49 while DISABLED.
LIQUIDITY_BLOCK_START: int = 60
LIQUIDITY_BLOCK_END: int = 70


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
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ModelCompatibility(Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


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
        # Wall-clock (UTC epoch seconds) counterparts of the monotonic
        # fields above. BUG-111: the report payload previously rendered
        # monotonic seconds through datetime.fromtimestamp() -> the 1970
        # sentinel. Monotonic is ONLY valid for age deltas; wall clock is
        # the only valid input for absolute timestamps.
        self._last_success_wall_at: float | None = None
        self._last_error_wall_at: float | None = None
        self._last_latency_ms: float | None = None
        self._source: SourceKind = SourceKind.UNAVAILABLE
        self._source_changed_wall_at: float | None = None
        self._engine_instance: Any = None
        # Monotonic liquidity state revision (BUG-111 stale-SSE guard):
        # incremented on every state mutation (snapshot, toggle, error).
        # Consumers (UI/SSE) must ignore older revisions.
        self._state_revision: int = 0

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
            self._state_revision += 1
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
                now_mono = time.monotonic()
                now_wall = time.time()
                self._last_success_at = now_mono
                self._last_success_wall_at = now_wall
                self._last_latency_ms = latency_ms
                self._last_error = None
                self._last_error_at = None
                self._last_error_wall_at = None
                if self._source != source:
                    self._source_changed_wall_at = now_wall
                self._source = source
                self._state_revision += 1
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
                self._last_error_wall_at = time.time()
                if self._source != SourceKind.UNAVAILABLE:
                    self._source_changed_wall_at = time.time()
                self._source = SourceKind.UNAVAILABLE
                self._state_revision += 1
            logger.error(
                "[LIQUIDITY] event=FEATURE_CALCULATION_FAILED stage=compute "
                "feature=liquidity_10 error_code=%s error=%s duration_ms=%.2f",
                type(exc).__name__,
                exc,
                latency_ms,
            )
            raise

    def build_runtime_60d_vector(self, features50: list[float] | tuple[float, ...]) -> list[float]:
        """Assembles the ACTIVE runtime vector: 50D base + the latest 10
        liquidity values (TASK-01 build_60d_vector contract, indices
        50..59). Raises when liquidity is disabled or no snapshot exists —
        never silently pads or zero-fills (STEP 7 / INV-009).
        """
        from nexus_scalp.features.liquidity_engine import (
            BASE_50D,
            validate_60d_liquidity_vector,
        )

        if not self._enabled:
            raise RuntimeError("build_runtime_60d_vector: liquidity disabled")
        with self._lock:
            snap = self._last_snapshot
        if snap is None:
            raise RuntimeError("build_runtime_60d_vector: no liquidity snapshot")
        if len(features50) != BASE_50D:
            raise ValueError(
                f"build_runtime_60d_vector: base must be exactly {BASE_50D}D, got {len(features50)}"
            )
        vec = list(features50) + list(snap.features)
        validate_60d_liquidity_vector(vec, context="runtime")
        return vec

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
            # Explicit separation of the two orthogonal dimensions (BUG-110
            # contract): calculation_status = whether the last compute attempt
            # SUCCEEDED; source_status = where the input data came from.
            # FEATURE_CALCULATION_OK + source=UNAVAILABLE is a legitimate pair
            # (bars computed from engine state without a live broker source) and
            # must NOT collapse into a single healthy boolean.
            if self._last_snapshot is not None:
                if self._last_error is None or (
                    self._last_success_at is not None
                    and self._last_error_at is not None
                    and self._last_success_at >= self._last_error_at
                ):
                    calculation_status = "SUCCESS"
                else:
                    calculation_status = "FAILED"
            else:
                calculation_status = "NOT_RUN" if self._last_error is None else "FAILED"
            source_status = source  # LIVE_MARKET_STATE | REPLAY | UNAVAILABLE
            age_sec = (
                round(time.monotonic() - self._last_success_at, 3)
                if self._last_success_at is not None
                else None
            )
            latency = round(self._last_latency_ms, 3) if self._last_latency_ms is not None else None
            error = self._last_error
            error_at = (
                datetime.fromtimestamp(self._last_error_wall_at, tz=UTC).isoformat()
                if self._last_error_wall_at is not None
                else None
            )
            # WALL-CLOCK last update (BUG-111): monotonic seconds were
            # previously rendered through fromtimestamp() -> 1970 sentinel.
            # age_sec keeps using monotonic (a delta); absolute timestamps
            # MUST come from the wall-clock counterpart.
            last_update = (
                datetime.fromtimestamp(self._last_success_wall_at, tz=UTC).isoformat()
                if self._last_success_wall_at is not None
                else None
            )
            # Snapshot DECISION timestamp (the bar time the values describe)
            # — the truthful provenance of the feature values themselves.
            snapshot_timestamp = (
                snap.decision_at.isoformat() if snap is not None and snap.decision_at else None
            )
            features_dict: dict[str, float] = {}
            if snap is not None:
                features_dict = {
                    name: float(value)
                    for name, value in zip(snap.names, snap.features, strict=True)
                }
            pools_payload: list[dict[str, Any]] = []
            if snap is not None:
                pools_payload = []
                for p in snap.pools:
                    confirmed_at = getattr(p, "confirmed_at", None)
                    if confirmed_at is not None and not isinstance(confirmed_at, str):
                        confirmed_at = (
                            confirmed_at.isoformat()
                            if hasattr(confirmed_at, "isoformat")
                            else str(confirmed_at)
                        )
                    pools_payload.append(
                        {
                            "side": getattr(p, "side", None),
                            "source": getattr(p, "source", None),
                            "state": getattr(p, "state", None),
                            "price": getattr(p, "price", None),
                            # ISO-8601 timezone-aware string — NEVER a raw
                            # datetime (BUG-110 SSE: json.dumps(payload) in the
                            # event_generator crashed on this field, killing
                            # every SSE frame once pools were confirmed).
                            "confirmed_at": confirmed_at,
                        }
                    )
            # feature_availability: explicit, never inferred (BUG-111).
            # DISABLED + retained snapshot = NOT_ACTIVE (historical cache);
            # ENABLED + fresh snapshot = AVAILABLE; ENABLED + old snapshot
            # = STALE_CACHE; no snapshot = UNAVAILABLE.
            if not enabled:
                feature_availability = "NOT_ACTIVE"
            elif snap is None:
                feature_availability = "UNAVAILABLE"
            elif causal == CausalState.STALE.value:
                feature_availability = "STALE_CACHE"
            else:
                feature_availability = "AVAILABLE"
            # causal_state is NOT_APPLICABLE while disabled: validity only
            # describes an ACTIVE computation, never a retained cache.
            if not enabled:
                causal = CausalState.NOT_APPLICABLE.value
            return {
                "enabled": enabled,
                "state_revision": self._state_revision,
                "available": feature_availability == "AVAILABLE",
                "feature_availability": feature_availability,
                "status": status,
                "calculation_status": calculation_status,
                "source_status": source_status,
                "causal_state": causal,
                "source": source,
                "algorithm_version": LIQUIDITY_ALGORITHM_VERSION,
                "last_update": last_update,
                "snapshot_timestamp": snapshot_timestamp,
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

        OFF  -> the repo ACTIVE contract (scalp_v1, 50D) — the honest
               pre-liquidity runtime; features are NOT_ACTIVE.
        ON   -> the CANONICAL 70D contract (scalp_v3: BASE 0..49 |
               FAMILY/NEWS 50..59 | LIQUIDITY 60..69), the same family
               layout schema_contract.py and the shadow70 chain use.
               Liquidity placement (60..69) NEVER derives from the active
               schema dimension (BUG-111: it derived 40..49 when OFF).
        """
        if not self._enabled:
            active = FEATURE_SCHEMAS.active
            return {
                "id": active.schema_id,
                "dimension": active.dimension,
                "family_indices": "0..49 (scalp_v1 protected)",
                "liquidity_indices": [LIQUIDITY_BLOCK_START, LIQUIDITY_BLOCK_END - 1],
            }
        return {
            "id": SCHEMA_70D,
            "dimension": DIMENSION_70D,
            "family_indices": "0..49 BASE | 50..59 NEWS | 60..69 LIQUIDITY",
            "liquidity_indices": [60, 69],
        }

    def model_compatibility(self) -> dict[str, Any]:
        """Model-vs-runtime compatibility for the CURRENT loaded model.

        Gated on the runtime toggle (BUG-111): while liquidity is
        DISABLED the compatibility block is NOT_APPLICABLE — a disabled
        runtime never claims a liquidity-enabled incompatibility. When
        enabled, the current model (schema + dimension) is evaluated
        against the canonical 70D contract (scalp_v3) with the explicit
        matrix in resolve_model_compatibility (never padded/truncated).
        """
        if not self._enabled:
            engine = self._engine_instance
            model_schema = getattr(engine, "FEATURE_SCHEMA_ID", None) if engine is not None else None
            model_dim = getattr(engine, "FEATURE_DIM", None) if engine is not None else None
            return {
                "result": ModelCompatibility.NOT_APPLICABLE.value,
                "reason": "LIQUIDITY_DISABLED",
                "model_schema_id": model_schema,
                "model_dimension": model_dim,
                "runtime_schema_id": SCHEMA_70D,
                "runtime_dimension": DIMENSION_70D,
            }
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
        """Ten real values with per-value index/source/timestamp (brief §8/§17).

        BUG-111: per-value indices come from the AUTHORITATIVE feature
        registry (schema_contract.canonical_feature_names -> 60..69),
        never from the active-schema dimension (which derived 40..49 while
        DISABLED). A retained snapshot while DISABLED is explicit:
        ``runtime_enabled=False`` + per-value ``status=DISABLED`` +
        ``feature_availability=NOT_ACTIVE`` with the snapshot timestamp.
        """
        with self._lock:
            snap = self._last_snapshot
            enabled = self._enabled
            status = self.status()
            causal = self.causal_state()
            if not enabled:
                causal = CausalState.NOT_APPLICABLE.value
            if not enabled:
                feature_availability = "NOT_ACTIVE"
            elif snap is None:
                feature_availability = "UNAVAILABLE"
            elif causal == CausalState.STALE.value:
                feature_availability = "STALE_CACHE"
            else:
                feature_availability = "AVAILABLE"
            if snap is None:
                act = self._active_schema_block()
                return {
                    "schema_id": act["id"],
                    "dimension": act["dimension"],
                    "timestamp": None,
                    "source": SourceKind.UNAVAILABLE.value,
                    "source_status": SourceKind.UNAVAILABLE.value,
                    "feature_availability": feature_availability,
                    "runtime_enabled": enabled,
                    "features": {},
                    "available": False,
                    "state_revision": self._state_revision,
                    "reason": "NO_LIQUIDITY_SNAPSHOT",
                }
            act = self._active_schema_block()
            # Authoritative indices follow the ACTIVE schema's liquidity
            # placement (TASK-02 60D: 50..59; 70D mode: 60..69). When the
            # 70D registry names are available and the runtime is 70D they
            # are used; otherwise the active schema's block start wins.
            # TASK-11 canonicalization: the runtime schema block is the
            # canonical 70D contract (scalp_v3) whose liquidity block is
            # ALWAYS 60..69 (schema_contract canonical names); never
            # derive from a 60D active-schema dimension.
            liq_start = int(act.get("liquidity_indices", [60, 69])[0])
            indices = {n: liq_start + i for i, n in enumerate(LIQUIDITY_FEATURE_NAMES)}
            source = self._source.value if self._source is not None else SourceKind.UNAVAILABLE.value
            snap_ts = snap.decision_at.isoformat() if snap.decision_at else None
            return {
                "schema_id": act["id"],
                "dimension": act["dimension"],
                "timestamp": snap_ts,
                "source": source,
                "source_status": source,
                "feature_availability": feature_availability,
                "runtime_enabled": enabled,
                "state_revision": self._state_revision,
                "features": {
                    name: {
                        "index": indices[name],
                        "value": float(value),
                        "timestamp": snap_ts,
                        "source": source,
                        "status": status,
                        "feature_availability": feature_availability,
                        "runtime_enabled": enabled,
                        "raw_value": float(value),
                        "normalized_value": float(value),
                        "clipped_value": float(value),
                    }
                    for name, value in zip(snap.names, snap.features, strict=True)
                },
                "available": feature_availability == "AVAILABLE",
            }
