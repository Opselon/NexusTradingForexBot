"""Model Generation Migration (PHASE 13).

ARTIFACT-FIRST MODEL FACTORY.

Legacy ScalpNet is classified as LEGACY BASELINE (control group), NOT
deleted — it stays loadable for benchmarking/rollback. The new center is the
model artifact (filesystem), with databases serving as history/telemetry/
registry only.

Concepts (all independently versionable):
    Sample / Setup / Strategy / Model / LabelSchema / NewsContext.
"""

from __future__ import annotations

from nexus_scalp.model_generation.architectures import ARCHITECTURE_VERSION, TCNAttentionV1
from nexus_scalp.model_generation.artifact_store import ArtifactStore, default_artifact_root
from nexus_scalp.model_generation.dataset_factory import DatasetFactory
from nexus_scalp.model_generation.experiment_factory import EXPERIMENT_SPACE, ExperimentFactory
from nexus_scalp.model_generation.model_factory import ModelFactory
from nexus_scalp.model_generation.models import (
    DatasetManifest,
    ExperimentConfig,
    LabelSchema,
    ModelArchitecture,
    ModelManifest,
    NeuralLabel,
    NewsContextSchema,
    SampleContract,
    SetupContract,
    StrategyContract,
    ValidationResults,
    default_label_schema,
    default_news_context_schema,
)
from nexus_scalp.model_generation.replay import (
    SampleReplay,
    detect_feature_drift,
    detect_prediction_drift,
)
from nexus_scalp.model_generation.runtime import LocalModelRuntime, ManifestValidationError
from nexus_scalp.model_generation.sample_factory import SampleFactory, deterministic_sample_id
from nexus_scalp.model_generation.sample_maker import (
    TIER_A_MIN,
    TIER_B_MIN,
    TIER_C_MIN,
    HunterSampleMaker,
    attach_hunter_metadata,
    quality_tier,
)
from nexus_scalp.model_generation.schema_v2 import (
    SCHEMA_V2_ID,
    augment_existing_dataset_to_60d,
    build_60d_dataset,
    compute_60d_frame,
    verify_60d_artifact,
)
from nexus_scalp.model_generation.sequence import SequenceBuilder
from nexus_scalp.model_generation.sequence_training import SequenceCandidateTrainer
from nexus_scalp.model_generation.setup_detector import (
    HUNTER_MIN_QUALITY,
    SETUP_TYPES,
    SetupDetection,
    SetupDetector,
    validate_setup_type,
)
from nexus_scalp.model_generation.strategy_factory import (
    DEFAULT_HUNTER_STRATEGY,
    HUNTER_STRATEGIES,
    HUNTER_VERSION,
    EntryDecision,
    HunterStrategy,
    StrategyFactory,
    best_strategy_for,
    get_strategy,
)
from nexus_scalp.model_generation.training import (
    MAX_GRAD_NORM,
    CandidateTrainer,
    deterministic_candidate_id,
)
from nexus_scalp.model_generation.validation import (
    ValidationFactory,
    compare_news_ablation,
    compute_calibration,
    detect_class_collapse,
)

__all__ = [
    "ARCHITECTURE_VERSION",
    "DEFAULT_HUNTER_STRATEGY",
    "EXPERIMENT_SPACE",
    "HUNTER_MIN_QUALITY",
    "HUNTER_STRATEGIES",
    "HUNTER_VERSION",
    "MAX_GRAD_NORM",
    "SCHEMA_V2_ID",
    "SETUP_TYPES",
    "TIER_A_MIN",
    "TIER_B_MIN",
    "TIER_C_MIN",
    "ArtifactStore",
    "CandidateTrainer",
    "DatasetFactory",
    "DatasetManifest",
    "EntryDecision",
    "ExperimentConfig",
    "ExperimentFactory",
    "HunterSampleMaker",
    "HunterStrategy",
    "LabelSchema",
    "LocalModelRuntime",
    "ManifestValidationError",
    "ModelArchitecture",
    "ModelFactory",
    "ModelManifest",
    "NeuralLabel",
    "NewsContextSchema",
    "SampleContract",
    "SampleFactory",
    "SampleReplay",
    "SequenceBuilder",
    "SequenceCandidateTrainer",
    "SetupContract",
    "SetupDetection",
    "SetupDetector",
    "StrategyContract",
    "StrategyFactory",
    "TCNAttentionV1",
    "ValidationFactory",
    "ValidationResults",
    "attach_hunter_metadata",
    "augment_existing_dataset_to_60d",
    "best_strategy_for",
    "build_60d_dataset",
    "compare_news_ablation",
    "compute_60d_frame",
    "compute_calibration",
    "default_artifact_root",
    "default_label_schema",
    "default_news_context_schema",
    "detect_class_collapse",
    "detect_feature_drift",
    "detect_prediction_drift",
    "deterministic_candidate_id",
    "deterministic_sample_id",
    "get_strategy",
    "quality_tier",
    "validate_setup_type",
    "verify_60d_artifact",
]
