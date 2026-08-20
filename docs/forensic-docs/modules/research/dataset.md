# src/nexus_scalp/research/dataset.py

- PURPOSE: PHASE 09B deterministic ResearchDataset builder consuming the
  immutable Phase 08 experience ledger (NOT a parallel trade database) and
  producing causally-safe research samples (spec 5/6/7). Includes the TASK-4
  explicit eligibility audit with a structured rejection taxonomy and a
  per-trade rejection ledger.
- ARCHITECTURE LAYER: Research (derived state; read-only over the ledger;
  no order authority). Purely offline/background.
- RESPONSIBILITY: (1) enumerate ledger records (deduped by idempotency_key),
  (2) classify each executed+closed outcome ELIGIBLE or REJECTED with an exact
  reason — never a blanket LOW_EVIDENCE, (3) build causally ordered datasets
  with content-addressed dataset_id, (4) expose `audit()` so the API can
  explain WHY the registry is empty.
- DEPENDENCIES: `experience.ledger` (ExperienceLedger),
  `experience.models` (ExperienceRecord), `research.models`
  (ResearchDataset/ResearchSample), `adapters.database.audit_repository`
  (private `_db_path`/`_is_sqlite` for the reconstruction-source query),
  observability.logging.
- CONNECTS TO: pipeline (dataset build), worker `_refresh_dataset`,
  store.research_health_summary (via `audit()` + `build()`), OOS/walk-forward
  via `build_for_strategy(as_of=...)` leakage guard.

- KEY CONCEPTS:
  - Rejection taxonomy (lines 50-63): MISSING_OUTCOME, OUTCOME_PRECEDES_
    DECISION, MISSING_REALIZED_R/PnL (UNKNOWN recorded as zero), INVALID_PNL/R
    (non-finite), INVALID_INITIAL_RISK, MISSING_CONTEXT, MISSING_FEATURE_SCHEMA,
    INVALID_TIMESTAMP, FUTURE_LEAKAGE, SCHEMA_MISMATCH, MALFORMED_PROVENANCE,
    NON_FINITE_SAMPLE. `_RECOVERABLE_REASONS` = the three "outcome is missing"
    reasons (data can be repaired by reconciliation, lines 78-84).
  - Zero-substitution audit (lines 68-76, 170-194): an UNKNOWN broker result
    must NOT look like a real break-even zero. `_AUTHORITATIVE_RECONSTRUCTION_
    SOURCES` = {BROKER_DEALS, BROKER_DEALS_AGGREGATED, BROKER_NATIVE}; a zero
    R/PnL pair with a non-authoritative source (or NONE) is REJECTED as
    zero-substituted (BUG-046/045 pattern). A genuinely recorded zero from an
    authoritative broker reconstruction stays eligible. Tolerances: 1e-9 R,
    1e-6 PnL.
  - `_load_reconstruction_sources` (lines 108-140): the typed merged record
    does not carry broker_outcome (Phase 14 keeps it in the persisted outcome
    payload), so the authoritative source is read directly from
    `audit_experience_outcomes` (bounded LIMIT 100000), cached once per
    build/audit, guarded for non-sqlite backends.
  - `evaluate_sample` (lines 151-215): deterministic eligibility audit — 8
    ordered checks (outcome presence → causality → finiteness → zero-
    substitution → initial risk sanity > 1e-9 → context symbols+regime →
    feature schema → timestamps). Never raises; returns
    (eligible, reason, detail).
  - `_to_sample` (lines 217-248): maps ledger → ResearchSample preserving
    full provenance (regime/session/volatility/trend, feature_hash,
    confluence_fingerprint, MAE/MFE, duration, exit_reason).
  - `audit()` (lines 266-314): full eligibility census with per-trade
    rejection rows and a rejection-reason histogram; the `zero_substituted`
    counter sums MISSING_REALIZED_R + MISSING_REALIZED_PNL.
  - `build()` (lines 316-342): full-dataset build, dedup by idempotency_key
    (one economic trade = one observation), sorted by decision_timestamp,
    rejection logged per record with `[STRATEGY_RESEARCH] event=DATASET_REJECTED`.
  - `build_for_strategy(as_of=...)` (lines 344-369): leak guard — only
    samples whose DECISION is strictly BEFORE `as_of` enter (spec 7); feeds
    train/validation construction that can never peek into the future.
  - `_dataset_id` (lines 389-395): content-addressed — sha256 over
    (idempotency_key | decision epoch) pairs; unchanged ledger ⇒ same id ⇒
    worker uses it as its rebuild guard. Empty dataset ⇒ timestamp id.
  - `dataset_provenance` (lines 398-410): eligible lineage summary (schema
    distribution, strategy ids, source range) for spec 26 research-data
    versioning.

- HOT PATH / PERFORMANCE: bounded single provenance query per build/audit;
  per-strategy reads capped at 10000 records; runs on the worker cycle only.
  Note: `_iter_records` reads every strategy id — O(strategies × 10000).

- EDGE CASES & PITFALLS:
  - `_load_reconstruction_sources` returns {} for non-sqlite audit repos —
    every outcome then appears non-authoritative and zeros get rejected;
    safe (no false-positive evidence) but strict.
  - `build_for_strategy` does NOT dedupe by idempotency_key (build() does):
    a replayed entry could appear twice in strategy-scoped datasets; the
    dataset_id digest includes the keys, so the dataset identity still
    changes on duplicates.
  - `evaluate_sample` step 4 asymmetry: R=0 with non-authoritative source is
    always MISSING_REALIZED_R even when PnL non-zero (a zero-R scratch trade
    with real PnL is discarded — conservative but can shrink samples).
  - `_sample_id` derives from idempotency_key or experience_id — two records
    sharing a key collapse to the same sample_id (consistent with dedup).