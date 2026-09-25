# src/nexus_scalp/incidents/store.py

- PURPOSE: Incident Response SQLite store (read/write via audit.db) —
  incidents, incident_events, incident_value_traces, incident_quarantine
  tables, created by governed additive migration AUDIT-0005, never ad-hoc
  DDL (spec 58/59).
- ARCHITECTURE LAYER: Application persistence (bounded read/write facade).
- RESPONSIBILITY: canonical DDL mirror (INCIDENT_DDL), row↔Incident
  projection (row_to_incident / _incident_row_values), upsert save via the
  AuditRepository queued writer when available (no synchronous DB on tick
  path, INV-001) or a direct connection (CLI/tests), bounded queries
  (list/search/count/stats/recurring), evidence archive before delete
  (spec 45/46), and the evidence-gated lifecycle (IncidentLifecycle).
  Store NEVER deletes incident evidence automatically; retention is
  policy-driven.
- DEPENDENCIES: models, observability.logging, sqlite3 (WAL), json.
- CONNECTS TO: incidents worker/correlator/reports/telegram, /api/
  diagnostics, CLI db commands, tests.
- KEY CONCEPTS:
  - DDL: incidents table is a wide column store with JSON payload columns
    (evidence_json, impact_json, recovery_plan_json, *_json lists); events,
    traces, quarantine are child tables keyed by incident_id; quarantine has
    UNIQUE(incident_id, target_table, record_key). Indexes on status,
    severity, category, fingerprint, detected_at + child-table incident_id.
  - save(): upsert via `ON CONFLICT(incident_id) DO UPDATE SET ...excluded`
    (line 348). In queued mode (audit_repo._queue present) the write is
    put_nowait — failures are LOGGED, not raised (best-effort, never blocks
    the tick path). In direct mode it also flushes timeline events
    (INSERT OR REPLACE with a SELECT-id trick for idempotency, line 373),
    traces (INSERT OR IGNORE), quarantine (INSERT OR REPLACE).
  - row_to_incident (line 181): defensive — every _dt/_json_loads falls back
    (naive→UTC, bad JSON→default, bad enum→“OTHER”/“UNKNOWN”/“LOCAL”),
    entire row failure logs + returns None instead of raising. Note: impact
    time range and affected_time_range are NOT restored (columns absent) —
    the wide-column projection drops the impact time range.
  - IncidentLifecycle.transition (line 733): VERIFIED is REFUSED unless
    fix_commit AND regression_test are set — refusal is recorded as a
    VERIFY_REFUSED timeline event (spec 30/31); mark_false_positive keeps
    the record with reason + evidence (spec 64).
  - search (line 600): exact incident_id match first, then bounded LIKE
    across 10 columns; dedup by incident_id; LIMIT capped at 100.
  - order_by severity maps to a CASE expression (line 537) so CRITICAL sorts
    first independently of string ordering.
  - archive_evidence (line 686) writes
    artifacts/incidents/archive/<incident_id>.json before a row may be
    safely removed.
- HOT PATH / PERFORMANCE: queued writes when a repo is attached; reads are
  bounded (list ≤500, search ≤100, recurring ≤100); per-call short-lived
  connections with 10s timeouts. child-table reads in get() are per-row
  loops — fine for single incident lookups.
- EDGE CASES & PITFALLS: queued mode does NOT persist incident_events/
  traces/quarantine (only the queued SQL for the main row) — direct mode is
  the only complete-write path; `get` queries require `incident_events`
  etc. tables to exist (missing table → sqlite3.Error → returns None with
  no distinction between "not found" and "schema missing"); list() default
  limit 100 up to 500; `_bool` treats "false"/"0"/"" as False (line 163).