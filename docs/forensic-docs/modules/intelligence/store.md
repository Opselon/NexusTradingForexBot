# src/nexus_scalp/intelligence/store.py

- PURPOSE: The bounded read facade over the intelligence tables —
  `position_lifecycle_events`, `trade_autopsies`, `behavior_detections`,
  `anomaly_events`, `strategy_evolution_candidates`. Every function is a
  read-path query for observability, forensics and the self-healing rebuild.
  This module owns NO write path.
- ARCHITECTURE LAYER: Application read facade (short-lived read-only SQLite
  connections; the live path is never blocked).
- RESPONSIBILITY (docstring lines 6-12): all intelligence is DERIVED from the
  authoritative Phase 08 ledger; writes are performed by the individual engines
  through the AuditRepository background queue; every read is bounded and opens
  a short-lived SQLite connection.
- DEPENDENCIES: `audit_repository.AuditRepository` (private _db_path /
  _is_sqlite), `intelligence.models` (typed reconstruction contracts), stdlib
  (json, sqlite3), observability.logging.
- CONNECTS TO: worker.py (load_autopsy admission checks),
  evolution.py (get_candidate reads), lifecycle.py (list_events_for_ticket),
  web API diagnostics and forensic dashboards.
- KEY CONCEPTS:
  - Bounds: MAX_READ_LIMIT=2000 for lifecycle; 500 for the other listers;
    list_autopsies/list_behavior_detections/list_anomaly_events default 100.
  - `load_lifecycle_events` (lines 47-99): ordered by sequence ASC per ticket;
    reconstructs FULL self-describing events from the persisted JSON payload
    (snapshot/performance/market/decision) — the payload is the authoritative
    event, the columns are the query surface. Parse failures degrade to
    `datetime.now(UTC)` for event_timestamp (never raises).
  - `load_autopsy` (lines 102-118): single-row PK-ish read by ticket; returns
    raw dict row (payload column included) or None.
  - `list_autopsies` (lines 121-148): newest-first by autopsied_at, optional
    strategy_id filter.
  - `list_behavior_detections` (lines 151-185): newest-first by detected_at,
    optional ticket/pattern filters.
  - `list_anomaly_events` (lines 188-256): optional ticket/anomaly_type
    filters; `grouped=True` default — ANOMALY-VERIFY-01: rows sharing the same
    incident identity (ticket + anomaly_type + algorithm_version) are collapsed
    into ONE incident with `observation_count`, `first_seen`, `last_seen` (a
    pure read-side projection — repeated observations are never deleted;
    TEST-ANOM-16/17/19). Kind note: the underlying table has no
    algorithm_version column in the SELECT — the group key reads it from the
    row dict; rows without it (pre-version rows) group under "".
  - `load_evolution_candidates` (lines 259-286): newest-first by
    discovered_at, optional status filter.
  - `count_autopsies` / `count_lifecycle_events` (lines 289-314): COUNT(*)
    helpers — SQLite returns int; failure → 0 rather than raise.
- HOT PATH / PERFORMANCE: read-only, per-call connections with 5s timeouts;
    bounded LIMITs everywhere; used on API/worker paths, never per tick.
- EDGE CASES & PITFALLS:
  - `load_lifecycle_events` re-validates payloads but does NOT merge the
    payload fields with column values when both exist — a payload/column
    divergence (e.g. from a legacy write) silently prefers the payload;
    columns are fallback only.
  - Lazy-schema shadow tables: missing tables (never-initialized DBs) surface
    as logged exceptions returning []/None — never raise.
  - `list_anomaly_events` grouping compares `detected_at` as STRINGS — safe
    only because the engine always persists ISO-8601 UTC (lexicographic order
    is chronological); a non-ISO timestamp would misorder first/last.
  - The anomaly group key includes algorithm_version, which the
    SELECT * of this table only contains if the column exists; historical rows
    written before the version column was added group under a single empty
    version — combined with the string comparison, pre-version duplicates can
    collapse incorrectly; acceptable for observability reads.