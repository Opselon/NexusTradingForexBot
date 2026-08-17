"""
Candle Intelligence Subsystem (BUG-061)
=======================================
LOCAL, ISOLATED, database-backed candlestick analysis and trade-decision
module for the Nexus Scalp Engine.

    config.py      CandleIntelligenceConfig (conservative safety thresholds)
    models.py      immutable domain contracts (close summary, patterns, decision)
    classifier.py  CandleCloseClassifier: the close-quality GATE
    patterns.py    PatternEngine: 29 candlestick/chart patterns + context weights
    decision.py    CandleDecisionEngine: rule hierarchy entry/hold/exit/no-trade
    store.py       isolated SQLite store (12 tables, audit columns)
    store_writes.py record methods for the store
    engine.py      CandleIntelligenceEngine: orchestrator + spec §11 contract

SAFETY: this package only analyzes, scores and recommends. It holds no adapter,
no order manager and no risk engine; it can never place, modify or close an
order. All persistence is local (artifacts/candle_intel.db); no network calls,
no cloud services, no remote telemetry.

The candle close is a GATE. Weak, contradictory or invalid closes downgrade
confidence, block entry, or accelerate exit — before any pattern logic runs.
"""

from __future__ import annotations

from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.candle_intelligence.engine import (
    CandleIntelligenceEngine,
    CandleOutput,
)
from nexus_scalp.candle_intelligence.models import (
    CandleCloseClass,
    CandleCloseSummary,
    CandleDecision,
    DecisionType,
    PatternDetection,
    RegimeState,
    RiskEvaluation,
    RiskState,
    TradeBias,
)

__all__ = [
    "CandleCloseClass",
    "CandleCloseSummary",
    "CandleDecision",
    "CandleIntelligenceConfig",
    "CandleIntelligenceEngine",
    "CandleOutput",
    "DecisionType",
    "PatternDetection",
    "RegimeState",
    "RiskEvaluation",
    "RiskState",
    "TradeBias",
]
