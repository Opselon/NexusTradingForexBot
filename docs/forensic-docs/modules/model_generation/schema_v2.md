# src/nexus_scalp/model_generation/schema_v2.py

- **PURPOSE:** The 60D/70D dataset builders — the artifact-first dataset
  factory (TASK-5 60D + TASK-01..07 70D): `build_60d_dataset` /
  `build_liquidity_dataset` / `build_70d_dataset` produce deterministic
  causal frames (Base 50D + News 10D + Liquidity 10D per the canonical
  contract) AND their `verify_*_artifact` counterparts enforce the
  dataset-integrity floors (dimension, finiteness, determinism, schema
  hash, news/liquidity coverage).
- **ARCHITECTURE LAYER:** ML research (dataset factory; artifact-first —
  versioned filesystem artifacts with manifests).
- **RESPONSIBILITY:** (a) `bars_frame_to_bardata` — convert a raw bars
  polars frame to BarData + timestamps; (b) `compute_60d_frame` /
  `compute_liquidity_frame` / `compute_70d_frame` — build the feature
  frames with exact column geometry (feat_0..feat_69 + label columns);
  (c) `build_*_dataset` — orchestrates frame build → artifact store write →
  manifest (dataset_id, schema_hash, counts, coverage);
  (d) `augment_existing_dataset_to_60d` — upgrade path;
  (e) `verify_60d_artifact` / `verify_liquidity_artifact` /
  `verify_70d_artifact` — the integrity gates (dimensions, finite
  [-3,+3], schema hash match, per-family coverage stats, no NaN).
- **DEPENDENCIES:** polars, sklearn scaler artifacts, features
  (schema_augment/liquidity_engine/schema_contract), news_bridge,
  artifact_store, logging.
- **CONNECTS TO:** model_factory (train pipeline), benchmark matrix
  (60D/70D cells), replay parity tests (test_70d_dataset_parity_task3,
  test_70d_contract_parity_task3, test_70d_replay_parity_task3),
  validation suites.
- **KEY CONCEPTS:** Determinism + causality + parity: the same market
  snapshot yields the SAME frame in training and replay; the 70D frame
  verifies its schema hash against the canonical contract
  (235b8fccc96b7e0e family); verify gates FAIL loudly (never repair);
  coverage stats separate "no news events" (legit NO_OVERLAP) from "news
  pipeline broken" (coverage below floor).
- **EDGE CASES & PITFALLS:** ATR column naming (atr vs atr_m1) handled
  explicitly; empty tails (insufficient history) drop rows but LOG the
  drop; the verify gates must run BEFORE any train call consumes the
  artifact (garbage-in guard).