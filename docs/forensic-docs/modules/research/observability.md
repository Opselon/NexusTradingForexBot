# src/nexus_scalp/research/observability.py

- PURPOSE: TASK-21-RESEARCH-OBSERVABILITY persistence facade over the
  research observability tables (2026-08-20): research_gates,
  research_events, research_evidence (immutable vault), research_run_
  snapshots, research_worker_heartbeat, research_queue observability. All
  writes go through the AuditRepository background queue (never block the
  live path); all reads are bounded and JSON-safe (BUG-075 defense).
- ARCHITECTURE LAYER: Application persistence facade (SQLite through
  AuditRepository; no order authority).
- RESPONSIBILITY: gate lifecycle bookkeeping (create/start/finish/block/
  skip), event timeline, evidence vault store/get/list, run-snapshot store/
  get, worker heartbeat + health classification (spec 29/30), queue census,
  failure heatmap, family analytics, and the one-click full trace.
- DEPENDENCIES: AuditRepository (private `_db_path`/`_is_sqlite`/`_queue`),
  `research.evidence` models/enums, stdlib (sqlite3, uuid, time, json).
- CONNECTS TO: pipeline.validate_candidate (gate chain), worker
  (_emit_heartbeat -> beat), web/API trace + health endpoints, store
  reads (registry summary).

- KEY CONCEPTS:
  - `_gates` in-memory dict cache (lines 88, 141/166/223): rows are written
    through the background queue, so start/finish MUST NOT depend on a
    synchronous DB read — create/start/finish/block/skip update the cache
    first and queue the SQL; `get_gate` falls back to a DB read (with row
    decode) when not cached.
  - Gate writes: `_INSERT_GATE_SQL` (ON CONFLICT DO NOTHING), two update
    statements (`_UPDATE_GATE_SQL` for status fields, `_UPDATE_GATE_RESULT_
    SQL` for result/duration/retryable). finish_gate computes duration from
    started_at and stores evidence via store_evidence FIRST (the evidence
    is enqueued before the gate update references its id).
  - `beat` (629-667): upsert-by-scope heartbeat row; `worker_health`
    (669-722): classifies from last_beat_at age — FAILED if status FAILED;
    IDLE if not RUNNING; STUCK if beat age > 900s; DEGRADED if > 300s;
    HEALTHY otherwise.
  - `queue_snapshot` (728-778): GROUP BY gate_type/status census +
    RUNNING/QUEUED rows (limit 20) + top-15 failure reasons per gate type.
  - `gate_failure_heatmap` (784-818): failure counts by gate + rejection
    reasons tallied from research_runs.result_summary (lifecycle REJECTED +
    primary_failure/reason/rejection_reason keys).
  - `family_analytics` (820-861): per-family (fingerprint or symbol)
    candidate/validated/rejected counts, avg/best score, pass rate.
  - `trace` (867-889): assembles registry entry + runs + gates + events +
    evidence + snapshots for one strategy — the one-click forensic view,
    results capped (500 gates, 300 events, 5 snapshots).
  - `_registry_blocked_reason` (1034-1102): resolves WHY a strategy has not
    moved — latest gate by order_index/completed_at: FAILED/ERROR/BLOCKED ⇒
    blocked with reason+required; RUNNING ⇒ in progress; DISCOVERED/
    BACKTESTING/VALIDATING without any gate ⇒ NOT_STARTED reason telling the
    operator to run /api/research/validate.
  - JSON discipline: `_json` (None → '{}', never 'null') and `_read_json`
    (bad text → {}) everywhere (BUG-075).
- HOT PATH / PERFORMANCE: all writes queued; reads bounded (MAX_READ_LIMIT
  2000); per-call 5s-timeout connections; used on worker/API paths, never
  per tick.
- EDGE CASES & PITFALLS:
  - The `_gates` cache is per-Store-instance; two stores sharing one queue
    (worker + API) can read stale gate rows until a DB-backed get — the
    cache is authoritative only within one in-process instance.
  - `worker_health` treats a beat older than 900s as STUCK even when the
    worker is intentionally idle-but-running; IDLE only when status !=
    RUNNING, so a stopped-but-never-updated row reads IDLE correctly only
    if `stop` wrote it.
  - `_registry_entry` in trace reads the raw row and json-normalizes 10
    columns — `discovery_evidence` is included in the decode list but is
    not a strategy_registry column; the getattr-style access would raise
    KeyError... actually `out.get(col)` is used, so a missing column maps
    to None → `_read_json(None)` → {} (harmless).
  - `family_analytics` FAMILY key falls back to symbol when fingerprint
    absent (legacy rows), mixing two naming schemes in one "families" map.
  - `beat()` requires `scope` positional default 'research' — the worker
    passes scope explicitly; other consumers must match the scope string
    exactly or they write a second heartbeat row family.