# src/nexus_scalp/shadow/store.py

- PURPOSE: Append-oriented, auditable shadow persistence (PHASE 11 spec
  20/24/25). Tables shadow_runs / shadow_decisions / shadow_comparisons /
  shadow_promotions. Historical shadow results are NEVER overwritten;
  model rebuilds and feature-schema evolution do NOT erase shadow
  history (spec 25): every row preserves model version + schema identity.
  Writes go through the AuditRepository background queue — the live path
  is never blocked.
- ARCHITECTURE LAYER: Infrastructure (persistence on audit.db).
- RESPONSIBILITY: ShadowStore — ensure_schema (once per process),
  save_run/save_decision/save_comparison/save_promotion, reads
  (get_run/list_runs/list_decisions/get_comparison/get_promotion/
  list_promotions/summary).
- DEPENDENCIES: adapters.database.audit_repository, shadow.models,
  sqlite3, json, logging.
- CONNECTS TO: ShadowEngine (writes), ShadowWorker, UI/API (reads),
  forensics checks (reads shadow_* tables).
- KEY CONCEPTS:
  - ensure_schema is guarded by the `_schema_ensured` flag — DDL runs
    at most once per process because record_shadow_decision →
    save_decision calls it EVERY TICK; a per-tick CREATE would be
    synchronous SQLite I/O on the hot path (documented design).
  - INSERT OR REPLACE keyed by shadow_decision_id / run_id (idempotency
    at the row level); simulated always stored as 1 from the record;
    full record payload JSON (model_dump) into the payload column —
    lossless denormalization for forensic replay.
  - save_comparison flattens by_regime/by_strategy JSON + list columns;
    save_promotion stores score/eligible/vetoes/reasons + payload.
  - Reads: bounded limits (list_runs ≤ 500, list_decisions ≤
    MAX_READ_LIMIT=3000, list_promotions ≤ 500), short-lived ro conns
    (plain connect — not mode=ro URI here, unlike governance.store).
  - summary(): runs grouped by status + decisions + promotions counts.
- HOT PATH / PERFORMANCE: save_decision is the live-path write — a
  single queue.put_nowait after ensure_schema flag check; cheap.
- EDGE CASES & PITFALLS:
  - Reads use regular sqlite3.connect (no ?mode=ro) — a read while the
    writer queue flushes can contend briefly (timeout=5.0); acceptable
    for API cadence.
  - Empty-table columns: save_comparison assumes columns exist — if a
    pre-existing shadow_comparisons table from an older schema lacks a
    column (e.g. degraded_regimes), the INSERT raises and the write is
    logged, not auto-migrated.
  - get_run/get_comparison/get_promotion return dict(row) — JSON columns
    stay as TEXT strings (consumers must json.loads themselves);
    save_decision stores `decision.model_dump(mode="json")` — pydantic
    datetime → ISO strings (fine).
  - shadow_comparisons.run_id is UNIQUE — a second finish_run for the
    same run_id silently REPLACES the aggregate (INSERT OR REPLACE),
    which argues for run_id uniqueness discipline in the engine.