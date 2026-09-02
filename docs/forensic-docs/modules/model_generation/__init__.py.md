# src/nexus_scalp/model_generation/__init__.py

- **PURPOSE:** Package entry point for PHASE 13 — the artifact-first MODEL
  FACTORY migration. Legacy ScalpNet is classified as LEGACY BASELINE (control
  group), NOT deleted — it stays loadable for benchmarking/rollback. The new
  center is the model artifact (filesystem), with databases serving as
  history/telemetry/registry only.
- **ARCHITECTURE LAYER:** Research/ML — model factory; inference needs no
  database. No order authority.
- **RESPONSIBILITY:** Re-export the public API of the 20 submodules so external
  callers (benchmarks, governance, CLI, web) import one coherent surface.
- **DEPENDENCIES:** every model_generation submodule it re-exports.
- **CONNECTS TO:** features (schema registry, scalar engines), training package
  (WalkForwardTrainer helpers), governance/CLI entry points.

- **KEY CONCEPTS:**
  - `__all__` exposes 70+ names: architectures (TCNAttentionV1, ARCHITECTURE_VERSION),
    artifact_store (ArtifactStore, default_artifact_root), dataset factory,
    experiment factory, model factory, contracts (models.py), replay, runtime,
    sample factory/maker, schema_v2 60D builders, sequence + sequence training,
    setup detector, strategy factory, training (CandidateTrainer, MAX_GRAD_NORM,
    deterministic_candidate_id), validation (ValidationFactory, calibration,
    class-collapse, news ablation).
  - The docstring defines the five independently-versionable concepts: Sample /
    Setup / Strategy / Model / LabelSchema / NewsContext.
- **EDGE CASES & PITFALLS:** Pure facade — heavy imports (torch) are pulled in
  transitively at import time (architectures, model_factory, training), so
  importing this package requires torch installed even for artifact-store-only
  work.