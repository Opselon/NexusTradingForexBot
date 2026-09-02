"""Market Structure & Liquidity Intelligence Engine (MSLIE).

A Market Perception Engine — the "visual cortex" of the AI trading system.
It transforms raw OHLC/volume/spread into structured, machine-readable
market intelligence (regime, swing structure, liquidity map, sweep events,
breakout quality, smart-money features) consumed by AI models.

NOT a trading bot, NOT a signal generator, NOT an execution system: the
final decision always remains with strategy models / ScalpNet / execution /
risk engines.
"""

from nexus_scalp.mslie.breakout import BreakoutQuality, assess_breakout_quality
from nexus_scalp.mslie.engine import (
    ALGORITHM_VERSION,
    FEATURE_VECTOR_VERSION,
    IMarketStructureEngine,
    MarketMemory,
    MarketStructureEngine,
)
from nexus_scalp.mslie.liquidity_map import build_liquidity_map
from nexus_scalp.mslie.models import (
    BrokenStatus,
    LiquidityRank,
    LiquiditySweepEvent,
    LiquidityZone,
    MarketBias,
    MarketContext,
    MarketIntelligenceFeatureVectorV1,
    MarketMemoryLevel,
    MarketRegimeFeatures,
    SmartMoneyFeatures,
    SweepState,
    SwingPoint,
    SwingType,
    ZoneSide,
)
from nexus_scalp.mslie.regime import compute_regime_features
from nexus_scalp.mslie.smart_money import compute_smart_money_features
from nexus_scalp.mslie.sweep import detect_sweep_events
from nexus_scalp.mslie.swing import detect_swings

__all__ = [
    "ALGORITHM_VERSION",
    "FEATURE_VECTOR_VERSION",
    "BreakoutQuality",
    "BrokenStatus",
    "IMarketStructureEngine",
    "LiquidityRank",
    "LiquiditySweepEvent",
    "LiquidityZone",
    "MarketBias",
    "MarketContext",
    "MarketIntelligenceFeatureVectorV1",
    "MarketMemory",
    "MarketMemoryLevel",
    "MarketRegimeFeatures",
    "MarketStructureEngine",
    "SmartMoneyFeatures",
    "SweepState",
    "SwingPoint",
    "SwingType",
    "ZoneSide",
    "assess_breakout_quality",
    "build_liquidity_map",
    "compute_regime_features",
    "compute_smart_money_features",
    "detect_sweep_events",
    "detect_swings",
]
