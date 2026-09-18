"""Labeling science package for the Nexus Scalp Engine (Stream C).

Exports:
  - TripleBarrierLabeler, TripleBarrierConfig, TripleBarrierMetrics
  - compute_sample_uniqueness, compute_concurrency_events, normalize_sample_weights
  - compute_uniqueness_metrics, add_sample_weights_to_dataframe, export_sample_weights_artifact
  - create_weighted_dataloader, WeightedTensorDataset, SampleWeightedCrossEntropyLoss, SampleWeightedFocalLoss
"""

from nexus_scalp.labeling.sample_weights import (
    SampleUniquenessReport,
    SampleWeightedCrossEntropyLoss,
    SampleWeightedFocalLoss,
    WeightedTensorDataset,
    add_sample_weights_to_dataframe,
    apply_time_decay,
    compute_concurrency_events,
    compute_return_attributed_weights,
    compute_sample_uniqueness,
    compute_uniqueness_metrics,
    create_weighted_dataloader,
    export_sample_weights_artifact,
    normalize_sample_weights,
)
from nexus_scalp.labeling.triple_barrier import (
    TripleBarrierConfig,
    TripleBarrierLabeler,
    TripleBarrierMetrics,
    compute_triple_barrier_metrics,
)

__all__ = [
    "SampleUniquenessReport",
    "SampleWeightedCrossEntropyLoss",
    "SampleWeightedFocalLoss",
    "TripleBarrierConfig",
    "TripleBarrierLabeler",
    "TripleBarrierMetrics",
    "WeightedTensorDataset",
    "add_sample_weights_to_dataframe",
    "apply_time_decay",
    "compute_concurrency_events",
    "compute_return_attributed_weights",
    "compute_sample_uniqueness",
    "compute_triple_barrier_metrics",
    "compute_uniqueness_metrics",
    "create_weighted_dataloader",
    "export_sample_weights_artifact",
    "normalize_sample_weights",
]
