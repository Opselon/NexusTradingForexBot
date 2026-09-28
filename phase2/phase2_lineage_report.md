# Phase 2 — Lineage Report

generated: 2026-09-28T01:41:37.852781+00:00
live database: `C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db`

## Lineage findings

### model_identity_missing_on_training_runs

- stage: **MODEL**  severity: **high**
- finding: 6 of 6 completed training runs persist model_id = '' (empty string, not NULL). The registry holds 6 distinct model ids, so no training run can be joined to the model it produced.
- location: `training_runs.model_id`
- enforcement: neither (nullable, no FK, no application guard on this path)
- owner: Agent 1 (runtime training path)
- action: reported — production file not modified

### model_fingerprint_not_globally_unique

- stage: **MODEL**  severity: **medium**
- finding: 26 registry rows carry only 16 distinct artifact_fingerprint values (10 reuse). A reader treating the fingerprint as a global identity key collides.
- location: `experience_model_registry.artifact_fingerprint`
- enforcement: neither (no UNIQUE constraint or index)
- owner: Agent 1 (registry identity)
- action: reported — production file not modified

### runs_without_registered_strategy

- stage: **STRATEGY**  severity: **low**
- finding: 0 research_runs reference a strategy_id that is not in strategy_registry
- location: `research_runs.strategy_id`
- enforcement: application-only (no FK)
- owner: Agent 1 (research pipeline)
- action: reported — production file not modified

### cross_table_lineage_is_application_only

- stage: **ALL**  severity: **structural**
- finding: the live SQLite store declares 0 foreign keys across 74 tables; every parent/child relationship in the lifecycle is held by application code only
- location: `schema-wide.n/a`
- enforcement: application
- owner: schema (cross-cutting)
- action: documented in phase2_constraint_audit.md

## Required stage matrix

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

## Store counts (live, read-only)

- research_runs: 4357
- research_gates: 26140
- research_evidence: 18316
- training_runs: 6
- training_runs_empty_model_id: 6
- experience_model_registry: 26
- strategy_registry: 4109
- shadow_runs: 63
- shadow_decisions: 55342
- shadow_comparisons: 61
- model_governance_events: 59164
- model_governance_state: 0
- model_promotion_audit: 0
- gates_without_run: 0
- runs_without_strategy: 0
- decisions_without_run: 0
- distinct_model_fingerprints: 16
- distinct_model_rows: 26
