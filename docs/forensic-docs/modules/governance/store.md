# src/nexus_scalp/governance/store.py

- PURPOSE: Governance Event Store — append-only, idempotent persistence
  for lifecycle transitions and every model-governance failure (TASK-6
  spec 30/31/35). REUSE-FIRST: only 6 NEW tables created (the rest of
  shadow data reuses PHASE 11 tables); writes go through the
  AuditRepository background queue so the live path is never blocked.
- ARCHITECTURE LAYER: Infrastructure (persistence adapter on audit.db).
- RESPONSIBILITY: GovernanceStore — schema mgmt (lazy once/process),
  record_event, record_transition (+state mirror), set_state,
  save_shadow_comparison, save_health, reads (get_state/list_events/
  list_comparisons/latest_health), promotion/rollback audit tables
  (TASK-08, migration AUDIT-0005): record_promotion_audit /
  record_rollback_audit / list_*_audits, summary.
- DEPENDENCIES: adapters.database.audit_repository (AuditRepository —
  uses its _queue and _db_path), governance.models, sqlite3, json, uuid.
- CONNECTS TO: GovernanceEngine, transaction, shadow_runtime, verify,
  forensics checks (reads same tables), UI/API.
- KEY CONCEPTS:
  - Tables created lazily (ensure_schema, once per process):
    model_governance_events (event_id UNIQUE → INSERT OR REPLACE =
    idempotent), model_governance_state (per-model current state),
    model_shadow_comparisons (bounded canonical comparison rows),
    model_runtime_health (periodic snapshots),
    model_promotion_audit (promotion_id PK, old/new champion pair,
    approval_token, rollback_target, status), model_rollback_audit
    (rollback_id PK, failed/previous, hashes, rollback_kind).
  - All writes are _queue.put_nowait — non-blocking; failures return
    False + error log (never raise to callers).
  - record_transition mirrors state via set_state (INSERT OR REPLACE on
    (model_id, model_version)).
  - READ consistency: get_state/list_events/list_promotion_audits call
    audit_repo._queue.join() FIRST so a just-recorded transition is
    visible to the next op (transitions are rare operator actions — never
    tick path). list_comparisons does NOT join.
  - Reads bounded: MAX_EVENTS_READ=2000; short-lived connections.
  - save_shadow_comparison writes probs as JSON text; note the
    `for c in prob_cols` cleanup at lines 378-380: it rebuilds args
    dropping index 0 ONLY when the column name is in row — the condition
    `i != 0 or c not in row` is always True at i==0... effectively a
    no-op (dead code, see pitfalls).
- HOT PATH / PERFORMANCE: ensure_schema guarded by _schema_ensured flag
  (DDL runs at most once per process, no per-tick CREATE); all writes
  queue puts; reads only on operator/API paths.
- EDGE CASES & PITFALLS:
  - save_shadow_comparison lines 378-380: the "drop raw lists" loop is
    logically dead — the comprehension retains `i==0` args
    unconditionally, so the intended column-strip never happens
    (probabilities are already JSON-serialized above; harmless but
    confusing dead code).
  - get_state joins the queue which can block up to the writer's flush
    cadence if the queue is backlogged.
  - Non-sqlite audit_repo (or None) → all methods return False/None
    silently (graceful degradation by design).
  - summary() counts only raw row counts (no schema-version awareness).