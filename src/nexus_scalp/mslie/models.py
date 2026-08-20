"""MSLIE domain contracts — Market Structure & Liquidity Intelligence Engine.

The engine is a MARKET PERCEPTION layer (the "visual cortex" of the AI
trading system). It transforms raw OHLC/volume/spread into structured,
machine-readable market intelligence consumed by AI models (neural nets,
transformers, temporal fusion, RL, classifiers). It is NOT a signal
generator, NOT a trading bot, and NEVER holds order authority (INV-002).

CONTRACT DISCIPLINE
-------------------
- Every model here is a frozen dataclass (immutable snapshots, same rule as
  the domain layer: use ``dataclasses.replace`` to derive variants).
- NO FUTURE LEAKAGE (INV-008): every value is computed only from bars fully
  closed at or before the decision timestamp. The engine never reads a bar
  with ``timestamp > decision_at``.
- Honest missing values: unavailable -> None (never fabricated 0.0).
- ``MarketIntelligenceFeatureVectorV1`` is the versioned, stable feature
  contract that models consume. It is advisory (observability first) and
  NEVER alters the live 50D/70D feature contract (INV-009).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Any

# =============================================================================
# ENUMERATIONS (stable, additive)
# =============================================================================


class SwingType(IntEnum):
    """Direction of a detected swing point."""

    HIGH = 1
    LOW = -1


class BrokenStatus(IntEnum):
    """Whether a swing level has been broken (structure change)."""

    INTACT = 0
    BROKEN = 1
    BROKEN_AND_RETESTED = 2


class MarketBias(IntEnum):
    """Session-level directional bias derived from structure."""

    BEARISH = -1
    NEUTRAL = 0
    BULLISH = 1


class SweepState(IntEnum):
    """Post-sweep market state (REVERSAL / CONTINUATION / UNCERTAIN)."""

    UNCERTAIN = 0
    REVERSAL = 1
    CONTINUATION = 2


class LiquidityRank(IntEnum):
    """Rank of a liquidity zone as a probability target."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    EXTREME = 4


class ZoneSide(IntEnum):
    """Side of a liquidity zone (which resting orders it represents)."""

    BUY_SIDE = 1
    SELL_SIDE = -1


# =============================================================================
# SWING STRUCTURE
# =============================================================================


@dataclass(frozen=True)
class SwingPoint:
    """One detected swing point with institutional-quality scoring.

    The engine deliberately avoids "simple fixed fractals": a random local
    high is NOT equal to a major institutional high. Every swing carries:

    - ``strength_score``  (0..100): how decisive the rejection was
      (ATR-normalized reaction, body/wick balance, volume participation).
    - ``importance_score`` (0..100): how much the market has reacted to this
      level historically (reaction count, retest proximity, timeframe).
    """

    id: str
    symbol: str
    timeframe: str
    price: float
    timestamp: datetime
    type: SwingType
    strength_score: float
    importance_score: float
    liquidity_created: bool
    liquidity_taken: bool
    reaction_count: int
    broken_status: BrokenStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "price": self.price,
            "timestamp": self.timestamp.isoformat(),
            "type": self.type.name,
            "strength_score": self.strength_score,
            "importance_score": self.importance_score,
            "liquidity_created": self.liquidity_created,
            "liquidity_taken": self.liquidity_taken,
            "reaction_count": self.reaction_count,
            "broken_status": self.broken_status.name,
        }


# =============================================================================
# LIQUIDITY MAP
# =============================================================================


@dataclass(frozen=True)
class LiquidityZone:
    """One detected liquidity zone (BSL / SSL side) with target ranking."""

    price: float
    side: ZoneSide
    strength_score: float
    timeframe: str
    age_bars: int
    number_of_tests: int
    distance_from_price: float
    probability_as_target: float
    rank: LiquidityRank
    sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "side": self.side.name,
            "strength_score": self.strength_score,
            "timeframe": self.timeframe,
            "age_bars": self.age_bars,
            "number_of_tests": self.number_of_tests,
            "distance_from_price": self.distance_from_price,
            "probability_as_target": self.probability_as_target,
            "rank": self.rank.name,
            "sources": list(self.sources),
        }


# =============================================================================
# STOP HUNT / SWEEP EVENTS
# =============================================================================


@dataclass(frozen=True)
class LiquiditySweepEvent:
    """A stop-hunt / liquidity-sweep detection (never a mere wick)."""

    direction: str  # BUY_SIDE | SELL_SIDE (which pool was violated)
    liquidity_type: str  # pool source taxonomy, e.g. EQH / SWING_HIGH / PDH
    price: float
    confidence: float  # 0..100
    sweep_strength: float  # ATR-normalized penetration depth (clipped)
    after_event_state: SweepState
    pool_price: float
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "liquidity_type": self.liquidity_type,
            "price": self.price,
            "confidence": self.confidence,
            "sweep_strength": self.sweep_strength,
            "after_event_state": self.after_event_state.name,
            "pool_price": self.pool_price,
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# MARKET REGIME
# =============================================================================


@dataclass(frozen=True)
class MarketRegimeFeatures:
    """Regime features of the MarketIntelligenceFeatureVectorV1 contract."""

    trend_direction: float  # -1 (bear) .. +1 (bull)
    trend_strength: float  # 0..100
    volatility_state: float  # 0..1 (1 = high volatility)
    ranging_probability: float  # 0..1
    expansion_probability: float  # 0..1
    compression_probability: float  # 0..1
    regime_label: str = "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "trend_direction": self.trend_direction,
            "trend_strength": self.trend_strength,
            "volatility_state": self.volatility_state,
            "ranging_probability": self.ranging_probability,
            "expansion_probability": self.expansion_probability,
            "compression_probability": self.compression_probability,
            "regime_label": self.regime_label,
        }


# =============================================================================
# BREAKOUT QUALITY
# =============================================================================


@dataclass(frozen=True)
class BreakoutQuality:
    """REAL BREAKOUT vs LIQUIDITY TRAP discrimination."""

    real_breakout_probability: float  # 0..1
    fake_breakout_probability: float  # 0..1
    closing_strength: float  # 0..1
    volume_support: float  # 0..1
    momentum_support: float  # 0..1
    retest_confirmation: float  # 0..1
    structure_confirmation: float  # 0..1

    def to_dict(self) -> dict[str, Any]:
        return {
            "real_breakout_probability": self.real_breakout_probability,
            "fake_breakout_probability": self.fake_breakout_probability,
            "closing_strength": self.closing_strength,
            "volume_support": self.volume_support,
            "momentum_support": self.momentum_support,
            "retest_confirmation": self.retest_confirmation,
            "structure_confirmation": self.structure_confirmation,
        }


# =============================================================================
# SMART MONEY FEATURES (numerical ML features)
# =============================================================================


@dataclass(frozen=True)
class SmartMoneyFeatures:
    """Numerical encoding of order blocks, FVGs, displacement, inducement and
    premium/discount zones. Every value is finite and bounded."""

    order_block_type: float  # -1 bearish OB .. +1 bullish OB
    order_block_strength: float  # 0..1
    fvg_count: float  # number of open FVGs (capped)
    fvg_strength: float  # ATR-normalized, clipped [-3, 3]
    displacement_strength: float  # ATR-normalized impulse, clipped [-3, 3]
    inducement_levels: float  # number of inducement levels in play
    premium_discount_position: float  # -1 (deep discount) .. +1 (deep premium)
    last_mitigated_order_block: float  # ATR distance to last mitigated OB

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_block_type": self.order_block_type,
            "order_block_strength": self.order_block_strength,
            "fvg_count": self.fvg_count,
            "fvg_strength": self.fvg_strength,
            "displacement_strength": self.displacement_strength,
            "inducement_levels": self.inducement_levels,
            "premium_discount_position": self.premium_discount_position,
            "last_mitigated_order_block": self.last_mitigated_order_block,
        }


# =============================================================================
# MARKET MEMORY (multi-month vision)
# =============================================================================


@dataclass(frozen=True)
class MarketMemoryLevel:
    """One remembered institutional level (multi-month vision)."""

    level: float
    created: str  # ISO date the level was first registered
    timeframe: str
    events: tuple[str, ...] = ()
    touch_count: int = 0
    last_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "created": self.created,
            "timeframe": self.timeframe,
            "events": list(self.events),
            "touch_count": self.touch_count,
            "last_price": self.last_price,
        }


# =============================================================================
# MARKET CONTEXT (the engine's live perception)
# =============================================================================


@dataclass(frozen=True)
class MarketContext:
    """The engine's current perception of the market."""

    symbol: str
    timeframe: str
    regime: MarketRegimeFeatures
    bias: MarketBias
    structure: str  # BULLISH | BEARISH | RANGING
    confidence: float  # 0..100
    decision_at: datetime
    mid_price: float
    atr: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime.to_dict(),
            "bias": self.bias.name,
            "structure": self.structure,
            "confidence": self.confidence,
            "decision_at": self.decision_at.isoformat(),
            "mid_price": self.mid_price,
            "atr": self.atr,
        }


# =============================================================================
# THE VERSIONED FEATURE VECTOR CONTRACT
# =============================================================================


@dataclass(frozen=True)
class MarketIntelligenceFeatureVectorV1:
    """Stable versioned contract consumed by AI models.

    This is a MARKET PERCEPTION vector — it describes WHERE important
    highs/lows exist, WHERE liquidity is located, WHETHER stops were likely
    hunted, WHETHER structure changed, WHAT market regime exists and HOW
    STRONG each observation is. The final decision always remains with the
    strategy models / ScalpNet / execution / risk engines.

    All sub-features are causal (INV-008): computed only from bars closed at
    or before ``decision_at``. Missing observations are None — never 0.0.
    """

    version: str
    symbol: str
    timeframe: str
    decision_at: datetime
    mid_price: float
    regime: MarketRegimeFeatures
    structure: str
    bias: MarketBias
    structure_confidence: float
    nearest_buy_side_liquidity: LiquidityZone | None
    nearest_sell_side_liquidity: LiquidityZone | None
    liquidity_map: tuple[LiquidityZone, ...] = ()
    last_sweep_event: LiquiditySweepEvent | None = None
    breakout_quality: BreakoutQuality | None = None
    smart_money: SmartMoneyFeatures | None = None
    memory: tuple[MarketMemoryLevel, ...] = ()
    swing_count_high: int = 0
    swing_count_low: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "decision_at": self.decision_at.isoformat(),
            "mid_price": self.mid_price,
            "regime": self.regime.to_dict(),
            "structure": self.structure,
            "bias": self.bias.name,
            "structure_confidence": self.structure_confidence,
            "nearest_buy_side_liquidity": (
                self.nearest_buy_side_liquidity.to_dict()
                if self.nearest_buy_side_liquidity
                else None
            ),
            "nearest_sell_side_liquidity": (
                self.nearest_sell_side_liquidity.to_dict()
                if self.nearest_sell_side_liquidity
                else None
            ),
            "liquidity_map": [z.to_dict() for z in self.liquidity_map],
            "last_sweep_event": self.last_sweep_event.to_dict() if self.last_sweep_event else None,
            "breakout_quality": self.breakout_quality.to_dict() if self.breakout_quality else None,
            "smart_money": self.smart_money.to_dict() if self.smart_money else None,
            "memory": [m.to_dict() for m in self.memory],
            "swing_count_high": self.swing_count_high,
            "swing_count_low": self.swing_count_low,
        }
