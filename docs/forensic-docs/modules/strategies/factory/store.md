# src/nexus_scalp/strategies/factory/store.py

- PURPOSE: Strategy Factory persistence store (2026-08-20) — factory
  research memory persisted through the SAME AuditRepository background
  queue as the research layer (spec 38/41/74/75). Tables:
  factory_generations, factory_candidates (+ structural verdict),
  factory_failures (structured rejection reasons, spec 23),
  factory_events (immutable event stream, spec 50), factory_runs
  (research-run ledger, spec 40), factory_provider_usage (LLM request/cost
  ledger, spec 45), factory_loop_state (autonomous control plane, spec 73).
  Immutability: a candidate's historical record is never mutated; lifecycle
  updates append (mirrors strategy_registry contract).
- ARCHITECTURE LAYER: Application persistence facade (SQLite through
  AuditRepository queue for writes; short-lived read connections; no order
  authority).
- RESPONSIBILITY: 7 write functions (queued) + 9 read functions (bounded,
  JSON-safe).
- DEPENDENCIES: `adapters.database.audit_repository` (private `_db_path` /
  `_is_sqlite` / `_queue`), stdlib (json, sqlite3, datetime).
- CONNECTS TO: orchestrator (persists every stage), worker (loop state),
  provider usage ledger, UI/API reads, summarizer (reads rows).

- KEY CONCEPTS:
  - Writes (all return bool, all queued, all isolated):
    - `upsert_generation` (69-101): ON CONFLICT(generation_id) UPDATE only
      status/completed_at/config — the population shell is preserved.
    - `upsert_candidate` (104-143): full row; ON CONFLICT(candidate_id)
      UPDATE only structural/lifecycle/failure_reasons — the definition
      (dsl column) is IMMUTABLE once inserted.
    - `record_failure` (146-175): append-only (ON CONFLICT DO NOTHING) per
      failure_id with stage + reason + detail JSON.
    - `emit_event` (178-206): append-only event stream.
    - `record_run` (209-237): append-only research-run ledger.
    - `record_provider_usage` (240-273): append-only usage row per
      generation (requests, failures, tokens, estimated_cost_usd,
      latency, last_error).
    - `set_loop_state` (276-306): single-row upsert on scope='autonomous'
      with checkpoint JSON + last_error.
  - Reads (all bounded, all JSON-normalized via `_row_safe` which maps
    `''`/`null`/None → `'{}'` on the 9 JSON-text columns, BUG-075
    discipline, lines 488-510): get_generation, list_generations (number
    DESC), list_candidates (gen/lifecycle filters, population_index ASC),
    list_failures, list_events, list_runs, get_loop_state (default
    {"state": "STOPPED"}), provider_usage_total (COALESCE SUM aggregates).
  - `_conn` (52-61): guarded connect (non-sqlite → None; exceptions
    logged) — every read degrades to []/None/0 rather than raising.
- HOT PATH / PERFORMANCE: writes all queued (never block); reads bounded
  (MAX_READ_LIMIT 2000); per-call connections with 5s timeouts; used on
  the factory worker cycle (asyncio.to_thread) and API paths, never the
  tick path.
- EDGE CASES & PITFALLS:
  - `upsert_candidate`'s immutable-dsl guarantee holds only because the SQL
    never writes dsl on conflict — but candidates whose lifecycle goes
    GENERATED → evaluated re-upsert the SAME dsl text; a definition change
    under the same candidate_id would be silently discarded (new content
    ⇒ new hash ⇒ new id, so this is self-consistent by design).
  - `record_failure` / `emit_event` rely on CALLER-generated unique ids
    (orchestrator uses fail_<uuid>/evt_<uuid>) — a duplicated id is a
    silent no-op (DO NOTHING), so a uuid collision loses the record
    without error.
  - `list_candidates` limit default 200 (bounded) vs complete_generation's
    explicit limit=2000 — a population larger than 2000 would silently
    truncate generation completion summaries.
  - `_row_safe` normalizes by column-name list; any new JSON column added
    to a table without updating this list leaks raw `null` text to UI
    consumers (the BUG-075 class of bug).
  - provider_usage_total does a full-table SUM per call — unbounded scan
    over the usage ledger; fine at current scale, should be windowed for
    long-running deployments.