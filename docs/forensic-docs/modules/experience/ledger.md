# src/nexus_scalp/experience/ledger.py

- PURPOSE: Append-only persistence for the experience memory layer — three
  tables owned by the existing AuditRepository (no second persistence system):
  `audit_experiences` (immutable decision rows, UNIQUE(idempotency_key)),
  `audit_experience_outcomes` (append-only outcomes, UNIQUE(idempotency_key)),
  `audit_experience_corrections` (additive correction events).
- ARCHITECTURE LAYER: Application / adapter boundary — wraps `AuditRepository`
  (adapters.database) write queue and read connections.
- RESPONSIBILITY: Enforce the five CRITICAL INVARIANTS (docstring lines 13-27):
  (1) decision row written exactly once via `ON CONFLICT DO NOTHING` and NEVER
  updated; (2) retrieval merges decision + outcome server-side into a typed
  `ExperienceRecord` — the fixed version of BUG-008 where scalar-column updates
  plus frozen payload reads silently produced zero closed samples; (3) retrieval
  is always bounded (LIMIT) and causally filtered when a decision time is
  given; (4) raw rows are never deleted, corrections are additive; (5) the
  ledger never touches the model artifact and never imports torch.
- DEPENDENCIES: `audit_repository.AuditRepository` (private `_queue`,
  `_db_path`, `_is_sqlite`), `experience.models`, stdlib (hashlib, json,
  sqlite3, uuid), observability.logging.
- CONNECTS TO: `intelligence.py` (record_experience/record_outcome), the
  evaluator (get_experiences_for_strategy), retriever (bounded causal reads),
  outcome_recovery (correlation lookups), outcome_repair (repair_outcome),
  behavior.py (count_recent_entries, list_strategy_ids) and accounting
  forensics via get_experiences_by_order_id / owner_of_execution.
- KEY CONCEPTS:
  - Write path (all queued through `audit_repo._queue.put_nowait` — the live
    tick path never performs disk I/O):
    - `record_experience` (lines 143-200): queues one immutable decision row;
      dedup is enforced by the UNIQUE constraint — a replayed event is a
      database-level no-op, never a second learning sample. Returns False when
      not SQLite or queue push failed.
    - `record_outcome` (lines 202-255): queues one append-only outcome row;
      `ON CONFLICT DO NOTHING` makes duplicate close callbacks harmless — PnL
      can never be double-counted.
    - `repair_outcome` (lines 257-313): BUG-046 repair path — UPDATEs ONLY the
      derived outcome layer (never the immutable decision row) with
      broker-reconstructed truth; idempotent by key; payload carries repair
      provenance. The only UPDATE statement in the module and it is scoped to
      audit_experience_outcomes.
    - `record_correction` / `build_correction` (lines 315-367): additive
      correction events (`corr_<uuid12>`), fields old_value/new_value.
  - Read path (short-lived read-only sqlite connections, 5-10s timeouts):
    - `_merge_row` (lines 378-413): rebuilds a typed ExperienceRecord from the
      LEFT-JOINed decision+outcome payloads; legacy revision-1 payloads migrate
      via the model validator; a causally-invalid outcome (predates its
      decision) is DEFENSIVELY rejected as evidence (logged, row returned
      without outcome).
    - `_query_records` (lines 415-437): bounds limit to [1, MAX_RETRIEVAL_LIMIT
      =2000] and orders by decision_timestamp DESC.
    - `get_experiences_for_strategy` / `get_experiences_for_symbol` (lines
      439-473): bounded, causally filtered (`decision_timestamp <
      before_timestamp`, strict) — future outcomes can never influence a past
      decision. Default limits 500 / 200.
    - `get_experience_by_key` (line 475): single-key merged read.
    - `get_experiences_by_order_id` (lines 480-499): Phase 14 POSITION_STATE
      correlation fallback — matches request_id in ANY of request_id,
      decision_id, execution_id, experience_id. Never fabricates an identity:
      the caller logs which fallback matched.
    - `owner_of_execution` (lines 501-531): ANOMALY-VERIFY-01 economic-identity
      guard — returns the FIRST closed outcome's key owning a broker ticket
      (excluding the caller's key), so the gate can reject split-fill /
      sibling-ticket duplicate outcomes (BUG-081 pattern) instead of
      double-counting one economic position.
    - `has_outcome` (lines 533-549), `count_recent_entries_for_strategy`
      (lines 551-584, REENTRY_OVERTRADING measurement — window bounded by
      `window_seconds`, count of DECISION rows, not outcomes),
      `list_strategy_ids` (limit 5000), `count_experiences`,
      `get_schema_distribution` (census proving historical schemas preserved).
  - Deterministic identity helpers (lines 650-686):
    - `compute_feature_hash`: sha256[:16] over
      `"{schema_id}:{len}:{values formatted %.6f}"` — schema id and length are
      folded in so an identical numeric prefix under a different schema cannot
      collide.
    - `generate_strategy_id`: `strat_<sha256[:12]>` over bounded context tokens
      only (symbol|timeframe|session|regime|volatility|trend|setup|confluence|
      parameter_hash).
- HOT PATH / PERFORMANCE: zero sync SQLite on the tick path (all writes
  queued). Read paths open one connection per call with bounded timeouts;
  per-tick reads are only the gate's TTL-cached score lookups. `_merge_row`
  does a full Pydantic re-validation per row — bounded by retrieval limits.
- EDGE CASES & PITFALLS:
  - Direct use of AuditRepository private members (`_queue`, `_db_path`,
    `_is_sqlite`) is deliberate (documented in module docstring) but couples
    the ledger to the queue being alive; `close()` on the repo nulls `_queue`
    (BUG-058 pattern) — callers must not flush via close().
  - `get_experiences_by_order_id` ORs four columns without index support inside
    the merged query; used only on the restart/reconciliation fallback path.
  - `record_experience` stores `execution_id` (default "") from the record —
    the decision row's execution_id stays EMPTY BY DESIGN (audit_experiences
    rarely carries the broker ticket; the outcome row is the ticket bridge).
  - `repair_outcome` does NOT update `audit_experiences` — a repair therefore
    fixes evaluation counts (which read outcomes) but leaves the legacy
    projection columns in the decision row stale; consumers must read merged
    records, not decision-row scalars.