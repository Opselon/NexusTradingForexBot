# 70D Worker Flow Forensics (TASK-70D-SYSTEM-FLOW-FORENSICS)

> Agent: Hermes-Forensic-70D · 2026-08-19
> Reconstructed from actual code at absorbed HEAD (2babe15).

## 1. Worker inventory (active, constructed in LiveEngine.__init__)

| # | Worker | Entrypoint | Start condition | Interval | Queue | Input | Output | DB | Checkpoint | Retry | Shutdown | Health |
| :-: | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | AccountingWorker | `accounting/worker.py::tick()` | `_accounting_worker_started` | loop-throttled | none | ledger/snapshots | derived period cache | audit.db (read) | none (rebuildable) | cycle-isolated | stop() | `[ACCOUNTING_WORKER] event=START/UPDATE/FAILURE` |
| 2 | BrokerHistorySyncWorker | `adapters/database/broker_history_sync.py::tick()` | `_history_sync_started` | interval_sec | none | broker orders/deals | broker_history tables | audit.db | broker_history_meta | next-cycle (interval) | stop() | cycle_count/last_error/real result |
| 3 | IntelligenceWorker | `intelligence/worker.py::tick()` | `_intelligence_worker_started` | 30s | none | ledger | behavior/autopsy/anomaly | audit.db | intelligence_worker_state | cycle-isolated | stop() | `[INTELLIGENCE_WORKER] event=FAILURE` |
| 4 | ResearchWorker | `research/worker.py::tick()` | `_research_worker_started` | 60s | none | experiences | registry/backtest/OOS | audit.db | research_worker_state | cycle-isolated | stop() | cycle_count + persisted state |
| 5 | TrainingWorker | `model_lifecycle/worker.py::tick()` | `_training_worker_started` | throttled | none | training config | candidate artifacts | audit.db | training state | bounded | stop/cancel | DISABLED default (INV-016) |
| 6 | ShadowWorker (50D) | `shadow/worker.py::tick()` | `_shadow_worker_started` | throttled | none | shadow decisions | comparisons | audit.db | shadow_runs | cycle-isolated | stop() | run status |
| 7 | NewsWorker | `news/worker.py::tick()` | `_news_enabled && _news_worker_started` | 60s | bounded priority | feeds/analysis | news DB + context | news.db | news_worker_state | <=3 bounded | stop() | health + worker state |
| 8 | DatabaseHygieneWorker | `hygiene/worker_runner.py` | 6h throttle | 6h | none | DB scans | plans/cleanups | all | hygiene state | BUSY→DEFER | pause | AUDIT_ONLY default |
| 9 | Shadow70Worker | `shadow/shadow70/worker.py` | `_shadow70_worker_started` | flush 5s/batch 100 | bounded 2000 | observations | shadow70_* tables | audit.db | none (INSERT OR IGNORE) | drop/backpressure | stop(flush) | enqueued/persisted/dropped |

All workers are kicked via `asyncio.to_thread(worker.tick)` from
`LiveEngine.run_loop` — NEVER inside `_process_tick_pipeline` (INV-001).

## 2. Worker state machine

Workers use start/stop + throttle + cycle_count + last_error. None of them
rely on a bare "RUNNING" label as health:
- `tick()` returns False when interval not elapsed or not started.
- Every cycle increments `cycle_count` and records `last_cycle_duration` /
  `last_error` / real result payloads.
- Checkpoints persist to `*_worker_state` tables (research/intelligence/news)
  and are restored on `start()` (restart-safe).

## 3. "RUNNING but doing nothing" — the BUG-105 class

The 70D shadow hook was the exact case study: `_shadow70_enabled=True` +
runtime READY, yet `shadow70_observations` stayed empty in the live DB
because the observation code was nested inside the 50D-shadow `except` block
(dead on the happy path) + the `build_70d_vector` conditional-import
UnboundLocalError. Fixed by extracting `_record_shadow70_observation()` as an
independent per-tick method (regression TEST-SHADOW-36..39).

## 4. Worker data flow (no dropped/duplicated items)

- Shadow70Worker: bounded queue (max 2000), `put_nowait`, drop telemetry
  (SHADOW_BACKPRESSURE), batch flush (100) every 5s, `INSERT OR IGNORE` on
  deterministic observation_id → duplicate persistence impossible.
- NewsWorker: bounded priority queue, dedup at ingest, retries <=3,
  checkpoint/restart-safe.
- ResearchWorker: seed builtin candidates each cycle BEFORE dataset/
  discovery (idempotent upserts preserve existing results).

## 5. Worker restart / failure behavior

- Workers persist checkpoints on stop and restore on start (research/
  intelligence/news) — no duplicate work, no lost checkpoint.
- Failures are cycle-isolated: `tick()` wraps each cycle; a failure logs
  `event=FAILURE` and the worker continues next cycle.
- Shadow70Store: lazy schema ensure (once per process), never on the tick
  path; backpressure policy drop/coalesce.

## 6. Verification status

| Check | Result |
| :--- | :--- |
| Worker inventory matches code | 🟢 (9 workers, all verified in live_engine.py) |
| State machine meaningful | 🟢 (start/stop/throttle/cycle_count/last_error) |
| No-progress detection | 🟢 (BUG-105 found + fixed; all workers have counters) |
| Restart recovery | 🟢 (checkpoint tables + tests) |
| Failure isolation | 🟢 (tick() try/except per cycle + asyncio.to_thread) |
| No silent success | 🟢 (workers record real results/errors) |