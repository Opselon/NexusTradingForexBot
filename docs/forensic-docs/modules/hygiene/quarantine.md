# src/nexus_scalp/hygiene/quarantine.py

- PURPOSE: DataQuarantine (TASK-22) — MOVE → MARK → REPORT quarantine for
  suspicious/uncertain data. Instead of DELETE, uncertain rows are MOVED
  into the quarantine store with full provenance (who/when/what/why),
  MARKed in the hygiene journal, and REPORTed in cycle telemetry.
- ARCHITECTURE LAYER: Application (persistence adapter, separate SQLite
  store under artifacts/archive/_quarantine/quarantine_store.db — never
  inside an active query path).
- RESPONSIBILITY: QuarantineStore (quarantine/restore/resolve CRUD +
  immutable event trail), schema + indexes, stats.
- DEPENDENCIES: sqlite3, json, uuid, datetime, pathlib.
- CONNECTS TO: hygiene worker (evidence/suspect rows), incidents
  QuarantineManager (advisory marks), report, operator restore tooling.
- KEY CONCEPTS:
  - States: QUARANTINED → RESTORED (write-back of the snapshot) or
    RESOLVED_DELETED (verified source deletion) / EXTERMINATED.
  - quarantine() (line 111): dedupes one item per (database, table,
    row_id) while QUARANTINED — repeats append a REPEAT event (never a
    second row); the full row snapshot is stored as row_json
    (sort_keys, default=str); returns the existing item on repeat.
  - restore() (line 162): marks RESTORED + resolved_at + notes AND returns
    the row snapshot (with "row" parsed) for write-back — first-class
    restore operation, append-only event trail.
  - resolve() (line 180): RESOLVED_DELETED (or EXTERMINATED for the
    stronger state) after verified source deletion.
  - Safety contract: deleting the SOURCE row happens ONLY when the caller
    passes an approved cleanup class with EXACT_DUPLICATE confidence (the
    CleanupExecutor's gates) — otherwise the source stays and the
    quarantine copy is additional evidence (MARK + REPORT only).
  - SQL quoting: "database" and "table" are SQLite reserved words — every
    reference to those column names is double-quoted (schema + queries).
  - stats() (line 233): counts by status and by database.table pair + the
    store path.
- HOT PATH / PERFORMANCE: per-row writes only during hygiene cycles;
    connections are short-lived with 5s timeouts; list bounded at 200.
- EDGE CASES & PITFALLS: row_id is stored as TEXT (str(row_id)) — numeric
  and string ids for the same row compare equal in the dedupe check only
  after string coercion, but "1" vs 1 in different callers still dedupe
  correctly; _get_locked parses row_json lazily and degrades to {} on
  corrupt JSON; restore() returns the UPDATED item dict (status RESTORED)
  with the row snapshot embedded — callers must extract d["row"] for
  write-back; no TTL/cleanup of RESOLVED/EXTERMINATED items (store grows
  monotonically — append-only by design); quarantine() commits per call
  (no batch path); concurrent writers rely on SQLite locking with 5s
  timeout, single-connection-per-call.