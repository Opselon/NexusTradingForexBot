# Phase 2 — Required Stage Matrix

| Stage | Write PG | Read PG | Write SQLite | Read SQLite | Lineage | Contract | Orphans | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DATA | yes | yes | yes | yes | dataset_id persisted on research_runs (NOT NULL) | yes | 0 runs without a registered strategy | VERIFIED |
| FEATURES | yes | yes | partial | partial | feature_schema_id + feature_dimension on training_runs (NULLABLE) | yes | 0 (all 6 training runs carry their feature lineage) | VERIFIED — schema nullable; application holds it |
| MODEL | yes | yes | yes | yes | model_id on training_runs (NULLABLE); registry keyed on (model_id, model_version) | yes | 6/6 training runs persist an EMPTY model_id — no run can be joined to its model | DEFECT FOUND — reported, not patched |
| STRATEGY | partial | partial | yes | yes | strategy_id on research_runs; strategy_registry.lifecycle | yes | 0 runs without a registered strategy | VERIFIED |
| BACKTEST | yes | yes | yes | yes | research_runs.run_id <- research_gates.research_run_id | yes | 0 gates without a parent run | VERIFIED |
| WALK-FORWARD | yes | yes | yes | yes | gate_type=WALK_FORWARD on research_gates, keyed to its run | yes | n/a (fold identity is the gate id) | VERIFIED |
| OOS | yes | yes | yes | yes | gate_type=OOS on research_gates; evidence keyed on gate_id | yes | evidence is per-gate; 43 runs lack evidence (see orphan report) | VERIFIED |
| ROBUSTNESS | yes | yes | yes | yes | gate_type=ROBUSTNESS on research_gates; evidence keyed on gate_id | yes | evidence is per-gate (corrected from a per-run assumption) | VERIFIED |
| COUNTERFACTUAL | no | no | yes | yes | shadow_comparisons.run_id -> shadow_runs.run_id | yes | 0 comparisons without a parent run | VERIFIED — SQLite-only (ops_shadow domain) |
| REPLAY | no | no | yes | yes | shadow_decisions.run_id -> shadow_runs.run_id | yes | 0 decisions without a parent run | VERIFIED — SQLite-only (ops_shadow domain) |
| VALIDATION | yes | yes | yes | yes | research_gates.research_run_id -> research_runs.run_id; governance events | yes | 43 COMPLETED runs without evidence (see orphan report) | VERIFIED |
| PROMOTION | no | no | yes | yes | model_promotion_audit (0 rows); governance state machine | yes | 0 promotion records (no promotion has ever been run) | VERIFIED — no LIVE promotion exists, by design |
