# src/nexus_scalp/model_generation/experiment_factory.py

- **PURPOSE:** Experiment Factory (PHASE 13, spec 18/22/36/37): training is
  experiment-driven; bounded, explainable experiment spaces — no random
  hyperparameter generation. Failed experiments are FAILED, never CHALLENGER;
  Champion is never overwritten.
- **ARCHITECTURE LAYER:** Research/ML — experiment config management.
- **RESPONSIBILITY:** The bounded EXPERIMENT_SPACE template registry + create/
  load/persist experiments bound to a dataset artifact + train_experiment
  convenience that routes to the candidate-safe trainer.
- **DEPENDENCIES:** artifact_store, models.ExperimentConfig, polars (for the
  train_experiment signature), logger; training.CandidateTrainer (lazy import).
- **CONNECTS TO:** benchmark (creates bench_{kind} experiments), training
  (train_experiment → CandidateTrainer), CLI/governance.

- **KEY CONCEPTS:**
  - `EXPERIMENT_SPACE` (line 25): 6 bounded templates — baseline_scalpnet_v1
    (LEGACY, 128/4 heads/0.25, 10 epochs, bs 256, lr 0.001, seed 42, no news),
    baseline_scalpnet_v1_news (same + news), mlp_v2 (± news), tcn_attention_v1
    (± news, 12 epochs bs 128 weight_decay 1e-4). No random search — the space
    is fixed and explainable.
  - `ExperimentFactory.create` (line 93): validates template, shallow-mutates
    base copy with `overrides` (line 110), experiment_id default
    `exp_<template>_<uuid8>`; seed override lands in training dict + config
    seed; persists experiment.json immediately (line 131). `load` (line 135)
    raises FileNotFoundError for unknown ids.
  - `train_experiment` (line 142): thin wrapper — delegates to
    `CandidateTrainer.train_candidate(experiment, dataset_frame, feature_cols)`;
    result dict {status: COMPLETED/FAILED, model_id, artifact?, error?}.
    Safety boundary inherited from the Phase 10 candidate/staging contract.
- **EDGE CASES & PITFALLS:**
  - `overrides` applies at the TOP level of the template dict only — a raw
    nested "training" dict in overrides REPLACES the template's training keys
    (dict(base) is shallow, line 108-110). Callers must merge nested dicts
    explicitly.
  - `news_schema_id` hardcoded "news_context_v1" at creation (line 128) —
    matches the model contract default; a future news schema version requires
    an explicit override path that does not exist here.
  - Experiment ids are NOT deterministic (uuid) unless provided — two identical
    creations (same template) yield different experiment artifacts; the
    DETERMINISTIC identity lives at the candidate level
    (deterministic_candidate_id in training.py).