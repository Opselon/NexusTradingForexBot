# src/nexus_scalp/model_lifecycle/worker.py

- **PURPOSE:** Isolated, restart-safe, observable background worker for
  CONTROLLED model training (spec 25/31/32/42). Six-part contract: (1) NEVER
  blocks trading — invoked through `asyncio.to_thread()` from the LiveEngine
  periodic task, never inside `_process_tick_pipeline()`; (2) failure-isolated —
  every cycle wrapped, failure logs `[TRAINING_WORKER] event=FAILURE` and the
  loop continues; (3) restart-safe — a crash mid-training leaves the run
  FAILED/INCOMPLETE, never VALIDATED; (4) bounded — at most
  `max_concurrent_trainings` active (default 1), refuses to stack trainings;
  (5) cancellable — `request_cancel()` marks stop, in-flight run abandoned as
  INCOMPLETE; (6) no execution capability — no adapter/order manager/risk engine.
- **ARCHITECTURE LAYER:** Research/ML background automation; no order authority.
- **RESPONSIBILITY:** Rate-limited training cycles (interval_sec default 300 s),
  telemetry for the REST layer, restart healing.
- **DEPENDENCIES:** sqlite3 (direct RUNNING-row restore), audit_repository,
  ledger, ModelLifecycleOrchestrator, logger.
- **CONNECTS TO:** LiveEngine periodic task (tick()), web status API
  (format_training_worker_status), orchestrator.

- **KEY CONCEPTS:**
  - `start()` (line 80): idempotent; resets `_last_run_ts`; calls
    `_restore_inflight_state()`.
  - `_restore_inflight_state` (line 107): on restart, finds any
    `status='RUNNING'` row in training_runs and marks it INCOMPLETE — the
    never-VALIDATED guarantee across crashes. Wrapped in try/except (debug log on
    failure) — restart healing is best-effort.
  - `tick()` (line 134): no-op unless running, no-op while `inflight` (bounded
    concurrency), rate-limited by `interval_sec`; measures `last_cycle_duration`;
    exceptions logged as `event=FAILURE` and swallowed (failure isolation).
  - `_maybe_train` (line 171): respects `auto_train_enabled` AND `_cancel_requested`;
    requires ≥ 50 ledger experiences AND ≥ 50 labeled samples; builds dataset
    (include_no_trade=True, weight 0.25, only_executed=True) and runs ONE
    controlled training with the SMOKE hyperparameters
    `{num_folds:5, epochs_per_fold:3, batch_size:64}`, num_epochs=3,
    build_identity="training_worker"; records last_run_id; `inflight` cleared and
    `_cancel_requested` reset in a finally block.
  - `request_cancel()` (line 98): sets `_cancel_requested`; the NEXT cycle
    refuses to start; an in-flight run is not killed mid-step (the trainer keeps
    running to completion/failure — cancellation is cooperative at cycle
    boundaries).
  - `format_training_worker_status` (line 217): TRUTHFUL STATE (spec 37):
    IDLE (not running) / DISABLED (running but auto_train off — NEVER claims
    RUNNING) / TRAINING (inflight) / RUNNING (loop alive, eligible). Exposes
    cycle_count, interval, last_run_id, last_error, last_cycle_duration_ms.

- **HOT PATH / PERFORMANCE:** The worker's own tick is cheap; the torch training
  bulk is invoked via to_thread at the LiveEngine level so the event loop is
  never blocked. Bounded: one training at a time + 300 s min gap.

- **EDGE CASES & PITFALLS:**
  - `_cancel_requested` is reset ONLY inside `_maybe_train`'s finally — if
    auto_train is disabled, `_maybe_train` returns BEFORE setting inflight and
    the flag persists until a later enabled cycle consumes it (request_cancel
    semantics degrade to "cancel next training attempt", which is the intent).
  - Stop with in-flight training: `stop()` logs abandonment but does NOT mark
    the run INCOMPLETE here — that only happens at the next `start()` via
    `_restore_inflight_state`; until then the DB shows RUNNING for an abandoned
    run (REST clients may report TRAINING while the worker reports IDLE).
  - `max_concurrent_trainings` accepts >1 but nothing ever exceeds 1 (inflight
    is a single bool) — the parameter is vestigial.
  - The smoke hyperparameters produce low-quality candidates; combined with the
    orchestrator's gate-omission behavior an under-trained candidate can still
    reach CHALLENGER status if its 7 wired gates pass.