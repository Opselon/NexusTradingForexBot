# src/nexus_scalp/shadow/shadow70/store.py

- PURPOSE: 70D shadow persistence (TASK-05-70D-SHADOW spec 12/13/14/40).
  REUSE-FIRST: observations persist into the CANONICAL audit.db via the
  existing AuditRepository background queue — NO synchronous DB on the
  tick path and NO new unrelated database. Idempotency: observation_id is
  deterministic; a reconnect/retry CANNOT duplicate a row (INSERT OR
  IGNORE on the unique key).
- ARCHITECTURE LAYER: Infrastructure (persistence on audit.db).
- RESPONSIBILITY: Shadow70Store (save_observation/record_event/
  save_feature_health/save_drift_alerts + reads), Shadow70Persistence
  (write contract interface), Shadow70BackpressurePolicy (bounded queue
  drop policy), lazy schema for 4 tables.
- DEPENDENCIES: adapters.database.audit_repository, models
  (Shadow70Observation), sqlite3, json, logging.
- CONNECTS TO: Shadow70Worker (flush batches → store), runtime (via
  worker), forensics liquidity checks (shadow70_drift_alerts /
  shadow70_feature_health reads), UI summary.
- KEY CONCEPTS:
  - Tables: shadow70_observations (observation_id UNIQUE),
    shadow70_events (event_id UNIQUE, INSERT OR IGNORE — idempotent),
    shadow70_feature_health (snapshot_id per health batch),
    shadow70_drift_alerts (alert_id UNIQUE). Lazy schema once per
    process (same _schema_ensured discipline as shadow/store.py).
  - Backpressure: save_observation checks the AuditRepository queue
    size (`_queue.qsize()`) against max_queue (default 2000) — over the
    cap → record_drop (SHADOW_BACKPRESSURE event, dropped_snapshots++),
    returns False. coalesced counter exists but is never incremented
    (see pitfalls).
  - save_drift_alerts builds alert_id deterministically:
    "drift70_{feature}_{metric}_{samples}" — repeated identical alerts
    collide → INSERT OR IGNORE = dedup (a feature's alert for the same
    sample count is stored once).
  - Writes json-dump probabilities/features/payload; all queue
    put_nowait.
  - Reads: list_observations (disagreement_only filter),
    list_events, latest_drift_alerts, latest_feature_health
    (10 rows by id DESC — NOTE: all from one snapshot since the health
    insert uses a single ts per batch), disagreement_counts (GROUP BY),
    summary() (truthful counts from REAL rows, spec 46 — no fake values).
- HOT PATH / PERFORMANCE: save_observation is the tick-path write —
    queue-size check + put_nowait, both O(1); BackpressurePolicy.should_drop
    calls qsize() — cheap; ensure_schema flag avoids per-tick DDL.
- EDGE CASES & PITFALLS:
  - BackpressurePolicy.coalesced is defined and exposed but NEVER
    incremented anywhere — the "coalesce" half of the drop/coalesce
    policy is unimplemented (dead telemetry).
  - The qsize gate uses a synthetic shim type when _queue is missing —
    `getattr(audit_repo, "_queue", type("Q", (), {"qsize": lambda s:
    0})())` — a None audit_repo short-circuits earlier, but a repo
    without _queue would silently never drop.
  - save_feature_health uses ONE snapshot_id (from the first row) and
    ONE timestamp for the whole batch; rows with different snapshot_ids
    would be mislabeled (worker passes a single batch, so fine).
  - latest_feature_health returns the LAST 10 rows by id — these are the
    last batch's 10 features, not the latest per-feature rows.
  - reads do not queue.join() (unlike governance.store) — a just-flushed
    observation may be briefly invisible to readers.