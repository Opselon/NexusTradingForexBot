# src/nexus_scalp/hygiene/index_health.py

- PURPOSE: Index Health Monitor (TASK-22) — runtime index health reports
  (spec §10 QUERY_HEALTH_REPORT): missing indexes (heuristic — high row
  count + WHERE/ORDER BY column with NO index), duplicate indexes (same
  column set covered by multiple indexes), unused indexes (estimated via
  per-index stat; NEVER dropped automatically — only REPORTED),
  polling_mode flag (live polling target → skip slow-query advisory).
- ARCHITECTURE LAYER: Application (read-only introspection).
- RESPONSIBILITY: IndexHealthMonitor with scan_missing / scan_duplicates /
  scan_unused / scan_table / scan_database / slow_query_report.
- DEPENDENCIES: sqlite3 (PRAGMA index_list / index_info / table_info,
  sqlite_stat1), dataclasses, datetime.
- CONNECTS TO: hygiene worker/report (evidence), web diagnostics, TASK-10
  migration planning (advisory SQL only).
- KEY CONCEPTS:
  - READ-ONLY by design — never creates/drops schema; schema changes go
    through TASK-10 migrations (hygiene discipline); WAL DBs are normal
    files; missing sqlite_stat1 (no ANALYZE) degrades gracefully.
  - SUSPECT_WHERE_COLUMNS (line 31): 26 high-value WHERE targets (ticket,
    order_id, position_id, trade_id, idempotency_key, article_hash,
    duplicate_of, source_id, article_id, analysis_id, run_id, symbol, ts,
    timestamp, generated_at, open_time, close_time, event_timestamp,
    window_start, created_at, updated_at, event_type, status, reason_code,
    strategy_id, request_id).
  - scan_missing (line 115): tables <1000 rows skipped (indexes a wash);
    leading index column (indexed.update(cols[:1])) is what matters;
    advisory CREATE INDEX SQL emitted with a "schema changes go through
    TASK-10" comment — never executed.
  - scan_duplicates (line 146): exact column-set tuples — same columns in
    ANY order flagged (column set equality, not prefix equality, so
    (a,b) vs (b,a) IS duplicate but (a) vs (a,b) is NOT).
  - scan_unused (line 167): indexes with no sqlite_stat1 entry →
    advisory UNUSED. NOTE the logic is inverted for its intent: an
    index ABSENT from sqlite_stat1 means the query planner never used it
    OR the DB was never ANALYZEd — the code flags absence, while a robust
    reading would flag low-usage rows; documented as advisory only.
  - polling_mode: skips the UNUSED scan for live polling targets.
  - scan_database (line 200): all tables (≤120) → findings + summary
    counts by category + generated_at.
  - slow_query_report (line 225): envelope only — slow_queries always
    [] here; instrumented callers fill it; advice encodes the
    never-CREATE/DROP-from-runtime invariant.
- HOT PATH / PERFORMANCE: COUNT(*) per table + PRAGMA per table — full
  introspection per hygiene cycle; bounded by max_tables=120; never on
  the tick path.
- EDGE CASES & PITFALLS: `indexed.update(idx["columns"][:1])` only checks
  the LEADING column — a table whose index begins with a different column
  than the WHERE target still flags MISSING even if the index helps;
  duplicate detection ignores index UNIQUENESS and origin/partial flags
  (a UNIQUE index and a non-unique index over the same columns are both
  flagged); sqlite_stat1 absence (never ANALYZEd) reports EVERY index as
  UNUSED — noisy on fresh DBs; f-string PRAGMA with table name from
  sqlite_master only (no user input).