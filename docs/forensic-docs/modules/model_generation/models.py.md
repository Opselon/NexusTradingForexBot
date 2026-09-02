# src/nexus_scalp/model_generation/models.py

- **PURPOSE:** PHASE 13 domain contracts — "ARTIFACT-FIRST MODEL FACTORY":
  the center of the ML system is the MODEL ARTIFACT, not `model.py`, not a
  database row. A model artifact must be independently loadable and fully
  self-describing. All contracts are frozen; historical schemas are never
  silently reinterpreted.
- **ARCHITECTURE LAYER:** Research/ML — pure data contracts, no I/O.
- **RESPONSIBILITY:** Define LabelSchema (3-class neural target; WAIT is a
  POLICY state, NOT a training target), NewsContextSchema (12-field numeric
  news context), Sample/Setup/Strategy contracts (independently versionable),
  ModelManifest + DatasetManifest ("exactly what produced this?"),
  ExperimentConfig (bounded/explainable experiment space), ValidationResults,
  ModelArchitecture registry.
- **DEPENDENCIES:** pydantic only.
- **CONNECTS TO:** every model_generation module; `features.schema_contract`
  reads `default_news_context_schema().fields` for the 70D name tuple.

- **KEY CONCEPTS:**
  - `NeuralLabel` (line 38): 3-class StrEnum; WAIT explicitly NOT a label ("the
    legacy 4th logit is a policy bridge, not a label").
  - `LABEL_SCHEMA_3CLASS_V1` (line 47): triple_barrier_3class_v1 with
    generation params (TP 1.1×ATR, SL 1.0×ATR, max hold 15 bars, friction
    $0.35, embargo 3, no_trade_stride 3, max MAE ratio 0.75, min ATR 0.20).
  - `LabelSchema.encode/decode/validate_labels` (lines 84-105): strict mapping,
    raises on unknown label/value — the 3-class contract enforcement.
  - `NewsContextSchema` (line 125): 12 fixed fields (line 138), dimension 12;
    `vectorize()` maps a context dict to the fixed-order vector with 0.0 for
    missing/uncoercible fields — "a disabled news engine still yields a
    well-formed zero vector" (news_enabled=false ablation).
  - `SampleContract` (line 180): observable state only + `validate_schema()`
    raising on width mismatch; default feature_schema_id "scalp_v1"/50D.
  - `SetupContract` (line 211) / `StrategyContract` (line 223): frozen rule
    contracts independent of the model.
  - `ModelArchitecture` (line 243): LEGACY_SCALPNET_V1 (baseline/control),
    MLP_V2, TCN_V2, TCN_ATTENTION_V1, TRANSFORMER_V1.
  - `ModelManifest` (line 258): the self-describing artifact contract — identity
    (model_id/version/role/status), architecture, input schema, label schema,
    training lineage, strategy context, news contract (news_enabled +
    news_feature_provenance), validation statuses, integrity hashes
    (artifact_hash, manifest_hash, scaler_hash), and TASK-03-70D-PARITY fields
    feature_schema_hash + training_dataset_id (training==inference schema
    identity), plus AGENT-09 top-level provenance fields
    (liquidity_algorithm_version, training_commit). `digest()` (line 342):
    deterministic sha256 over the manifest excluding mutable hash fields.
  - `DatasetManifest` (line 357): dataset identity + source_identity_hash,
    row_counts per split, temporal_range, purge/embargo parameters, news
    provenance (news_schema_id, news_data_range, news_version), and the 70D
    feature_schema_hash.
  - `ExperimentConfig` (line 398): one bounded experiment (dataset, class_count,
    architecture + params, training dict, strategy, news flags, seed, notes).
  - `ValidationResults` (line 422): gates list + verdict REJECTED /
    CHALLENGER_ELIGIBLE; class_collapse_detected; news_ablation.

- **HOT PATH / PERFORMANCE:** Contracts only; `vectorize` is a 12-field float
  loop, trivial.

- **EDGE CASES & PITFALLS:**
  - `SampleContract` default feature_schema_id "scalp_v1" (50D) while the
    canonical research contract is now scalp_v3 (70D) — 70D samples must pass
    the schema explicitly; defaults silently describe the legacy 50D shape.
  - ModelManifest default class_count=3 while the LEGACY baseline head is
    4-wide (WAIT policy bridge) — 4-logit legacy models built through
    model_factory record class_count from the label schema (3) and must map
    logit 3 → WAIT policy in the runtime.
  - NewsContextSchema.vectorize treats ANY missing field as 0.0 — a truthful
    "news off" zero vector is indistinguishable from a "news on but dead" zero
    vector at the vector level; provenance (news_enabled in manifest) is the
    distinguisher.