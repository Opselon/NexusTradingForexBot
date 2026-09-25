"""
Candle Intelligence Domain Contracts
=====================================
Immutable contracts for the local, isolated candle-close analysis and
trade-decision module (BUG-061).

The candle close is a GATE, not a feature: every decision record carries the
full close classification and the reason codes that led to it, so any decision
is explainable and deterministic for identical input state.

All models are frozen Pydantic contracts — never mutate, use model_copy.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CandleCloseClass(StrEnum):
    """Primary classification of how a candle closed."""

    BULLISH_CONTINUATION = "BULLISH_CONTINUATION"
    BEARISH_CONTINUATION = "BEARISH_CONTINUATION"
    BULLISH_REVERSAL = "BULLISH_REVERSAL"
    BEARISH_REVERSAL = "BEARISH_REVERSAL"
    INDECISION = "INDECISION"
    TRAPPED_BREAKOUT = "TRAPPED_BREAKOUT"
    EXHAUSTION = "EXHAUSTION"
    FALSE_BREAKOUT = "FALSE_BREAKOUT"
    WEAK_CLOSE = "WEAK_CLOSE"
    INVALID = "INVALID"


class TradeBias(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    NO_TRADE = "NO_TRADE"


class DecisionType(StrEnum):
    ENTRY = "ENTRY"
    HOLD = "HOLD"
    FAST_EXIT = "FAST_EXIT"
    EXIT = "EXIT"
    NO_TRADE = "NO_TRADE"
    MODIFY_SL_TP = "MODIFY_SL_TP"
    CANCEL_PENDING = "CANCEL_PENDING"


class RiskState(StrEnum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    RISK_ON = "RISK_ON"
    REDUCED = "REDUCED"
    BLOCKED = "BLOCKED"


class CandleCloseSummary(BaseModel):
    """Full close-quality classification for one candle (BUG-061)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    timestamp: datetime

    # Raw geometry (already validated finite, body > 0)
    open: float
    high: float
    low: float
    close: float
    range: float
    body: float
    upper_wick: float
    lower_wick: float

    # Ratios (0..1, NaN-free)
    body_ratio: float = Field(ge=0.0, le=1.0)
    upper_wick_ratio: float = Field(ge=0.0, le=1.0)
    lower_wick_ratio: float = Field(ge=0.0, le=1.0)
    close_position_in_range: float = Field(ge=0.0, le=1.0)  # 0=low, 1=high

    open_to_close_direction: str = "UP"  # UP | DOWN | FLAT
    close_strength: float = Field(ge=0.0, le=1.0)
    rejection_score: float = Field(ge=0.0, le=1.0)
    continuation_score: float = Field(ge=0.0, le=1.0)
    reversal_score: float = Field(ge=0.0, le=1.0)
    indecision_score: float = Field(ge=0.0, le=1.0)
    momentum_decay_score: float = Field(ge=0.0, le=1.0)

    close_class: CandleCloseClass
    close_quality: str = "NEUTRAL"  # STRONG | GOOD | NEUTRAL | WEAK | INVALID

    def model_dump_for_db(self) -> dict[str, Any]:
        """Flat serialization for the candle_closures table."""
        d = self.model_dump()
        d["close_class"] = self.close_class.value
        return d


class PatternDetection(BaseModel):
    """One detected candlestick pattern with a context-weighted score."""

    model_config = ConfigDict(frozen=True)

    pattern_name: str
    direction: str  # BULLISH | BEARISH | NEUTRAL
    raw_score: float = Field(ge=0.0, le=1.0)  # shape fidelity
    context_weight: float = Field(ge=0.0, le=1.0)  # trend/vol/structure multiplier
    confidence_score: float = Field(ge=0.0, le=1.0)  # raw * context
    requires_confirmation: bool = True
    reason_codes: list[str] = Field(default_factory=list)


class RegimeState(BaseModel):
    """Snapshot of the market regime used for pattern/decision weighting."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    timestamp: datetime
    regime: str = "UNKNOWN"  # e.g. RANGING_MEAN_REVERSION / TRENDING_MOMENTUM
    volatility_state: str = "NORMAL"  # NORMAL | HIGH | LOW
    atr: float = Field(default=0.0, ge=0.0)
    spread: float = Field(default=0.0, ge=0.0)


class RiskEvaluation(BaseModel):
    """Risk-layer output for the decision (never sizes orders here)."""

    model_config = ConfigDict(frozen=True)

    risk_state: RiskState = RiskState.SAFE
    risk_allowed: bool = True
    reason_codes: list[str] = Field(default_factory=list)


class CandleDecision(BaseModel):
    """The final decision record for one candle-close event (BUG-061)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    timestamp: datetime

    close_summary: CandleCloseSummary
    detected_patterns: list[PatternDetection] = Field(default_factory=list)
    regime_state: RegimeState
    risk_evaluation: RiskEvaluation = Field(default_factory=RiskEvaluation)

    trade_bias: TradeBias = TradeBias.NEUTRAL
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)

    entry_allowed: bool = False
    hold_allowed: bool = True
    fast_exit_required: bool = False
    exit_required: bool = False
    modify_order: bool = False
    cancel_pending: bool = False

    decision_type: DecisionType = DecisionType.NO_TRADE
    no_trade_reason: str = ""
    reason_codes: list[str] = Field(default_factory=list)

    raw_payload: dict[str, Any] = Field(default_factory=dict)
    computed_payload: dict[str, Any] = Field(default_factory=dict)
