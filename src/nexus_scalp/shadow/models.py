"""
Shadow Trading Domain Models
============================
PHASE 11 production-safe parallel model evaluation (spec 2 / 5 / 21 / 22 / 23).

The Challenger is SHADOW-ONLY: it has ZERO direct execution authority. It can
never submit an MT5 order, modify an order, reserve margin or alter Champion
state. Every shadow artifact is explicitly marked SHADOW / SIMULATED and can
never be confused with real account PnL.

Model layout:

    ShadowDecisionRecord  one parallel decision (Champion vs Challenger)
    ShadowRun              a bounded evaluation run over live market state
    ShadowComparison       multi-dimension Champion vs Challenger comparison
    PromotionEvaluation    explainable promotion score + veto conditions
    ShadowEvidenceStatus   INSUFFICIENT_EVIDENCE / EVALUATING / PROMOTION_ELIGIBLE / REJECTED
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexus_scalp.experience.models import CANONICAL_FEATURE_DIMENSION, CANONICAL_FEATURE_SCHEMA_ID


class ShadowDecisionKind(StrEnum):
    """How a shadow decision was produced."""

    LIVE_PARALLEL = "LIVE_PARALLEL"  # evaluated on the live tick path (same input as Champion)
    REPLAY = "REPLAY"  # historical replay


class ShadowEvidenceStatus(StrEnum):
    """Sample-sufficiency status (spec 9)."""

    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    EVALUATING = "EVALUATING"
    PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"
    REJECTED = "REJECTED"


class ShadowModelRef(BaseModel):
    """Identity of one model participating in a shadow evaluation."""

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(...)
    model_version: str = Field(...)
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)
    artifact_hash: str = Field(default="")
    is_champion: bool = Field(default=False)


class SharedInputRef(BaseModel):
    """
    Proof of same-input integrity (spec 3 / 4).

    For every shadow comparison the system records the identity of the shared
    market state so mismatched inputs are marked INVALID_COMPARISON and excluded
    from promotion statistics.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(...)
    symbol: str = Field(...)
    timeframe: str = Field(default="M1")
    feature_hash: str = Field(default="")
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)
    regime: str = Field(default="UNKNOWN")
    session: str = Field(default="ALL")
    configuration_version: str = Field(default="")

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    def matches(self, other: SharedInputRef) -> bool:
        """Input equality under the current feature schema (spec 4)."""
        return (
            self.timestamp == other.timestamp
            and self.symbol == other.symbol
            and self.timeframe == other.timeframe
            and self.feature_hash == other.feature_hash
            and self.feature_schema_id == other.feature_schema_id
            and self.feature_dimension == other.feature_dimension
        )


class ShadowDecisionRecord(BaseModel):
    """
    One parallel Champion/Challenger decision (spec 5).

    IDENTITY / MODEL / FEATURE / MARKET / DECISION / RISK / OUTCOME are all
    preserved. PnL here is ALWAYS hypothetical and explicitly flagged
    `simulated=True`; it must never be presented as real account PnL.
    """

    model_config = ConfigDict(frozen=True)

    shadow_decision_id: str = Field(...)
    run_id: str = Field(...)
    decision_id: str = Field(default="")  # the production decision id, if any
    timestamp: datetime = Field(...)
    symbol: str = Field(...)
    timeframe: str = Field(default="M1")

    champion: ShadowModelRef = Field(...)
    challenger: ShadowModelRef = Field(...)
    shared_input: SharedInputRef = Field(...)

    champion_action: str = Field(default="NO_TRADE")
    champion_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    champion_probabilities: list[float] = Field(default_factory=list)
    champion_strategy_id: str = Field(default="")

    challenger_action: str = Field(default="NO_TRADE")
    challenger_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    challenger_probabilities: list[float] = Field(default_factory=list)
    challenger_strategy_id: str = Field(default="")

    action_agreement: bool = Field(default=False)
    valid_comparison: bool = Field(default=True)
    invalid_reason: str = Field(default="")

    # Hypothetical risk (shadow only)
    hypothetical_risk_pct: float = Field(default=0.0, ge=0.0)
    hypothetical_volume: float = Field(default=0.0, ge=0.0)
    hypothetical_sl: float = Field(default=0.0, ge=0.0)
    hypothetical_tp: float = Field(default=0.0, ge=0.0)

    # Hypothetical outcome (shadow only)
    hypothetical_entry: float = Field(default=0.0, ge=0.0)
    hypothetical_exit: float = Field(default=0.0, ge=0.0)
    hypothetical_pnl_usd: float = Field(default=0.0)
    hypothetical_r: float = Field(default=0.0)
    mfe_r: float = Field(default=0.0)
    mae_r: float = Field(default=0.0)
    holding_duration_sec: float = Field(default=0.0, ge=0.0)
    exit_reason: str = Field(default="")

    simulated: bool = Field(default=True, description="ALWAYS True; shadow/simulated")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("timestamp", "created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class ShadowRun(BaseModel):
    """A bounded shadow evaluation run (spec 20 / 24)."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(...)
    champion: ShadowModelRef = Field(...)
    challenger: ShadowModelRef = Field(...)
    status: str = Field(default="RUNNING")  # RUNNING | COMPLETED | FAILED | CANCELLED
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = Field(default=None)
    decision_count: int = Field(default=0, ge=0)
    error: str = Field(default="")

    @field_validator("started_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class ShadowComparison(BaseModel):
    """
    Multi-dimension Champion vs Challenger comparison over shadow decisions
    (spec 7 / 8 / 11 / 13 / 14 / 15).
    """

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(...)
    champion: ShadowModelRef = Field(...)
    challenger: ShadowModelRef = Field(...)

    sample_count: int = Field(default=0, ge=0)
    valid_comparisons: int = Field(default=0, ge=0)
    invalid_comparisons: int = Field(default=0, ge=0)
    action_agreement_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    champion_expectancy_r: float = Field(default=0.0)
    challenger_expectancy_r: float = Field(default=0.0)
    champion_drawdown_r: float = Field(default=0.0, ge=0.0)
    challenger_drawdown_r: float = Field(default=0.0, ge=0.0)
    champion_profit_factor: float = Field(default=0.0)
    challenger_profit_factor: float = Field(default=0.0)
    champion_tail_losses: int = Field(default=0, ge=0)
    challenger_tail_losses: int = Field(default=0, ge=0)

    champion_mfe_r: float = Field(default=0.0)
    challenger_mfe_r: float = Field(default=0.0)
    champion_mae_r: float = Field(default=0.0)
    challenger_mae_r: float = Field(default=0.0)
    champion_holding_sec: float = Field(default=0.0)
    challenger_holding_sec: float = Field(default=0.0)

    champion_calibration: float = Field(default=0.0, ge=0.0, le=1.0)
    challenger_calibration: float = Field(default=0.0, ge=0.0, le=1.0)
    champion_avg_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    challenger_avg_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    by_regime: dict[str, dict[str, float]] = Field(default_factory=dict)
    by_strategy: dict[str, dict[str, float]] = Field(default_factory=dict)
    by_session: dict[str, dict[str, float]] = Field(default_factory=dict)

    best_regimes: list[str] = Field(default_factory=list)
    worst_regimes: list[str] = Field(default_factory=list)
    degraded_regimes: list[str] = Field(default_factory=list)
    degraded_strategies: list[str] = Field(default_factory=list)
    improved_strategies: list[str] = Field(default_factory=list)

    evidence_status: ShadowEvidenceStatus = Field(
        default=ShadowEvidenceStatus.INSUFFICIENT_EVIDENCE
    )
    samples_required: int = Field(default=30, ge=1)
    samples_observed: int = Field(default=0, ge=0)
    evaluation_started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evaluation_duration_hours: float = Field(default=0.0, ge=0.0)

    @field_validator("evaluation_started_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @property
    def expectancy_delta(self) -> float:
        return self.challenger_expectancy_r - self.champion_expectancy_r

    @property
    def drawdown_delta(self) -> float:
        return self.challenger_drawdown_r - self.champion_drawdown_r


class PromotionEvaluation(BaseModel):
    """
    Explainable multi-dimensional promotion evaluation (spec 22 / 23).

    A single critical VETO overrides the aggregate score. Components:
    performance_delta, risk_delta, drawdown_delta, oos_delta, robustness_delta,
    calibration_delta, stability_delta, strategy_regression_penalty,
    sample_confidence.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(...)
    candidate_model_id: str = Field(...)
    candidate_version: str = Field(...)
    champion_model_id: str = Field(...)
    champion_version: str = Field(...)

    performance_delta: float = Field(default=0.0)
    risk_delta: float = Field(default=0.0)
    drawdown_delta: float = Field(default=0.0)
    oos_delta: float = Field(default=0.0)
    robustness_delta: float = Field(default=0.0)
    calibration_delta: float = Field(default=0.0)
    stability_delta: float = Field(default=0.0)
    strategy_regression_penalty: float = Field(default=0.0, ge=0.0)
    sample_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    final_score: float = Field(default=0.0, ge=0.0, le=1.0)
    eligible: bool = Field(default=False)
    vetoes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("evaluated_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
