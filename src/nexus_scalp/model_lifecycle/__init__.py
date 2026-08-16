"""
Model Lifecycle & Challenger Engine
===================================
PHASE 10 controlled model training + Champion/Challenger management.

This package implements:

    VERIFIED EXPERIENCE -> TRAINING DATASET -> CANDIDATE MODEL
        -> VALIDATION GATES -> CHALLENGER (shadow-eligible)

Production inference stays with the Champion; a validated Challenger NEVER
replaces it automatically. The package holds no adapter, no order manager and
no risk engine - it cannot place, modify or close an order.

Modules:
    models.py       immutable contracts (TrainingRun, TrainingDataset, gates)
    dataset.py      deterministic, causally-safe training dataset builder
    integrity.py    artifact hash/dimension/class-count compatibility
    champion.py     Champion loading + verification (production model only)
    trainer.py      ChallengerTrainer: offline candidate training (staging paths)
    gates.py        12 validation gates + collapse protection
    comparison.py   Champion vs Challenger multi-dimension comparison
    registry.py     additive lifecycle status over experience_model_registry
    store.py        TrainingRun + comparison immutable persistence
    orchestrator.py end-to-end controlled training pipeline
    worker.py       isolated, bounded, cancellable background training worker
"""

from nexus_scalp.model_lifecycle.models import (
    ChampionChallengerComparison,
    GateResult,
    ModelArtifactInfo,
    ModelStatus,
    TrainingDataset,
    TrainingDatasetRow,
    TrainingRun,
    TrainingRunStatus,
)

__all__ = [
    "ChampionChallengerComparison",
    "GateResult",
    "ModelArtifactInfo",
    "ModelStatus",
    "TrainingDataset",
    "TrainingDatasetRow",
    "TrainingRun",
    "TrainingRunStatus",
]
