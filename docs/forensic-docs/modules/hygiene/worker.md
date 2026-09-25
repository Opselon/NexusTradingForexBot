# src/nexus_scalp/hygiene/worker.py

- PURPOSE: Hygiene Planner / Executor / Verification (TASK-11) — the
  OBSERVE → CLASSIFY → PLAN → VALIDATE → CLEAN → VERIFY pipeline (spec
  §1, §17-18, §45-46). PLANNER is read-only (DeleteCandidate items with
  confidence/risk/reason/source_of_truth/retention_status); EXECUTOR is
  bounded, journaled, archive-before-delete, applies ONLY pre-approved
  safe classes at confidence 1.0, stops the moment verification fails;
  VERIFIER runs integrity_check / foreign_key_check / financial
  aggregates after every batch (spec §46) — never reports success before
  verification.
- ARCHITECTURE LAYER: Application (hygiene runtime core).
- RESPONSIBILITY: HygieneScanner (schema scan), HygienePlanner (evidence
  combination), CleanupExecutor (bounded journaled deletes), Verification
  Engine, financial_aggregates (audit.db accounting invariants, spec §24),
  SAFE_CLEAN_CLASSES + SAFE_RETENTION_DELETES policy tables.
- DEPENDENCIES: hygiene package (Confidence/WorkerMode), archive
  (ArchiveManager/CleanupJournal), detectors (DuplicateDetector/
  OrphanDetector/DuplicateCandidate), retention.RetentionEngine.
- CONNECTS TO: hygiene worker_runner / hygiene_runtime, CLI, web
  diagnostics (plan + executor results).
- KEY CONCEPTS:
  - Budgets (lines 34-39): MAX_ROWS_SCANNED 200k, MAX_ROWS_DELETED 2000,
    MAX_ROWS_ARCHIVED 5000, MAX_RUNTIME_MS 30s, MAX_LOCK_MS 2000,
    DELETE_BATCH_SIZE 200.
  - SAFE_CLEAN_CLASSES: only DUPLICATE_WITH_CANONICAL / STALE_TEMP /
    EXPIRED_CACHE / REBUILDABLE_DERIVED may EVER be auto-applied.
  - SAFE_RETENTION_DELETES (line 53) — the retention evidence windows:
    audit: audit_signals 7d (generated_at), audit_guard_telemetry 13d
    (window_start, pk rowid_del→rowid), position_lifecycle_events 3d
    (event_timestamp, POSITION_MOVING only), research/intelligence_
    worker_state 30d; news: news_health 90d, news_worker_state 30d;
    candle_intel: candles/candle_closures/candle_patterns/market_regimes/
    risk_evaluations/trade_decisions/rule_vetoes 30d (ts), feature_vectors/
    trade_proposals 7d, open_positions/exit_signals 1d. (mirrors BUG-054
    purge contract + derived candle rows)
  - HygienePlanner.build_plan (line 165): duplicates → orphans →
    retention candidates (COUNT rows older than cutoff; event_type filter
    for POSITION_MOVING); retention delete candidates only in SAFE_CLEAN/
    AGGRESSIVE_CLEAN (EXACT_DUPLICATE confidence is used as the
    "policy-proven" marker for range deletes); duplicate delete candidates
    require EXACT_DUPLICATE + canonical_row_id; everything else lands in
    blocked. ORPHANS ARE NEVER DELETED — reported only (plan.orphans).
  - CleanupExecutor.apply_plan (line 396): DRY_RUN/AUDIT_ONLY → nothing
    deleted (verification SKIPPED_DRY_RUN). SAFE_CLEAN path: per-candidate
    checks — runtime budget, delete budget, class in SAFE_CLEAN_CLASSES,
    confidence == EXACT_DUPLICATE, row_id not None (range candidates go
    through the bulk path), _table_rows_sql delete path exists (schema
    change → BLOCKED NO_DELETE_PATH), canonical row re-verified live
    (_canonical_exists — news articles must have duplicate_of → an existing
    article_hash sibling), archive_before_delete via ArchiveManager,
    journal.record (DELETE_AFTER_ARCHIVE, verification PENDING), DELETE by
    pk, VERIFY every batch_size deletes — any failure → STOP.
  - _apply_retention_batches (line 583): bulk bounded deletes using
    `WHERE pk IN (SELECT pk … LIMIT n)` subqueries; GLOBAL budget shared
    across ALL tables — decremented after EVERY batch (hyg34 fix: 5005-row
    table with 2000 budget must not exhaust the budget in one table);
    rows removed from already_deleted count against the cap.
  - VerificationEngine.verify (line 276): PRAGMA integrity_check == "ok",
    foreign_key_check empty, financial aggregates unchanged (<1e-6) for
    audit.db.
  - financial_aggregates (line 311): ledger_rows, pnl_sum (status !=
    'OPENED'), broker_trades, experiences, outcomes — missing tables are
    skipped silently.
- HOT PATH / PERFORMANCE: all reads COUNT(*)/bounded; deletes batched at
  200; busies never forced (busy_timeout=2000, defer).
- EDGE CASES & PITFALLS: _apply_retention_batches decrements the global
    budget TWICE per table when the table fully drains (line 641 inside
    the loop AND line 647 after) — a table that consumed exactly N rows
    deducts N + total again, over-restricting later tables; retention
    candidates are counted via `now - timedelta(days)` ISO comparisons —
    string comparison requires ISO-8601 text in ts_col (mixed formats
    miscompare); the executor's verification runs every batch_size (200)
    DELETES but the retention path verifies only once at the end;
    _canonical_exists returns True for every table EXCEPT news_articles
    (non-news tables trust the detector); PK mapping is hard-coded per
    (db_key, table) — audit_experience_outcomes uses "id", news tables
    use *_id; deletes are prepared with f-strings but only against the
    whitelisted cfg/table pairs (no user input reaches them).