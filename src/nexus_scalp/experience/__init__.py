"""
Experience Intelligence Subsystem
=================================
Phase 08 Experience-Driven Strategy Intelligence.

Module map:

    models.py       immutable memory contracts (records, outcomes, scores,
                    schema-versioned feature snapshots, provenance)
    quality.py      deterministic outcome decomposition + behavioral flags
    ledger.py       append-only persistence, dedup, bounded causal retrieval
    evaluator.py    statistical scoring, confidence calibration, lifecycle,
                    replay validation, self-healing rebuild
    retriever.py    bounded context fingerprinting and top-K retrieval
    provenance.py   model registry (metadata only - never weights)
    intelligence.py pre-trade decision boundary + post-trade recorder

The subsystem may DOWN-RANK or REJECT proposals. It never executes orders and
never bypasses RiskEngine or OrderManager.
"""

from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    MAX_STRATEGY_CONFIDENCE,
    BehavioralFlag,
    ExperienceAction,
    ExperienceCorrection,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    ModelProvenance,
    OutcomeDecomposition,
    PositionBehavior,
    PreTradeExperienceDecision,
    StrategyContext,
    StrategyLifecycle,
    StrategyScore,
)
from nexus_scalp.experience.provenance import ModelRegistry
from nexus_scalp.experience.quality import OutcomeAnalyzer, compute_behavior_metrics
from nexus_scalp.experience.retriever import ExperienceRetriever

__all__ = [
    "CANONICAL_FEATURE_DIMENSION",
    "CANONICAL_FEATURE_SCHEMA_ID",
    "MAX_STRATEGY_CONFIDENCE",
    "BehavioralFlag",
    "ExperienceAction",
    "ExperienceCorrection",
    "ExperienceIntelligenceEngine",
    "ExperienceLedger",
    "ExperienceOutcome",
    "ExperienceRecord",
    "ExperienceRetriever",
    "FeatureSnapshot",
    "ModelProvenance",
    "ModelRegistry",
    "OutcomeAnalyzer",
    "OutcomeDecomposition",
    "PositionBehavior",
    "PreTradeExperienceDecision",
    "StrategyContext",
    "StrategyEvaluator",
    "StrategyLifecycle",
    "StrategyScore",
    "compute_behavior_metrics",
]
