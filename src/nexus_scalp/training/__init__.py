"""
Training & Online Adaptation Module
===================================
Provides Walk-Forward validation and Zero-Leakage online fine-tuning engines,
plus the deterministic training primitives (ML-TRAIN-001) and the centralized
optimizer / LR-scheduler factory (ML-TRAIN-003).
"""

from nexus_scalp.training.engine import (
    AMPContext,
    DeterministicTrainingConfig,
    run_deterministic_training,
    set_deterministic_seed,
from nexus_scalp.training.optimizers import (
    DEFAULT_OPTIMIZER_CONFIG,
    OPTIMIZER_NAMES,
    SCHEDULER_NAMES,
    Lookahead,
    OptimizerConfigError,
    WarmupScheduler,
    build_optimizer_and_scheduler,
    current_lrs,
    normalize_config,
    step_scheduler,
)
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

__all__ = [
    "AMPContext",
    "DeterministicTrainingConfig",
    "WalkForwardTrainer",
    "run_deterministic_training",
    "set_deterministic_seed",
    "DEFAULT_OPTIMIZER_CONFIG",
    "OPTIMIZER_NAMES",
    "SCHEDULER_NAMES",
    "Lookahead",
    "OptimizerConfigError",
    "WalkForwardTrainer",
    "WarmupScheduler",
    "build_optimizer_and_scheduler",
    "current_lrs",
    "normalize_config",
    "step_scheduler",
]
