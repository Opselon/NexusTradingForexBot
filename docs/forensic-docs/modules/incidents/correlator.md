# src/nexus_scalp/incidents/correlator.py

- PURPOSE: Incident correlation engine (TASK-12 spec 4/5/6/9/10/31) —
  turns a stream of telemetry events (log lines, DB anomalies, runtime
  symptoms) into canonical incidents: stable root fingerprint per event,
  dedup of repeats into ONE incident, correlation by correlation_id /
  ticket / execution identity, causal-chain recognition, first-divergence
  identification. NEVER mutates trading state — only groups evidence.
- ARCHITECTURE LAYER: Application (detection/correlation), pure in-memory.
- RESPONSIBILITY: DEFAULT_WINDOWS_SEC (per-category merge windows, from
  30s MT5/EXPOSURE to 900s DATA/LEARNING/RESEARCH/NEWS), CHAIN_HINTS
  (event-type → chain position: CALL_FAILED→ROOT_EVENT, EXCEPTION→
  PRIMARY_FAILURE, STATE/CACHE_STALE→STATE_CORRUPTION, POLICY_REJECTION/
  ORDER_REJECTED→DOWNSTREAM_EFFECT, SYMPTOM/UI_EMPTY/EMPTY→
  USER_VISIBLE_SYMPTOM), KNOWN_FAILURE_CLASSES (historical failure class →
  (category, severity)), SEVERITY_BY_CODE (evidence-driven severity map).
- DEPENDENCIES: incidents.models only (+stdlib time/dataclasses).
- CONNECTS TO: telemetry collector, worker, store, reports, web diagnostics.
- KEY CONCEPTS:
  - `correlate()` (line 257) matching priority: (1) correlation_id (any
    window) — strongest for user-visible errors; (2) ticket/execution
    identity; (3) fingerprint + per-category window overlap on last_seen_at;
    else new incident.
  - Events are processed in timestamp order (sorted), and dedup uses EVENT
    time — timelines are built from actual timestamps, never wall-clock
    processing order (spec 10).
  - Dedup ring (line 329): key = incident:event_type:error_code within 5s
    → counted unchanged, skipped (ring bounded at 20000 keys, trimmed at
    600s cutoff).
  - `_merge_event` (line 428): mark_seen, repeated_count++, ticket appended
    to affected_records once, correlation_id backfilled, severity can only
    ESCALATE (evidence-driven), timeline event appended.
  - `_infer_severity`: SEVERITY_BY_CODE first, else MEDIUM (conservative).
  - `_infer_category`: KNOWN_FAILURE_CLASSES first, else substring match of
    component name (e.g. "EXPERIENCE"→LEARNING, "WEB"→UI, "UPDATE"→VERSION),
    else OTHER. NOTE: loose `key in comp` matching (line 209) — e.g. any
    component containing "MODEL" maps to MODEL.
  - `classify_chain` (line 344): sorts timeline events and buckets them
    into canonical positions in ROOT_EVENT → PRIMARY_FAILURE → STATE_
    CORRUPTION → DOWNSTREAM_EFFECT → USER_VISIBLE_SYMPTOM order; ≥4
    occupied positions → CHAIN, ≥2 → FAN_OUT, else SINGLE. Sequence comes
    from ACTUAL timestamps only (spec 10) — never invented.
- HOT PATH / PERFORMANCE: O(events × existing); index dicts (by_fp, by_corr,
  by_ticket) keep merges near-constant; sorted() per batch. Ring trim is
  amortized O(ring).
- EDGE CASES & PITFALLS: fingerprint uses `error_code or event_type`
  (line 289) — events without error codes fingerprint on event_type;
  correlation_id wins over fingerprint even across categories (a shared
  correlation_id can merge distinct failure classes into one incident);
  the 5s dedup key includes incident_id so identical events hitting
  DIFFERENT incidents within 5s are NOT deduped.