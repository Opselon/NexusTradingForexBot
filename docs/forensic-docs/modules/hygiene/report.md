# src/nexus_scalp/hygiene/report.py

- PURPOSE: Database Hygiene Reports (TASK-22) — structured report builders:
  DATABASE_HYGIENE_INITIAL_REPORT (full first-run audit, spec §4, written
  once to artifacts/archive/_hygiene_state/initial_audit.json), cycle
  telemetry (spec §15: cleanup_id/start/end/duration/records scanned-
  deleted-archived-quarantined/errors), QUERY_HEALTH_REPORT (index health
  summary), Telegram report TEXT builders (spec §16 shape). All builders
  are PURE functions over scan/cycle results — no DB access; no sending
  here — delivery goes through the engine's notifier (INV-010: telegram is
  a read-only consumer).
- ARCHITECTURE LAYER: Application (report rendering/persistence).
- RESPONSIBILITY: build_initial_audit_report, persist_initial_audit,
  build_cycle_telemetry, build_query_health_report, build_telegram_
  report_text, build_telegram_initial_report_text.
- DEPENDENCIES: json, datetime, pathlib only.
- CONNECTS TO: hygiene_runtime (all builders), web diagnostics, Telegram
  notifier (text consumers).
- KEY CONCEPTS:
  - build_initial_audit_report (line 32): aggregates plan totals from each
    db result (plan or plan_summary — both accepted), consistency
    violations, per-db index summaries, quarantine stats; verdict
    ACTION_REQUIRED when any violations else CLEAN.
  - persist_initial_audit (line 91): idempotent write — an existing
    initial_audit.json is first rotated to .prev (previous report kept) —
    preserves the first-run baseline across reruns.
  - build_cycle_telemetry (line 106): spec §15 shape — cleanup_id, mode,
    deep_maintenance flag, start/end, duration_ms, records_scanned/deleted/
    archived/quarantined, deleted_by_table + archived_by_table maps,
    errors, verification.
  - build_query_health_report (line 139): merges per-DB index findings,
    splits MISSING (ref_sql list for later migration planning), DUPLICATE
    and UNUSED (table+detail) — with the TASK-10 advisory: CREATE INDEX
    statements are advisory only, schema changes go through the migration
    engine, never the runtime worker.
  - build_telegram_report_text (line 161): spec §16 "DATABASE HYGIENE
    REPORT" — Cycle/Scanned/Removed/Archived/Quarantined/Duration/Mode/
    Status + error count; plain tag-safe text.
  - build_telegram_initial_report_text (line 180): first-run digest with
    verdict.
- HOT PATH / PERFORMANCE: pure dict/str work, per cycle only.
- EDGE CASES & PITFALLS: initial report totals read plan keys via
  `res.get("plan", res.get("plan_summary", {}))` — the planner's summary
  lacks tables_scanned/rows_scanned when only summary() was persisted
  (plan.summary() includes both, so consistent); verdict is based ONLY on
  consistency violations (duplicates/orphans/delete candidates do not
  raise verdict); .prev rotation overwrites the previous .prev (only one
  backup level); telegram text never contains error DETAILS (just the
  count) — debugging requires the cycle telemetry.