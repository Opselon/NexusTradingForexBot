# src/nexus_scalp/incidents/worker.py

- PURPOSE: Incident background worker (TASK-12 spec 58/59, TASK-13
  STEP-01/02/03) — drains telemetry backlog, correlates into incidents,
  auto-attaches impact + recovery plan, saves bounded, alerts Telegram.
  BACKGROUND via asyncio.to_thread from the live engine — never on the
  tick path (INV-001); READ-ONLY + BOUNDED; no trading mutation (spec 0);
  no automatic recovery execution (spec 29).
- ARCHITECTURE LAYER: Application (background task, stateful runtime).
- RESPONSIBILITY: state machine STARTING/RUNNING/DEGRADED/STOPPING/STOPPED/
  FAILED (TASK-13 spec 7 — alive process ≠ healthy worker; health requires
  demonstrated progress via last_useful_work + incident counters); bounded
  cycle budget (30s), max saves/cycle (50), max events/cycle (2000), ring
  backlog (2000); latency percentiles p50/p95/p99.
- DEPENDENCIES: correlator, impact, models, store, telegram (lazy), logging.
- CONNECTS TO: LiveEngine (asyncio.to_thread), producers via ingest(),
  /api/diagnostics (format_incident_worker_status), store, reports.
- KEY CONCEPTS:
  - State machine: start()/stop()/fail(); tick() only runs in RUNNING or
    DEGRADED. Consecutive failure policy: ≥2 → DEGRADED (transient), ≥5 →
    FAILED (persistent, recoverable via start()); DEGRADED auto-recovers to
    RUNNING on a successful cycle.
  - tick() (line 157): interval-gated (60s default); merges external events
    with the in-memory backlog (ingest() is a bounded ring — producers never
    block; overflow drops OLDEST events with events_dropped++).
  - `_cycle_once` (line 234): backlog + events → _to_telemetry (lenient) →
    list_incidents(limit=200, last_seen_at order) → correlate → for each
    incident: analyze impact (auto, read-only), generate recovery plan if
    empty (auto), save up to MAX_SAVES_PER_CYCLE. New-vs-updated tracked
    against the pre-cycle id set; dedup accounting = result.merged +
    result.unchanged.
  - Telegram: alerts only CRITICAL/HIGH (or MEDIUM when min_severity
    MEDIUM); per-incident maybe_alert (cooldown 900s / repeat 3600s);
    failures degrade to debug log.
  - `_to_telemetry` (line 292): timestamp parsing handles datetime (naive
    → UTC), numeric epoch with ms-vs-seconds discrimination (≥1e12 is
    millis — TEST-TIMEBASE-03/04; ambiguous small values treated as
    seconds), ISO strings with "Z"→+00:00; bad input → None (dropped).
    ticket falls back to execution_id; source invalid → TELEMETRY; payload
    = residual keys.
  - `format_incident_worker_status` (line 347): JSON-serializable worker
    telemetry for the REST layer — state, progress counters, latency
    percentiles, queue depth, error details.
  - latency_percentiles (line 222): nearest-rank on the sorted recent
    window (LATENCY_WINDOW=200).
- HOT PATH / PERFORMANCE: interval-gated off-tick; bounded everywhere
  (backlog 2000, events 2000, saves 50, list 200); the 200-incident list +
  correlate is the main per-cycle cost — O(events×incidents) in the worst
  case but indexed dicts keep merges near-constant.
- EDGE CASES & PITFALLS: tick() returning False when interval not elapsed
  is NORMAL (not an error); MAX_SAVES_PER_CYCLE cut-off skips SAVING but
  still counts created (created is computed AFTER save loop from the
  pre-cycle set — an incident created in-memory but not saved still counts
  as created at line 267 only if saved; actually `created` only increments
  inside the save loop, so unsaved incidents are silently dropped without
  counter; events_dropped double counts nothing but overflow events and
  non-dict ingest() items are silently ignored; DEGRADED→RUNNING recovery
  on the same cycle that records last_error means last_error may be stale
  after recovery.