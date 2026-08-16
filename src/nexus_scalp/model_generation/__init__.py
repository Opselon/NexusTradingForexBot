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
from nexus_scalp.model_generation.training import CandidateTrainer
from nexus_scalp.model_generation.validation import (
    ValidationFactory,
    compare_news_ablation,
    compute_calibration,
    detect_class_collapse,
)

__all__ = [
    "EXPERIMENT_SPACE",
    "ArtifactStore",
    "CandidateTrainer",
    "DatasetFactory",
    "DatasetManifest",
    "ExperimentConfig",
    "ExperimentFactory",
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
    "SetupContract",
    "StrategyContract",
    "ValidationFactory",
    "ValidationResults",
    "compare_news_ablation",
    "compute_calibration",
    "default_artifact_root",
    "default_label_schema",
    "default_news_context_schema",
    "detect_class_collapse",
    "detect_feature_drift",
    "detect_prediction_drift",
    "deterministic_sample_id",
]
