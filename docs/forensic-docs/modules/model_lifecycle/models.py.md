# src/nexus_scalp/model_lifecycle/models.py

- **PURPOSE:** Immutable Pydantic contracts for PHASE 10 controlled model
  training, Champion/Challenger management and training-run lineage. Every
  record is frozen; nothing here can be mutated after construction (rebuildable
  derived summaries use `model_copy`, never in-place edits).
- **ARCHITECTURE LAYER:** Research/ML — pure data contracts, no I/O, no order
  authority.
- **RESPONSIBILITY:** Enforce the four design invariants: (1) model-separated
  memory (artifact ≠ experience memory); (2) immutable training runs (no
  anonymous model files); (3) Champion/Challenger authority separation; (4)
  feature-schema awareness — every record carries `feature_schema_id` +
  `feature_dimension` so a schema mismatch fails explicitly, never silently
  reshapes.
- **DEPENDENCIES:** pydantic, `nexus_scalp.experience.models` (for
  `CANONICAL_FEATURE_DIMENSION` / `CANONICAL_FEATURE_SCHEMA_ID` defaults).
- **CONNECTS TO:** every model_lifecycle module; also the Phase 08 experience
  layer (canonical schema constants) and the trainer (WalkForwardTrainer
  contract).

- **KEY CONCEPTS:**
  - `ModelStatus` (StrEnum, line 32): lifecycle ladder CANDIDATE → CHALLENGER →
    CHAMPION, plus REJECTED / ARCHIVED / INVALID. CHALLENGER is "shadow-eligible,
    no production authority" — the no-auto-promotion contract.
  - `TrainingRunStatus` (line 43): STARTED/RUNNING/COMPLETED/FAILED/CANCELLED/
    INCOMPLETE. Docstring pins the invariant: interrupted runs are INCOMPLETE
    and can never be VALIDATED.
  - `TrainingDatasetRow` (line 54): one deterministic training sample with full
    provenance (sample_id, experience_id, idempotency_key, decision_timestamp,
    schema, vector, label, strategy, regime, symbol/timeframe/session, sample
    weight, outcome_r, exit reason). `validate_schema()` (line 91) raises when
    `len(feature_vector) != feature_dimension` — the explicit schema-mismatch
    failure. Timestamps are normalized to UTC via `_utc` validator (line 86).
  - `TrainingDataset` (line 101): dataset artifact with deterministic identity;
    `sample_count`, `label_distribution()`, `ordered_rows()` (temporal sort used
    for causality checks and training order).
  - `GateResult` (line 135): one gate outcome; `passed` is mandatory, reasons
    carried in `details`/`reason`.
  - `ModelArtifactInfo` (line 146): hash/size/schema/dimension/classes/scaler
    plus AI-Hub tensor diagnostics (actual_input_dimension, actual_output_classes,
    actual_hidden_dimension, class_head_name, scaler_dimension, integrity_reason)
    — populated by `integrity.inspect_artifact`.
  - `TrainingRun` (line 178): immutable lineage record — run/dataset/schema,
    model + parent champion ids, hyperparameters, seed, train/validation/OOS
    ranges, embargo/purge bars (defaults 15/15), timestamps, artifacts, metrics,
    gates, status, build_identity. `all_gates_passed` (line 226) requires gates
    non-empty AND all passed; `eligible_as_challenger` (line 230) additionally
    requires status COMPLETED — a FAILED run with passing gates is not eligible.
  - `ChampionChallengerComparison` (line 235): per-metric champion/challenger/
    delta dicts (expectancy_r, max_drawdown_r, oos_expectancy_r,
    calibration_score, robustness_status, stability), bounded improvement_score
    [0,1], eligibility bool + reasons.

- **HOT PATH / PERFORMANCE:** None — contracts only; the frozen rows are cheap
  to copy via `model_copy`. `ordered_rows()` sorts per call (used on dataset
  build and gate 1, not on the tick path).

- **EDGE CASES & PITFALLS:**
  - `label` is constrained `ge=0` only (line 72) — 0/1/2 contract is enforced by
    callers (dataset LABEL_MAP, label_schema.validate_labels in Phase 13), not
    here; a 4 (WAIT) would pass the field constraint silently.
  - `feature_vector` has no length validator at construction — mismatch is only
    detected when `validate_schema()` is called explicitly. Rows built by
    dataset.py always validate; hand-built rows may not.
  - `TrainingRun.eligible_as_challenger` ignores gate COUNT — an empty gates list
    is guarded by `all_gates_passed` requiring truthy gates.
