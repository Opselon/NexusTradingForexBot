# src/nexus_scalp/shadow/worker.py

- PURPOSE: Isolated, restart-safe, cancellable background shadow worker
  (PHASE 11 spec 16/17/33). Periodically FINALISES the active shadow run
  into a persisted comparison — per-tick decision recording stays on the
  live path via ShadowEngine; heavy aggregation NEVER runs inside
  _process_tick_pipeline().
- ARCHITECTURE LAYER: Application (background task).
- RESPONSIBILITY: ShadowWorker (start/stop/tick/_maybe_finalize/
  request_cancel/_mark_interrupted_runs), format_shadow_worker_status.
- DEPENDENCIES: adapters.database.audit_repository, shadow.engine,
  sqlite3, time, logging.
- CONNECTS TO: LiveEngine periodic task (invoked via asyncio.to_thread
  per contract), ShadowEngine.finish_run, UI status endpoint.
- KEY CONCEPTS — THE 5 CONTRACT POINTS:
  1. NEVER blocks trading — tick() is called off the tick pipeline; a
     cycle is interval-gated (default 300s) by _last_run_ts.
  2. FAILURE-ISOLATED — _maybe_finalize wrapped; any exception →
     [SHADOW_WORKER] event=FAILURE + last_error; the worker continues;
     Champion unaffected.
  3. RESTART-SAFE — start() calls _mark_interrupted_runs(): any RUNNING
     shadow run row becomes INCOMPLETE (crash mid-cycle resumes cleanly;
     a half-finished run is never presented as COMPLETED).
  4. CANCELLABLE — request_cancel() sets the flag; the next _maybe_finalize
     clears it and returns without finalizing.
  5. BOUNDED CPU/memory — aggregation runs on the bounded in-memory
     _decisions list.
  - tick(): rate-limits cycles (interval), increments cycle_count,
    records last_cycle_start/duration; returns True/False for
    success/failure telemetry.
  - _maybe_finalize: finalizes when engine.active_run_id AND
    len(engine._decisions) >= finalize_after_decisions (default 30).
- HOT PATH / PERFORMANCE: tick() is called periodically (not per-tick);
  _mark_interrupted_runs does one bounded UPDATE at start only.
- EDGE CASES & PITFALLS:
  - _mark_interrupted_runs updates only the FIRST RUNNING run found
    (LIMIT 1) — a second RUNNING row from a double-start stays RUNNING.
  - tick() uses time.time() monotonic-ish wall clock — a system clock
    jump backwards delays the next cycle; fine.
  - finish_run inside _maybe_finalize may raise (comparer aggregation
    errors) → caught by tick()'s handler; the worker keeps going.
  - request_cancel only defers the NEXT finalize; an already-running
    _maybe_finalize is not interrupted.
  - format_shadow_worker_status reads engine._decisions directly (private
    attribute contract).