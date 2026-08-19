"""70D Shadow Domain Models (TASK-05-70D-SHADOW).

Canonical, frozen contracts for the 70D shadow observation layer:

* Shadow70CandidateContract — everything the runtime must verify before load
  (model_id, model_version, schema_id=scalp_v4, dimension=70,
  feature_schema_hash, scaler_hash, training_dataset_id, validation_result,
  artifact_hash).
* Shadow70Observation — one idempotent Champion-vs-Shadow comparison.
* Shadow70FeatureProvenance — lineage of the 70D vector (news state,
  liquidity state + calculation version).
* Shadow70VectorReport — the validated live vector summary (finite/range/
  schema/freshness/provenance pass/fail).
* DisagreementClass — the 8-class disagreement taxonomy.

SAFETY: frozen models only; no execution/risk/policy objects (INV-018).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Canonical 70D schema id for this runtime (POST_70D / 70D series contract).
#: TASK-03-70D-PARITY schema_contract.py defines scalp_v3 == 70D; the brief
#: mandates schema_id=scalp_v3 for the 70D candidate. (AGENT-10 temporarily
#: moved the runtime to scalp_v4 for the news-family canonicalization; the
#: canonical id is restored here — reconciliation, TASK-05-70D-SHADOW.)
SHADOW70_SCHEMA_ID: str = "scalp_v3"

#: 50 Base + 10 News + 10 Liquidity (INV-70D-001..003 / INV-70D-004).
SHADOW70_DIMENSION: int = 70

BASE_DIM: int = 50
NEWS_DIM: int = 10
LIQUIDITY_DIM: int = 10

#: Family base indices inside the 70D vector (INV-70D-001..003).
BASE_SLICE: tuple[int, int] = (0, 50)
NEWS_SLICE: tuple[int, int] = (50, 60)
LIQUIDITY_SLICE: tuple[int, int] = (60, 70)

#: Canonical Liquidity feature names at indices 60..69 (70D series contract).
LIQUIDITY_FEATURE_NAMES: tuple[str, ...] = (
    "bsl_distance_atr",
    "ssl_distance_atr",
    "eqh_strength",
    "eql_strength",
    "htf_liquidity_score",
    "internal_liquidity_distance",
    "external_liquidity_distance",
    "liquidity_confluence",
    "liquidity_sweep_state",
    "post_sweep_displacement",
)

#: Output classes — the existing 4-class contract, never reinterpreted.
OUTPUT_CLASSES: tuple[str, ...] = ("NO_TRADE", "BUY_MARKET", "SELL_MARKET", "WAIT")


class Shadow70RuntimeState(StrEnum):
    """Runtime lifecycle (spec 32)."""

    IDLE = "IDLE"  # no candidate attached
    LOADING = "LOADING"
    READY = "READY"  # SHADOW_READY
    DEGRADED = "DEGRADED"  # SHADOW_DEGRADED
    BLOCKED = "BLOCKED"  # SHADOW_BLOCKED (candidate rejected / drift critical)
    FAILED = "FAILED"  # SHADOW_LOAD_FAILED
    STOPPED = "STOPPED"
    PAUSED = "PAUSED"


class Shadow70LoadStatus(StrEnum):
    """Load-gate verdict (spec 4)."""

    SHADOW_READY = "SHADOW_READY"
    SHADOW_BLOCKED = "SHADOW_BLOCKED"
    SHADOW_DEGRADED = "SHADOW_DEGRADED"
    SHADOW_LOAD_FAILED = "SHADOW_LOAD_FAILED"
    NO_VALIDATED_CANDIDATE = "NO_VALIDATED_CANDIDATE"


class DisagreementClass(StrEnum):
    """Comparison outcome taxonomy (spec 9 / 26)."""

    AGREEMENT = "AGREEMENT"
    ACTION_DISAGREEMENT = "ACTION_DISAGREEMENT"
    DIRECTION_DISAGREEMENT = "DIRECTION_DISAGREEMENT"
    CONFIDENCE_DIVERGENCE = "CONFIDENCE_DIVERGENCE"
    NO_TRADE_DISAGREEMENT = "NO_TRADE_DISAGREEMENT"
    # --- spec 26 detailed categories ---
    CHAMPION_BUYS_SHADOW_NO_TRADE = "CHAMPION_BUYS_SHADOW_NO_TRADE"
    CHAMPION_SELLS_SHADOW_NO_TRADE = "CHAMPION_SELLS_SHADOW_NO_TRADE"
    CHAMPION_NO_TRADE_SHADOW_BUYS = "CHAMPION_NO_TRADE_SHADOW_BUYS"
    CHAMPION_NO_TRADE_SHADOW_SELLS = "CHAMPION_NO_TRADE_SHADOW_SELLS"
    BUY_VS_SELL = "BUY_VS_SELL"
    HIGH_CONFIDENCE_DISAGREEMENT = "HIGH_CONFIDENCE_DISAGREEMENT"
    LOW_CONFIDENCE_DISAGREEMENT = "LOW_CONFIDENCE_DISAGREEMENT"

    # aliases keep the taxonomy small for consumers
    @classmethod
    def agreement_classes(cls) -> tuple[str, ...]:
        return (cls.AGREEMENT.value,)


def _utc(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class Shadow70CandidateContract(BaseModel):
    """The verified contract a 70D candidate must satisfy (spec 3)."""

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(...)
    model_version: str = Field(...)
    schema_id: str = Field(default=SHADOW70_SCHEMA_ID)
    dimension: int = Field(default=SHADOW70_DIMENSION, ge=1)
    feature_schema_hash: str = Field(default="", description="hash of the schema/order contract")
    scaler_hash: str = Field(default="")
    training_dataset_id: str = Field(default="")
    validation_result: str = Field(default="")  # e.g. VALIDATED_CANDIDATE / REJECTED ...
    artifact_hash: str = Field(default="")
    artifact_path: str = Field(default="")
    scaler_path: str = Field(default="")
    num_classes: int = Field(default=4, ge=1)

    def is_validated(self) -> bool:
        return self.validation_result.upper() == "VALIDATED_CANDIDATE"

    def is_70d(self) -> bool:
        return self.dimension == SHADOW70_DIMENSION and self.schema_id == SHADOW70_SCHEMA_ID


class Shadow70FeatureProvenance(BaseModel):
    """Lineage of one live 70D vector (spec 7 / 41)."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(default="XAUUSD")
    timestamp: datetime = Field(...)
    feature_schema_id: str = Field(default=SHADOW70_SCHEMA_ID)
    feature_schema_hash: str = Field(default="")
    news_state: str = Field(default="")
    liquidity_state: str = Field(default="")
    liquidity_calculation_version: str = Field(default="")
    news_context_hash: str = Field(default="")
    liquidity_feature_hash: str = Field(default="")
    base_feature_hash: str = Field(default="")
    regime: str = Field(default="UNKNOWN")
    session: str = Field(default="ALL")
    market_snapshot: dict[str, Any] = Field(default_factory=dict)  # price, atr, spread (bounded)
    producer_version: str = Field(default="shadow70_v1")

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _utc(v)


class Shadow70VectorReport(BaseModel):
    """Validation verdict for one 70D vector before inference (spec 6)."""

    model_config = ConfigDict(frozen=True)

    ok: bool = Field(...)
    dimension: int = Field(...)
    finite: bool = Field(...)
    in_range: bool = Field(...)
    schema_ok: bool = Field(...)
    freshness_ok: bool = Field(...)
    provenance_ok: bool = Field(...)
    reasons: list[str] = Field(default_factory=list)


class Shadow70Observation(BaseModel):
    """One idempotent Champion-vs-Shadow observation (spec 12 / 13 / 25).

    ``observation_id`` is deterministic: sha256(snapshot_id | model_id |
    model_version | timestamp) so a reconnect/retry never duplicates.
    Outcome linkage (spec 25) is separate research telemetry — this record
    NEVER feeds accounting or the experience ledger (INV-018).
    """

    model_config = ConfigDict(frozen=True)

    observation_id: str = Field(...)
    snapshot_id: str = Field(...)
    timestamp: datetime = Field(...)
    symbol: str = Field(default="XAUUSD")
    timeframe: str = Field(default="M1")
    simulated: bool = Field(default=True)

    model_id: str = Field(default="")
    model_version: str = Field(default="")
    model_hash: str = Field(default="")
    scaler_hash: str = Field(default="")
    schema_id: str = Field(default=SHADOW70_SCHEMA_ID)
    schema_dimension: int = Field(default=SHADOW70_DIMENSION)

    champion_action: str = Field(default="NO_TRADE")
    champion_probabilities: list[float] = Field(default_factory=list)
    champion_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    shadow_action: str = Field(default="NO_TRADE")
    shadow_probabilities: list[float] = Field(default_factory=list)
    shadow_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_delta: float = Field(default=0.0)

    disagreement: DisagreementClass = Field(default=DisagreementClass.AGREEMENT)
    agreement: bool = Field(default=True)
    valid: bool = Field(default=True)
    reason: str = Field(default="")

    #: bounded evidence: regime/session/news/liquidity state + the 10D
    #: liquidity sub-vector (spec 10 / 27). Full 70D only under debug.
    regime: str = Field(default="UNKNOWN")
    session: str = Field(default="ALL")
    news_state: str = Field(default="")
    liquidity_state: str = Field(default="")
    news_context_hash: str = Field(default="")
    liquidity_feature_hash: str = Field(default="")
    liquidity_features_10: list[float] = Field(default_factory=list)

    feature_hash: str = Field(default="")  # snapshot id (feature provenance)
    sample_source: str = Field(default="LIVE")  # LIVE / REPLAY
    latency_ms: float = Field(default=0.0, ge=0.0)
    error_code: str = Field(default="")

    outcome: str = Field(default="PENDING")  # PENDING / WIN / LOSS / FLAT (research only)
    outcome_resolved_at: datetime | None = Field(default=None)

    @field_validator("timestamp", "outcome_resolved_at")
    @classmethod
    def _utc_opt(cls, v: datetime | None) -> datetime | None:
        return _utc(v) if v is not None else None

    @field_validator("shadow_probabilities", "champion_probabilities")
    @classmethod
    def _finite(cls, v: list[float]) -> list[float]:
        for x in v:
            if not math.isfinite(x):
                raise ValueError("probability vector must be finite")
        return v

    def deterministic_id(self) -> str:
        return self.observation_id


def _direction(action: str) -> str:
    if action in ("BUY_MARKET", "BUY"):
        return "BUY"
    if action in ("SELL_MARKET", "SELL"):
        return "SELL"
    return "NONE"


def _is_trade(action: str) -> bool:
    return action in ("BUY_MARKET", "BUY", "SELL_MARKET", "SELL")


def classify_disagreement(
    champion_action: str,
    shadow_action: str,
    champion_confidence: float | None = None,
    shadow_confidence: float | None = None,
    high_conf_threshold: float = 0.75,
) -> DisagreementClass:
    """Classifies one comparison into the 8-category taxonomy (spec 9 / 26).

    Pure function; never raises. ORDER of precedence:
      same action          -> AGREEMENT
      opposite direction   -> DIRECTION_DISAGREEMENT (BUY vs SELL)
      one trades one not   -> NO_TRADE sub-category / direction-specific
      both trade, same dir -> ACTION_DISAGREEMENT
    Confidence divergence is only used when the actions agree.
    """
    ca = str(champion_action or "NO_TRADE")
    sa = str(shadow_action or "NO_TRADE")
    if ca == sa:
        c_conf = float(champion_confidence or 0.0)
        s_conf = float(shadow_confidence or 0.0)
        if abs(c_conf - s_conf) >= 0.10:
            return DisagreementClass.CONFIDENCE_DIVERGENCE
        return DisagreementClass.AGREEMENT

    cb = _is_trade(ca)
    sb = _is_trade(sa)
    if cb and sb:
        if _direction(ca) != _direction(sa):
            return DisagreementClass.BUY_VS_SELL
        return DisagreementClass.ACTION_DISAGREEMENT
    # one is NO_TRADE / WAIT
    if cb and not sb:
        return (
            DisagreementClass.CHAMPION_BUYS_SHADOW_NO_TRADE
            if ca.startswith("BUY")
            else DisagreementClass.CHAMPION_SELLS_SHADOW_NO_TRADE
        )
    if sb and not cb:
        return (
            DisagreementClass.CHAMPION_NO_TRADE_SHADOW_BUYS
            if sa.startswith("BUY")
            else DisagreementClass.CHAMPION_NO_TRADE_SHADOW_SELLS
        )
    if _direction(ca) != _direction(sa) and "NONE" not in (_direction(ca), _direction(sa)):
        return DisagreementClass.DIRECTION_DISAGREEMENT
    return DisagreementClass.NO_TRADE_DISAGREEMENT
