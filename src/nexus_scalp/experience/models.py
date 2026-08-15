"""
Experience Intelligence Domain Models
=====================================
Defines immutable data models, strategy context identifiers, performance scoring,
lifecycle state machines, and decision objects for Phase 08 Experience Intelligence.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrategyLifecycle(StrEnum):
    """
    Data-driven lifecycle states for strategy hypotheses and context patterns.

    Lifecycle Transitions:
        DISCOVERED  -> First observed pattern with insufficient sample size.
        EVALUATING  -> Accumulating experience samples for statistical significance.
        VALIDATED   -> Reached min sample size & positive risk-adjusted expectancy.
        ACTIVE      -> Eligible to actively qualify/down-rank live trade proposals.
        DEGRADED    -> Showing recent performance decay, drawdown, or adverse variance.
        RETIRED     -> Persistent negative expectancy or catastrophic drawdown. Ineligible.
        QUARANTINED -> Anomalous, corrupted, or unsafe execution patterns. Isolated.
    """

    DISCOVERED = "DISCOVERED"
    EVALUATING = "EVALUATING"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    RETIRED = "RETIRED"
    QUARANTINED = "QUARANTINED"


class ExperienceAction(StrEnum):
    """
    Decision actions output by the Experience Intelligence pre-trade decision gate.
    """

    ALLOW = "ALLOW"
    ALLOW_WITH_CONTEXT = "ALLOW_WITH_CONTEXT"
    ALLOW_WITH_REDUCED_CONFIDENCE = "ALLOW_WITH_REDUCED_CONFIDENCE"
    PENALIZE = "PENALIZE"
    REJECT = "REJECT"


class StrategyContext(BaseModel):
    """
    Sparse, hierarchical context fingerprint representing a strategy hypothesis/pattern.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(..., description="Unique strategy pattern ID or fingerprint hash")
    symbol: str = Field(default="XAUUSD", description="Financial instrument")
    timeframe: str = Field(default="M1", description="Primary timeframe")
    session: str = Field(default="ALL", description="Trading session (e.g. LONDON, NY, ASIAN, ALL)")
    regime: str = Field(default="UNKNOWN", description="Market regime classification")
    volatility_regime: str = Field(
        default="NORMAL", description="Volatility state (LOW, NORMAL, HIGH, EXTREME)"
    )
    trend_state: str = Field(
        default="NEUTRAL", description="HTF trend alignment (BULLISH, BEARISH, NEUTRAL)"
    )
    confluence_fingerprint: str = Field(
        default="", description="Unique string hash of active SMC/technical confluences"
    )
    parameter_hash: str = Field(default="", description="Parameter configuration fingerprint")


class ExperienceRecord(BaseModel):
    """
    Immutable experience snapshot recording trade proposals, execution context, and outcomes.

    Hard Invariants:
        - experience_timestamp < decision_timestamp for causal post-trade outcome updates.
        - Preserves the canonical 50D feature vector snapshot without alteration.
    """

    model_config = ConfigDict(frozen=True)

    experience_id: str = Field(
        ..., description="Deterministic unique identifier (e.g. exp_{ticket/request_id})"
    )
    request_id: str = Field(..., description="Trade proposal tracing request ID")
    execution_id: str = Field(default="", description="Broker ticket or order ID")
    decision_id: str = Field(default="", description="Pre-trade decision record ID")
    idempotency_key: str = Field(
        ..., description="Deterministic key preventing duplicate experience recording"
    )

    symbol: str = Field(..., description="Symbol")
    timeframe: str = Field(default="M1", description="Timeframe")
    decision_timestamp: datetime = Field(
        ..., description="UTC timestamp when pre-trade decision was made"
    )
    outcome_timestamp: datetime | None = Field(
        default=None, description="UTC timestamp when trade outcome became known"
    )

    # Strategy & Context
    strategy_id: str = Field(..., description="Associated strategy/pattern ID")
    strategy_version: str = Field(default="1.0.0", description="Strategy logic version")
    context: StrategyContext = Field(..., description="Hierarchical context snapshot")

    # Feature Provenance (Canonical 50D preserved)
    feature_vector_50d: list[float] = Field(
        ..., min_length=50, max_length=50, description="Exact canonical 50D feature vector snapshot"
    )
    feature_hash: str = Field(default="", description="SHA256 fingerprint of 50D feature vector")

    # Signal & Proposal Snapshot
    action: str = Field(..., description="Proposed trade action")
    entry_reason: str = Field(..., description="Primary signal entry reason")
    model_probability: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Raw model probability"
    )
    signal_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Signal confidence before experience gate"
    )

    # Risk & Proposed Execution Specs
    proposed_entry: float = Field(..., gt=0.0, description="Proposed entry price")
    stop_loss: float = Field(..., gt=0.0, description="Proposed stop loss price")
    take_profit: float = Field(..., gt=0.0, description="Proposed take profit price")
    risk_reward_ratio: float = Field(default=1.0, description="Planned risk to reward ratio")
    approved_volume: float = Field(default=0.0, ge=0.0, description="Approved lot size")

    # Post-Trade Outcome (Populated when outcome becomes known)
    is_executed: bool = Field(
        default=False, description="True if proposal was executed into open position"
    )
    is_closed: bool = Field(default=False, description="True if position reached final exit")
    exit_reason: str = Field(default="", description="Exit mechanism or rejection reason")
    realized_pnl_usd: float = Field(default=0.0, description="Net realized PnL in USD")
    realized_r_multiple: float = Field(default=0.0, description="Realized R-multiple profit/loss")
    mae_points: float = Field(default=0.0, description="Maximum Adverse Excursion in points")
    mfe_points: float = Field(default=0.0, description="Maximum Favorable Excursion in points")
    mae_usd: float = Field(default=0.0, description="Maximum Adverse Excursion in USD")
    mfe_usd: float = Field(default=0.0, description="Maximum Favorable Excursion in USD")
    holding_duration_seconds: float = Field(
        default=0.0, ge=0.0, description="Trade duration in seconds"
    )

    # System Provenance Metadata
    model_version: str = Field(default="1.0.0", description="Model bundle version")
    config_version: str = Field(default="1.0.0", description="Algo configuration version")

    @field_validator("decision_timestamp", "outcome_timestamp")
    @classmethod
    def validate_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)


class StrategyScore(BaseModel):
    """
    Multi-dimensional performance score and confidence evaluation for a strategy/context pattern.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(..., description="Strategy identifier")
    sample_count: int = Field(default=0, ge=0, description="Total historical experiences evaluated")
    win_count: int = Field(default=0, ge=0, description="Winning experiences count")
    loss_count: int = Field(default=0, ge=0, description="Losing experiences count")
    breakeven_count: int = Field(default=0, ge=0, description="Breakeven experiences count")

    win_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Historical win rate ratio")
    expectancy_usd: float = Field(default=0.0, description="Average net profit per trade in USD")
    expectancy_r: float = Field(default=0.0, description="Average profit per trade in R-multiples")
    profit_factor: float = Field(
        default=1.0, ge=0.0, description="Gross profit over gross loss ratio"
    )

    median_r: float = Field(default=0.0, description="Median R-multiple outcome")
    max_drawdown_r: float = Field(default=0.0, ge=0.0, description="Peak-to-trough drawdown in R")
    downside_tail_risk_r: float = Field(default=0.0, description="5th percentile worst R outcome")
    avg_mae_r: float = Field(default=0.0, description="Average MAE in R")
    avg_mfe_r: float = Field(default=0.0, description="Average MFE in R")
    r_variance: float = Field(default=0.0, ge=0.0, description="Variance of R outcomes")

    recency_weighted_expectancy_r: float = Field(
        default=0.0, description="Recency-weighted expected R value"
    )
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Evidence-based statistical confidence (0.0 to 1.0)",
    )

    lifecycle_state: StrategyLifecycle = Field(
        default=StrategyLifecycle.DISCOVERED, description="Active lifecycle state"
    )
    last_updated: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Last evaluation UTC timestamp"
    )


class PreTradeExperienceDecision(BaseModel):
    """
    Pre-trade decision object output by the Experience Intelligence boundary.
    """

    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(..., description="Unique decision ID")
    request_id: str = Field(..., description="Associated trade proposal request ID")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="UTC timestamp of decision"
    )

    action: ExperienceAction = Field(..., description="Pre-trade experience decision action")
    qualifies_trade: bool = Field(
        ..., description="True if proposal is accepted (ALLOW/ALLOW_WITH_*)"
    )
    adjusted_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence after experience adjustment"
    )

    strategy_id: str = Field(..., description="Matched strategy context identifier")
    strategy_lifecycle: StrategyLifecycle = Field(
        ..., description="Lifecycle status of matched strategy"
    )
    strategy_score: StrategyScore | None = Field(
        default=None, description="Detailed strategy score snapshot"
    )

    retrieved_sample_count: int = Field(
        default=0, ge=0, description="Number of historical experiences retrieved"
    )
    similarity_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Context match similarity score"
    )
    penalty_reason: str = Field(default="", description="Explanatory text if penalized or rejected")
