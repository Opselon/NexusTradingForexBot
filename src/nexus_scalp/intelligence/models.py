"""
Trade Intelligence Brain Domain Models
======================================
PHASE 09 immutable contracts for position lifecycles, trade autopsies,
measurable behavior detection and strategy evolution.

These models are the DERIVED intelligence layer. They are layered on top of the
authoritative Phase 08 experience ledger and never replace it:

    experience *is* the source of truth (immutable decisions/outcomes)
    intelligence *is* the rebuildable interpretation (lifecycle, autopsy,
    behavior, evolution candidates)

SAFETY CONTRACT
---------------
Nothing defined here can place, modify or close an order. The intelligence
package only analyzes, scores, recommends and rejects *before* execution through
the existing bounded gate. It holds no adapter, no order manager and no risk
engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PositionEventType(StrEnum):
    """Immutable position-timeline lifecycle observations."""

    POSITION_CREATED = "POSITION_CREATED"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_MOVING = "POSITION_MOVING"
    POSITION_EXPECTATION_CONFIRMED = "POSITION_EXPECTATION_CONFIRMED"
    POSITION_MFE_REACHED = "POSITION_MFE_REACHED"
    POSITION_PROFIT_GIVEBACK = "POSITION_PROFIT_GIVEBACK"
    POSITION_DEGRADING = "POSITION_DEGRADING"
    POSITION_RECOVERY_ATTEMPT = "POSITION_RECOVERY_ATTEMPT"
    POSITION_EXITED = "POSITION_EXITED"
    POSITION_MODIFIED = "POSITION_MODIFIED"


class AutopsyVerdict(StrEnum):
    """High level 'why did this trade win/lose' summary."""

    CLEAN_WIN = "CLEAN_WIN"
    LUCKY_WIN = "LUCKY_WIN"
    MANAGED_LOSS = "MANAGED_LOSS"
    COSTLY_LOSS = "COSTLY_LOSS"
    UNKNOWN = "UNKNOWN"
    EVEN = "EVEN"


class EvolutionStatus(StrEnum):
    """Lifecycle of a discovered strategy candidate."""

    DISCOVERED = "DISCOVERED"
    BACKTESTING = "BACKTESTING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    PROMOTED = "PROMOTED"


class BehaviorSeverity(StrEnum):
    """How materially a detected behavior degrades decision quality."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class MarketContext(BaseModel):
    """Bounded market snapshot snapshot-captured with every lifecycle event."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(default="")
    timeframe: str = Field(default="M1")
    session: str = Field(default="ALL")
    market_regime: str = Field(default="UNKNOWN")
    volatility_state: str = Field(default="NORMAL")
    atr: float = Field(default=0.0, ge=0.0)
    spread: float = Field(default=0.0, ge=0.0)


class PositionSnapshot(BaseModel):
    """Full position state captured at an event instant."""

    model_config = ConfigDict(frozen=True)

    entry_price: float = Field(default=0.0, ge=0.0)
    current_price: float = Field(default=0.0, ge=0.0)
    volume: float = Field(default=0.0, ge=0.0)
    stop_loss: float = Field(default=0.0, ge=0.0)
    take_profit: float = Field(default=0.0, ge=0.0)
    floating_pnl: float = Field(default=0.0)
    realized_pnl: float = Field(default=0.0)


class PositionPerformance(BaseModel):
    """Excursion / timing performance observed to this point."""

    model_config = ConfigDict(frozen=True)

    mfe: float = Field(default=0.0)
    mae: float = Field(default=0.0)
    max_profit_reached: float = Field(default=0.0)
    max_loss_reached: float = Field(default=0.0)
    profit_giveback_pct: float = Field(default=0.0, ge=0.0)
    holding_duration_sec: float = Field(default=0.0, ge=0.0)


class DecisionContext(BaseModel):
    """The decision identity that produced this position."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(default="")
    strategy_version: str = Field(default="")
    feature_schema_id: str = Field(default="")
    model_version: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    probability: float = Field(default=0.0, ge=0.0, le=1.0)


class PositionLifecycleEvent(BaseModel):
    """
    One immutable, self-describing observation in a position's timeline.

    Carries identity, market context, position state, performance and the
    decision context that created the trade - so a replay of the timeline fully
    reconstructs WHY a position behaved the way it did.
    """

    model_config = ConfigDict(frozen=True)

    event_key: str = Field(..., description="Deterministic dedup key")
    ticket: str = Field(...)
    trade_id: str = Field(default="")
    experience_id: str = Field(default="")
    symbol: str = Field(default="")
    timeframe: str = Field(default="M1")
    event_type: PositionEventType = Field(...)
    sequence: int = Field(default=0, ge=0)
    event_timestamp: datetime = Field(...)

    market_context: MarketContext = Field(default_factory=MarketContext)
    position: PositionSnapshot = Field(default_factory=PositionSnapshot)
    performance: PositionPerformance = Field(default_factory=PositionPerformance)
    decision: DecisionContext = Field(default_factory=DecisionContext)
    detail: str = Field(default="")

    @field_validator("event_timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class TradeAutopsy(BaseModel):
    """
    Forensic narrative for one closed trade.

    The outcome decomposition already separates strategy/entry/management/exit/
    execution quality (Phase 08 `OutcomeAnalyzer`). This object packages those
    dimensions into an explanatory *narrative* so an operator (or a future
    evolution engine) can read WHY the trade won or lost at a glance.
    """

    model_config = ConfigDict(frozen=True)

    ticket: str = Field(...)
    trade_id: str = Field(default="")
    experience_id: str = Field(default="")
    strategy_id: str = Field(default="")
    strategy_version: str = Field(default="")
    symbol: str = Field(default="")
    timeframe: str = Field(default="M1")

    entry_price: float = Field(default=0.0, ge=0.0)
    exit_price: float = Field(default=0.0, ge=0.0)
    volume: float = Field(default=0.0, ge=0.0)
    direction: str = Field(default="")
    entry_reason: str = Field(default="")
    realized_pnl_usd: float = Field(default=0.0)
    realized_r: float = Field(default=0.0)

    mfe_r: float = Field(default=0.0)
    mae_r: float = Field(default=0.0)
    giveback_pct: float = Field(default=0.0, ge=0.0)
    holding_duration_sec: float = Field(default=0.0, ge=0.0)
    exit_mechanism: str = Field(default="")

    strategy_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    entry_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    management_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    exit_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    execution_quality: float = Field(default=0.0, ge=-1.0, le=1.0)

    verdict: AutopsyVerdict = Field(default=AutopsyVerdict.UNKNOWN)
    behavioral_flags: list[str] = Field(default_factory=list)
    narrative: str = Field(default="")
    autopsied_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("autopsied_at")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class BehaviorDetection(BaseModel):
    """
    One measurable behavioral pattern evidenced by recorded position data.

    NEVER an emotional/psychological attribution (no "the trader was greedy"),
    ONLY an objective, rule-derived label provable from the recorded numbers.
    """

    model_config = ConfigDict(frozen=True)

    behavior_id: str = Field(...)
    ticket: str = Field(...)
    experience_id: str = Field(default="")
    pattern: str = Field(...)
    severity: BehaviorSeverity = Field(...)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: dict[str, Any] = Field(default_factory=dict)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("detected_at")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class EvolutionCandidate(BaseModel):
    """
    A strategy variation DISCOVERED from historical patterns.

    A candidate NEVER affects live trading until it has been backtested and
    validated (status == VALIDATED, then operator-promoted). It is produced by
    the evolution engine purely as an hypothesis with supporting evidence.
    """

    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(...)
    source_strategy_id: str = Field(...)
    symbol: str = Field(...)
    timeframe: str = Field(default="M1")
    hypothesis: str = Field(...)
    parameter_delta: dict[str, Any] = Field(default_factory=dict)
    pattern_evidence: dict[str, Any] = Field(default_factory=dict)
    status: EvolutionStatus = Field(default=EvolutionStatus.DISCOVERED)
    backtest_expectancy_r: float = Field(default=0.0)
    backtest_sample_count: int = Field(default=0, ge=0)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("discovered_at")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
