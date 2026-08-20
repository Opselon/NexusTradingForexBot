# src/nexus_scalp/adapters/database/audit_repository.py

- **PURPOSE:** The SQLite WAL audit & ledger repository — 2,489 lines of
  table definitions, the async write queue (background worker thread), and
  every persistence method the engine/accounting/research/intelligence
  layers call. This is the system's memory substrate.
- **ARCHITECTURE LAYER:** Adapters (persistence). Writes are QUEUED —
  the tick path NEVER blocks on DB I/O (INV-001).
- **RESPONSIBILITY:** (a) `_create_sqlite_tables` — the audit tables
  (audit_signals with BUG-054 dedup via deterministic signal_dedup_key +
  UNIQUE index + ON CONFLICT DO NOTHING — restart-safe, no SELECT-then-
  INSERT on hot path; audit_guard_telemetry — composite-PK counter table
  ~40B/event; audit_orders; audit_account_snapshots throttled writes);
  (b) experience tables (audit_experiences immutable rows keyed by
  idempotency_key, execution_id EMPTY BY DESIGN; audit_experience_outcomes
  carrying the broker ticket — the trade→strategy identity bridge);
  (c) intelligence tables (lifecycle/autopsy/behavior/evolution);
  (d) research tables + research observability tables (runs/snapshots/
  gates/events/evidence); (e) the write queue: `_start_background_worker`
  / `_process_queue_worker` — a queue.Queue + worker thread processing
  writes sequentially; (f) dedup helpers: `_signal_dedup_key` (sha256 of
  symbol|M1-bucket|model_action|decision_stage|execution_mode|reason_code);
  (g) log_signal/log_order/log_execution/guard telemetry; (h) sync_broker_
  history + broker trades/deals/orders readers (reconstruction pipeline).
- **DEPENDENCIES:** sqlite3 (WAL), queue/threading, domain models, enums,
  structlog logging.
- **CONNECTS TO:** EVERYTHING — engine, accounting, experience,
  intelligence, research, web endpoints, hygiene worker, tests.
- **KEY CONCEPTS:**
  - The worker-thread queue is THE hot-path protection: producers enqueue
    (non-blocking), the worker drains; `_queue.join()` is the test
    synchronization primitive (flush before asserting reads);
    `close()` nulls the queue (post-close guard, BUG-058) — do NOT call
    close() mid-test to flush.
  - Retention (BUG-054): `purge_old_audit_data` bounded 500-row batches,
    WAL mode, NEVER touches ledger/experiences/autopsies/research.
  - Lazy-schema shadow tables: ensure_schema on first save — absence in a
    live DB means no run attached, not a bug.
  - `net_result`-style monetary truth lives in the ledger; the repo only
    stores what producers pass (it never computes PnL itself).
- **HOT PATH / PERFORMANCE:** Producers never wait on I/O; the queue is
  thread-safe and bounded; purge measured 16-31ms with 0 contention
  (audit 2026-08-18); indexes: audit_orders lacks (ticket, order_id);
  audit_ledger ORDER BY close_time defeats indexes (USE TEMP B-TREE) —
  P3 debt (see issues ledger).
- **EDGE CASES & PITFALLS:** composite-PK guard telemetry needs rowid-based
  deletion (window_start, symbol, reason_code — not id); executor rows
  need row_factory=sqlite3.Row (dict(row) on a tuple raises); writes from
  the worker are serialized (no concurrent writers — WAL safely permits
  concurrent readers).