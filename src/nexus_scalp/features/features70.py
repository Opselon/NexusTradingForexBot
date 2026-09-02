"""Canonical 70D Feature Snapshot & Assembly (TASK-03-70D-PARITY).

ONE MARKET SNAPSHOT -> ONE CANONICAL 70D VECTOR, same semantics everywhere.

This module is the single assembly path used by:
  - dataset builder   (model_generation/schema_v2.py::compute_70d_frame)
  - replay            (model_generation/replay.py)
  - inference         (model_generation/runtime.py / live engine hook)

It composes the three canonical producers:
  base 0..49       ScalpFeatureEngine.compute_from_bars (scalp_v1 protected)
  news 50..59      news_context_v1 fields 0..8 + news_state (canonical order)
  liquidity 60..69 liquidity_engine.compute_liquidity_features (as_vector)

The snapshot is IMMUTABLE after construction. No downstream subsystem can
reorder or reinterpret dimensions: validate() re-checks dimension, hash,
finiteness and bounds; any violation raises with an explicit reason.

PROVENANCE (carried by every snapshot):
  schema_id, schema_version, symbol, timeframe, timestamp_utc,
  feature_vector[70], feature_names, source_state, news_status,
  liquidity_status, calculation_version
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from nexus_scalp.features.schema_contract import (
    DIMENSION,
    NEWS_10D_NAMES,
    SCHEMA_ID,
    SCHEMA_VERSION,
    canonical_feature_names,
    feature_schema_hash,
    validate_70d_vector,
)


class FeatureSourceState(StrEnum):
    """Availability state of one feature family (never silently guessed)."""

    FEATURE_AVAILABLE = "FEATURE_AVAILABLE"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    FEATURE_UNAVAILABLE = "FEATURE_UNAVAILABLE"
    FEATURE_INVALID = "FEATURE_INVALID"


#: Calculation version of this assembly module (bump on semantic change).
CALCULATION_VERSION: str = "70d-v1.0.0"

#: Sentinels for absent families (per documented contract, never random).
NEWS_NEUTRAL_10D: tuple[float, ...] = (0.0,) * 10
LIQUIDITY_NEUTRAL_10D: tuple[float, ...] = (3.0, 3.0, 0.0, 0.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Feature70Snapshot:
    """Immutable canonical 70D feature snapshot with full provenance."""

    schema_id: str = SCHEMA_ID
    schema_version: str = SCHEMA_VERSION
    symbol: str = ""
    timeframe: str = ""
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    feature_vector: tuple[float, ...] = field(default_factory=tuple)
    feature_names: tuple[str, ...] = field(default_factory=canonical_feature_names)
    source_state: FeatureSourceState = FeatureSourceState.FEATURE_AVAILABLE
    news_status: FeatureSourceState = FeatureSourceState.FEATURE_AVAILABLE
    liquidity_status: FeatureSourceState = FeatureSourceState.FEATURE_AVAILABLE
    news_available: bool = True
    liquidity_available: bool = True
    news_context_hash: str = ""
    calculation_version: str = CALCULATION_VERSION
    model_id: str = ""
    model_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_vector", tuple(self.feature_vector))
        object.__setattr__(self, "feature_names", tuple(self.feature_names))
        if self.timestamp_utc.tzinfo is None:
            object.__setattr__(self, "timestamp_utc", self.timestamp_utc.replace(tzinfo=UTC))
        # Dimension is enforced at construction (INV-009: no silent pad/truncate).
        if len(self.feature_vector) != DIMENSION:
            raise ValueError(
                f"Feature70Snapshot: vector must be exactly {DIMENSION}D, got "
                f"{len(self.feature_vector)} (schema={self.schema_id})"
            )
        if len(self.feature_names) != DIMENSION:
            raise ValueError(
                f"Feature70Snapshot: names must be exactly {DIMENSION}, got "
                f"{len(self.feature_names)}"
            )

    # -- derived ------------------------------------------------------------
    @property
    def vector(self) -> list[float]:
        return list(self.feature_vector)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "feature_vector": list(self.feature_vector),
            "feature_names": list(self.feature_names),
            "source_state": self.source_state.value,
            "news_status": self.news_status.value,
            "liquidity_status": self.liquidity_status.value,
            "news_available": self.news_available,
            "liquidity_available": self.liquidity_available,
            "news_context_hash": self.news_context_hash,
            "calculation_version": self.calculation_version,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "feature_schema_hash": feature_schema_hash(self.schema_id),
        }

    def validate(
        self,
        *,
        expect_finite: bool = True,
        expect_bounds: bool = True,
        context: str = "",
    ) -> list[float]:
        """Validates the snapshot vector; returns a fresh list.

        Raises ValueError with an explicit reason when the vector violates
        the canonical 70D contract (dimension/order/finite/bounds/hash).
        """
        vec = validate_70d_vector(
            self.feature_vector, schema_hash=feature_schema_hash(self.schema_id), context=context
        )
        return vec

    def schema_hash(self) -> str:
        return feature_schema_hash(self.schema_id)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def assemble_70d(
    *,
    base50: list[float] | tuple[float, ...],
    news10: list[float] | tuple[float, ...] | None = None,
    liquidity10: list[float] | tuple[float, ...] | None = None,
    symbol: str = "",
    timeframe: str = "",
    timestamp_utc: datetime | None = None,
    news_available: bool = True,
    liquidity_available: bool = True,
    news_status: FeatureSourceState = FeatureSourceState.FEATURE_AVAILABLE,
    liquidity_status: FeatureSourceState = FeatureSourceState.FEATURE_AVAILABLE,
    news_context_hash: str = "",
    model_id: str = "",
    model_version: str = "",
) -> Feature70Snapshot:
    """Assembles one canonical 70D snapshot from the three family producers.

    NEVER pads or truncates: a missing family with no explicit neutral marker
    raises. When a family is unavailable the caller MUST pass the documented
    neutral block (NEWS_NEUTRAL_10D / LIQUIDITY_NEUTRAL_10D) AND the matching
    status so downstream sees the truth (FEATURE_UNAVAILABLE), not fake data.
    """
    if len(base50) != 50:
        raise ValueError(f"assemble_70d: base must be exactly 50D, got {len(base50)}")
    b = list(base50)

    if news10 is None:
        raise ValueError(
            "assemble_70d: news10 required — pass the documented neutral block "
            "(NEWS_NEUTRAL_10D) with news_status=FEATURE_UNAVAILABLE explicitly; "
            "never fabricate values silently"
        )
    if len(news10) != 10:
        raise ValueError(f"assemble_70d: news block must be exactly 10D, got {len(news10)}")
    n = list(news10)

    if liquidity10 is None:
        raise ValueError(
            "assemble_70d: liquidity10 required — pass the documented neutral block "
            "(LIQUIDITY_NEUTRAL_10D) with liquidity_status=FEATURE_UNAVAILABLE "
            "explicitly; never fabricate values silently"
        )
    if len(liquidity10) != 10:
        raise ValueError(
            f"assemble_70d: liquidity block must be exactly 10D, got {len(liquidity10)}"
        )
    l = list(liquidity10)

    if not news_available and news_status == FeatureSourceState.FEATURE_AVAILABLE:
        news_status = FeatureSourceState.FEATURE_UNAVAILABLE
    if not liquidity_available and liquidity_status == FeatureSourceState.FEATURE_AVAILABLE:
        liquidity_status = FeatureSourceState.FEATURE_UNAVAILABLE

    vector = tuple(b + n + l)
    return Feature70Snapshot(
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        symbol=symbol,
        timeframe=timeframe,
        timestamp_utc=timestamp_utc or datetime.now(UTC),
        feature_vector=vector,
        feature_names=canonical_feature_names(),
        source_state=FeatureSourceState.FEATURE_AVAILABLE,
        news_status=news_status,
        liquidity_status=liquidity_status,
        news_available=news_available,
        liquidity_available=liquidity_available,
        news_context_hash=news_context_hash,
        calculation_version=CALCULATION_VERSION,
        model_id=model_id,
        model_version=model_version,
    )


def news_10d_from_context(context: dict[str, float] | None) -> list[float]:
    """Extracts the canonical news 10D block from a news_context_v1 dict.

    Uses the canonical field order (NEWS_10D_NAMES); absent fields -> 0.0,
    non-finite -> 0.0 (same discipline as news_bridge._num).
    """
    out: list[float] = []
    for f in NEWS_10D_NAMES:
        try:
            v = float((context or {}).get(f, 0.0))
        except (TypeError, ValueError):
            v = 0.0
        if math.isnan(v) or math.isinf(v):
            v = 0.0
        out.append(v)
    return out


def news_context_hash_10(context: dict[str, float] | None) -> str:
    """Deterministic content hash of the news 10D block (for provenance)."""
    import hashlib

    if context is None:
        return hashlib.sha256(b"no_news_context").hexdigest()[:16]
    payload = "|".join(f"{name}={context.get(name, 0.0)}" for name in NEWS_10D_NAMES)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def clamp_neutral_family(values: list[float], neutral: tuple[float, ...]) -> list[float]:
    """Clips one family block to [-3,+3] and replaces non-finite with neutral.

    Used ONLY at dataset build time for families whose producer already
    clipped (liquidity engine clips centrally). Defensive; never called with
    family semantics that would hide a producer bug (validation still runs).
    """
    out: list[float] = []
    for i, v in enumerate(values):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            f = neutral[i] if i < len(neutral) else 0.0
        out.append(max(-3.0, min(3.0, f)))
    return out
