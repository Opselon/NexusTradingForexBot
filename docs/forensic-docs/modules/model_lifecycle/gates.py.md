# src/nexus_scalp/model_lifecycle/gates.py

- **PURPOSE:** The 12 explicit, mandatory validation gates plus model-collapse
  protection (spec 20/21/22/38). A candidate fails if ANY mandatory gate fails;
  failures are NEVER hidden behind an aggregate score.
- **ARCHITECTURE LAYER:** Research/ML — validation gates, no order authority.
- **RESPONSIBILITY:** Gate 1-12 implementations + `run_gates` runner + collapse
  guard. Thresholds are module constants so floors are auditable.
- **DEPENDENCIES:** models (GateResult, TrainingDataset), math, logger. Gate 5-10
  accept either Phase 09 research result objects or plain dicts (duck-typed).
- **CONNECTS TO:** orchestrator (`_evaluate_gates` wires a subset), comparison
  (GateResult for GATE10), Phase 09 research engines (OOS, robustness,
  walk-forward results).

- **KEY CONCEPTS — the 12 gates as implemented:**
  - GATE1_DATASET (`gate_dataset_integrity`, line 47): dataset non-empty,
    `ordered_rows()` strictly temporally ordered, non-empty source-experience
    provenance, feature_dimension > 0.
  - GATE2_SCHEMA (`gate_schema_compatibility`, line 68): dataset
    feature_schema_id == artifact schema id AND dimensions equal. Explicit
    mismatch failure.
  - GATE3_LABELS (`gate_label_integrity`, line 95): labels 0,1,2 all present
    (count>0), each ≥ 5% of total (MIN_CLASS_RATIO=0.05, line 38), dataset not
    single-class.
  - GATE4_STABILITY (`gate_training_stability`, line 120): final_loss present +
    finite + ≤ 1e3; nan_inf_fraction ≤ 0.001 (MAX_NAN_INF_FRACTION line 40).
  - GATE5_VALIDATION (`gate_validation_performance`, line 143): validation
    accuracy ≥ 0.35 default floor (min_accuracy parameter).
  - GATE6_WALK_FORWARD (`gate_walkforward`, line 159): dict or WalkForwardResult;
    passes on `passed` flag; reports avg_oos + fold_count.
  - GATE7_OOS (`gate_oos`, line 181): status == "PASS" AND oos_expectancy_r ≥ 0.0
    (min_oos_expectancy default; a positive floor is NOT hardcoded — the default
    floor is 0.0).
  - GATE8_ROBUSTNESS (`gate_robustness`, line 200): status == "PASS" (max
    degradation reported).
  - GATE9_RISK (`gate_risk_drawdown`, line 221): max_drawdown_r ≤ 10.0R default
    ceiling.
  - GATE10_COMPARISON (`gate_champion_comparison`, line 238): comparison
    `eligible` bool + reasons.
  - GATE11_ARTIFACT (`gate_artifact_integrity`, line 257): integrity_ok AND
    feature_dimension + num_classes present.
  - GATE12_REPRODUCIBILITY (`gate_reproducibility`, line 280): run_id,
    dataset_id, schema_id, seed all truthy.
  - Collapse protection (`check_model_collapse`, line 295): class > 99% of
    predictions, or <2 classes predicted, or constant output, or avg max-prob >
    0.999 (MAX_PROB_SATURATION), or non-finite probabilities, or nan_inf_fraction
    > 0.001 — reports as gate COLLAPSE_GUARD.
  - `run_gates` (line 344): runs all callables; ANY failure ⇒ False;
    `ValidationGateError` raised inside a gate fn becomes a failed "UNKNOWN" gate.

- **EDGE CASES & PITFALLS:**
  - GATE5's floor (0.35) is BELOW a 3-class no-information baseline for accuracy
    (~0.33-0.5) — it is a sanity floor, not an evidence floor; Phase 13 requires
    macro-F1/balanced-acc floors instead (validation.py >0.34/0.34).
  - GATE7 floor of 0.0 means "non-negative OOS expectancy" — matches spec 38.17
    reading (failure rejects) but a model with tiny positive expectancy passes.
  - GATE3 uses ratio counts but does not require the class to appear in
    PREDICTIONS (that is the collapse guard's job).
  - Gate lambdas in orchestrator capture the CURRENT value of `oos_result` etc.
    at closure creation — the orchestrator appends them only when non-None, so a
    failed OOS evaluation silently OMITS GATE7/GATE9 rather than failing them
    (see orchestrator notes).