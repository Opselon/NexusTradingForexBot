# src/nexus_scalp/hygiene/state.py

- PURPOSE: Worker State Store (TASK-11) — persists hygiene worker state +
  run history (spec §43, §51, §66) in a dedicated SQLite DB under
  artifacts/archive/_hygiene_state/hygiene_state.db: hygiene_worker_state
  (single row id=1: state/mode/cycle/last_scan/last_cleanup/last_success/
  last_failure/stats/updated_at) and hygiene_run_history (one row per run:
  run_id/database/started_at/finished_at/duration/mode/rows_scanned/
  duplicates_found/orphans_found/archived/deleted/errors/bytes_freed/
  verification_status/correlation_id/plan_json).
- ARCHITECTURE LAYER: Application (persistence adapter).
- RESPONSIBILITY: HygieneStateStore (get/set worker state, record_run,
  list_runs, recover_interrupted), new_run_id.
- DEPENDENCIES: sqlite3, json, uuid, datetime, pathlib, hygiene WorkerState
  enum.
- CONNECTS TO: hygiene worker/worker_runner (state transitions + run
  records), hygiene_runtime status(), startup recovery in the engine.
- KEY CONCEPTS:
  - get_state (line 92): empty row → synthetic IDLE/AUDIT_ONLY defaults
    (never raises); stats JSON parsed defensively (bad JSON → {}).
  - set_state (line 117): single-row upsert (CHECK id=1 + ON CONFLICT(id)
    DO UPDATE) — state/mode/cycle/last_* replaced wholesale; mode defaults
    to AUDIT_ONLY, cycle 0 when omitted.
  - record_run (line 160): INSERT OR REPLACE by run_id (a rerun with the
    same id overwrites — run ids are unique per cycle via new_run_id);
    errors stored as a JSON list; plan_summary serialized with default=str.
  - recover_interrupted (line 203): startup crash recovery — any run left
    with verification_status='IN_PROGRESS' is marked 'INTERRUPTED' and
    NEVER resumed blindly (spec §66); returns the count marked. The
    worker/executor must not auto-resume destructive batches from unknown
    state.
  - new_run_id (line 221): HYGRUN-<uuid12>.
- HOT PATH / PERFORMANCE: per-cycle writes only (2 rows per run); short
  connections with 5s timeouts; list_runs bounded at 50.
- EDGE CASES & PITFALLS: get_state returns scalar columns AS-IS (ints may
  come back as int but ISO text as str) — callers should not assume types
  beyond stats; set_state with stats=None persists "{}" and OVERWRITES
  existing stats — a caller wanting to preserve stats must pass them
  (full-row replace semantics); record_run expects run.get("verification")
  while the executor's result key is "verification" — consistent, but a
  caller passing verification_status would silently record ""; errors
  json.dumps without default=str — non-serializable error objects raise
  inside record_run and lose the run row (no try/except).