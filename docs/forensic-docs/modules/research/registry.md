# src/nexus_scalp/research/registry.py

- PURPOSE: PHASE 09B Strategy Registry persistence (spec 20/26/40) — the
  enduring home of validation truth: identity, version, feature schema,
  discovery source, validation lineage, backtest/walk-forward/OOS/robustness
  results, score, confidence, lifecycle, timestamps, retirement reason.
  INDEPENDENT of the current model file (spec 24); historical validation
  truth is never mutated (spec 28). Rows are written through the
  AuditRepository background queue so the registry never blocks the live
  path.
- ARCHITECTURE LAYER: Application/Research persistence (SQLite read+queued
  write; no order authority).
- RESPONSIBILITY: upsert (immutability-guarded), get/list/count queries,
  lifecycle transitions (persisted, state-machine enforced), and the
  VALIDATED/REJECTED invariant checker (TASK-21, spec 55/56/57).
- DEPENDENCIES: `adapters.database.audit_repository` (private `_db_path` /
  `_is_sqlite` / `_queue`), `research.models`, `research.lifecycle`
  (transition), observability.logging, stdlib (json, sqlite3).
- CONNECTS TO: pipeline._register (writes entries + runs), seeder
  (builtin candidates), store (read facade), observability (trace/
  family_analytics), web API.

- KEY CONCEPTS:
  - `UPSERT_ENTRY_SQL` (lines 41-67): ON CONFLICT(strategy_id,
    strategy_version) DO UPDATE of every column except created_at.
  - `upsert` (lines 80-146): TWO immutability guards BEFORE the write —
    (a) if an existing row's context_definition differs from the new entry,
    the upsert is REFUSED (returns False, logs "definition mutation
    refused") — a version's historical definition can never be rewritten;
    (b) `forbid_lifecycle_regression=True` refuses replacing a stronger
    lifecycle (VALIDATED/SHADOW/ACTIVE/REJECTED/DEGRADED/RETIRED) with a
    weaker one via `_is_stronger` (lines 384-404). Write goes through
    `audit_repo._queue.put_nowait` (async, isolated).
  - `get` (lines 152-178): by (id, version) or newest-by-updated_at; row
    decode via `_from_row` which json-loads result columns through `_load`
    — tolerant of `null` literals and `{}` (BUG-075 canonical empty),
    never raising on malformed rows.
  - `list` / `count` (lines 180-224): bounded (limit clamped to 500),
    lifecycle filter, newest first; failures → []/0, never raise.
  - `invariant_check` (lines 230-280): VALIDATED requires backtest +
    walkforward + oos + robustness ALL present, oos/robustness status PASS,
    walkforward.passed True, score present with verdict VALIDATED — no
    shortcuts. REJECTED requires a failed gate OR a validation attempt (a
    freshly-seeded DISCOVERED row marked REJECTED is flagged as invalid).
  - `transition_lifecycle` (lines 286-312): load → state-machine transition
    (LifecycleError caught) → model_copy with new state + append-only
    validation_lineage timestamp snapshot → upsert.
  - Helpers: `_json` (lines 407-423) — None must round-trip to `'{}'`,
    never `"null"` (BUG-075 crash fix, spec 24); `_parse_ts` tolerant ISO
    parsing.
- HOT PATH / PERFORMANCE: all writes queued (never block); reads bounded
  5s-timeout connections; used on worker/API paths, never per tick.
- EDGE CASES & PITFALLS:
  - The definition-mutation guard keys on context_definition only — a
    mutated entry_logic/exit_logic/risk under the SAME version updates the
    row silently (the version hash would normally change, but a caller that
    fabricates a version string can bypass); old vs new comparison uses
    raw dict equality, so key reordering in JSON also trips the guard.
  - `upsert` returns True as soon as the queue accepted the write — a
    broken SQL later in the background queue surfaces as a silent missing
    row; readers must tolerate absence.
  - `transition_lifecycle` does NOT pass forbid_lifecycle_regression when
    persisting — the state machine adjacency is the only guard; a DEGRADED
    → DISCOVERED is legal per the graph (DEGRADED has no DISCOVERED target,
    so it's blocked in practice), but VALIDATED → OOS_TESTING is also not
    adjacent (blocked).
  - `_is_stronger` treats DEGRADED/REJECTED/RETIRED as strength 2 — a
    REJECTED row can't be re-seeded to DISCOVERED with the regression flag
    on (intended: validation truth is preserved).