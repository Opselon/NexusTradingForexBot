# src/nexus_scalp/hygiene/retention.py

- PURPOSE: Retention Policy Engine (TASK-11) — policy-driven retention
  rules per table: minimum_retention (guaranteed keep), maximum_retention
  (candidate after — still NOT auto-deleted unless proven-safe class),
  archive_after, delete_after (age at which a proven-safe row may be
  deleted), never_delete (hard guard TIER-0/1/2/3/4). The engine NEVER
  decides deletion alone — cleanup requires an approved class AND
  confidence 1.0 AND a journal record. Default: UNKNOWN retention → KEEP
  (spec §73: when not 100% certain, keep).
- ARCHITECTURE LAYER: Domain (policy) — pure data + evaluation.
- RESPONSIBILITY: RetentionRule (one table's policy + age candidacy),
  canonical registries AUDIT_RETENTION / NEWS_RETENTION /
  CANDLE_RETENTION, RetentionEngine (lookup + age + classify).
- DEPENDENCIES: hygiene package DataTier enum, datetime.
- CONNECTS TO: hygiene worker planner (classify/age evidence via
  RetentionEngine), retention delete candidates, reports, docs
  (DATABASE_HYGIENE_MATRIX.md mirrored values, 2026-08-18).
- KEY CONCEPTS:
  - RetentionRule.is_age_candidate (line 48): never_delete → False;
    threshold = delete_after_days else maximum_retention_days; None →
    False. is_archive_candidate: archive_after_days only.
  - AUDIT_RETENTION — TIER-0/1 canonical NEVER deleted (audit_ledger,
    audit_experiences, audit_experience_outcomes, audit_broker_deals/
    orders/trades, audit_account_snapshots, audit_orders, audit_executions,
    audit_broker_history_meta, audit_experience_corrections); TIER-2
    research evidence never deleted (behavior_detections/analysis,
    anomaly_events, trade_autopsies, strategy_evolution_candidates,
    model_shadow_comparisons, model_comparisons, research_runs);
    TIER-3 model metadata never deleted (registries, governance, training
    runs, trading_rules_config); cleanable: audit_signals 7d
    (TIER-5, KEEP-class, rebuildable — mirrors BUG-054), audit_guard_
    telemetry 13d (TIER-7), position_lifecycle_events 3d (TIER-1 but the
    MOVING subset is telemetry), research_worker_state + intelligence_
    worker_state 30d (STALE_TEMP, TIER-7).
  - NEWS_RETENTION: everything never_delete except news_health 90d
    (TIER-5 rebuildable) and news_worker_state 30d (STALE_TEMP).
    news_articles declares cleanup_class DUPLICATE_WITH_CANONICAL (never_
    delete=True — only per-row exact-duplicate deletions reach it, the
    class signals the allowed path, not blanket retention deletion).
  - CANDLE_RETENTION: candles/candle_closures/candle_patterns/
    market_regimes/risk_evaluations/trade_decisions/rule_vetoes 30d
    (TIER-5 rebuildable); feature_vectors/trade_proposals 7d
    (TIER-6 EXPIRED_CACHE); open_positions/exit_signals 1d (TIER-7
    STALE_TEMP active-state mirrors); audit_log TIER-1 never_delete.
  - RetentionEngine.classify (line 658): unknown table → KEEP;
    never_delete → KEEP; unknown row age → KEEP; ARCHIVE before
    CANDIDATE (archive_after checked first).
  - age_days (line 635): ISO parser ("Z"→+00:00, bare 10-char dates →
    midnight UTC, naive → UTC) and epoch seconds; negative ages clamped
    to 0; parse failure → None (→ KEEP).
- HOT PATH / PERFORMANCE: pure dict lookups + float arithmetic — used
  per-row only in hygiene cycles; no I/O.
- EDGE CASES & PITFALLS: is_age_candidate treats delete_after_days=0 as
  "candidate immediately" but 0/None both mean "not applicable" per the
  dataclass docstring — a rule with delete_after_days=0.0 would return
  True for every age (0.0 is not None); position_lifecycle_events is
  TIER-1 with a 3d window — the rule encodes "MOVING subset is
  telemetry" but the RULE itself has no event-type granularity (the
  worker's SAFE_RETENTION_DELETES carries event_type="POSITION_MOVING"
  separately — the rule KEEP-class means the retention path is safe only
  for the MOVING rows; a generic caller using only this rule could
  mis-delete non-MOVING rows); age uses max(0, age) so future-dated rows
  classify KEEP.