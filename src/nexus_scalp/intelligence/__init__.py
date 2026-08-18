"""
Trade Intelligence Brain
========================
PHASE 09 adaptive strategy evolution + position lifecycle intelligence.

    models.py      immutable position-lifecycle / autopsy / behavior /
                   evolution domain contracts
    lifecycle.py   PositionLifecycleTracker: immutable position timeline
    autopsy.py     TradeAutopsyEngine: forensic "why did this trade win?" / "...lose?"
    behavior.py    BehaviorDetectionEngine: measurable behavioral patterns
    evolution.py   StrategyEvolutionEngine: controlled candidate discovery
    gate.py        PreTradeIntelligenceGate: WARN/suitability pre-trade decision
    worker.py      IntelligenceWorker: isolated background refresh
    store.py       bounded read facade over the intelligence tables

SAFETY: this package only analyzes, scores, recommends and rejects BEFORE
execution. It holds no adapter, no order manager and no risk engine; it can
never place, modify or close an order.
"""

from nexus_scalp.intelligence.autopsy import TradeAutopsyEngine
from nexus_scalp.intelligence.behavior import BehaviorDetectionEngine
from nexus_scalp.intelligence.evolution import StrategyEvolutionEngine
from nexus_scalp.intelligence.gate import PreTradeIntelligenceGate, SuitabilityTier
from nexus_scalp.intelligence.lifecycle import PositionLifecycleTracker
from nexus_scalp.intelligence.models import (
    AnomalyEvent,
    AutopsyVerdict,
    BehaviorAnalysis,
    BehaviorAnalysisStatus,
    BehaviorDetection,
    BehaviorSeverity,
    DecisionContext,
    EvolutionCandidate,
    EvolutionStatus,
    MarketContext,
    PositionEventType,
    PositionLifecycleEvent,
    PositionPerformance,
    PositionSnapshot,
    TradeAutopsy,
)
from nexus_scalp.intelligence.worker import IntelligenceWorker, format_intelligence_worker_status

__all__ = [
    "AnomalyEvent",
    "AutopsyVerdict",
    "BehaviorAnalysis",
    "BehaviorAnalysisStatus",
    "BehaviorDetection",
    "BehaviorDetectionEngine",
    "BehaviorSeverity",
    "DecisionContext",
    "EvolutionCandidate",
    "EvolutionStatus",
    "IntelligenceWorker",
    "MarketContext",
    "PositionEventType",
    "PositionLifecycleEvent",
    "PositionLifecycleTracker",
    "PositionPerformance",
    "PositionSnapshot",
    "PreTradeIntelligenceGate",
    "StrategyEvolutionEngine",
    "SuitabilityTier",
    "TradeAutopsy",
    "TradeAutopsyEngine",
    "format_intelligence_worker_status",
]
