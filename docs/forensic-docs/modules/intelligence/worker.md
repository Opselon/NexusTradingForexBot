# src/nexus_scalp/intelligence/worker.py

- PURPOSE: The PHASE 09 isolated, restart-safe, observable background worker
  for Trade Intelligence — updates position intelligence, produces autopsies,
  detects behavioral patterns, scans for evolution candidates, and rebuilds
  corrupted derived intelligence on demand.
- ARCHITECTURE LAYER: Application (periodic task; invoked via
  `asyncio.to_thread()` from the LiveEngine periodic task, so it never blocks
  the asyncio loop).
- RESPONSIBILITY — the five-point CONTRACT (docstring lines 14-26):
  1. NEVER blocks trading — bounded reads + queued writes only;
  2. FAILURE-ISOLATED — every cycle wrapped; failure logged with the
     `[INTELLIGENCE_WORKER] event=FAILURE` contract; the worker continues next
     cycle; it can never crash the live engine;
  3. RESTART-SAFE — start/stop manage a cycle counter and last-error
     telemetry; a crash mid-cycle resumes cleanly; a checkpoint is recorded so
     nothing is rebuilt redundantly;
  4. IDEMPOTENT — re-running a cycle with no new data is a no-op;
  5. NEVER executes, modifies or closes an order — no adapter, no order
     manager.
- DEPENDENCIES: `audit_repository.AuditRepository`, `experience.ledger`,
  the four engines (lifecycle/autopsy/behavior/evolution), `intelligence.store`
  (load_autopsy), stdlib (sqlite3, time), observability.logging.
- CONNECTS TO: LiveEngine periodic task (tick() via asyncio.to_thread), web
  diagnostics (`format_intelligence_worker_status`), the engine classes it
  drives, and the `intelligence_worker_state` checkpoint table.
- KEY CONCEPTS:
  - State machine: `start()` (idempotent; loads checkpoint), `stop()`
    (idempotent; saves checkpoint). `running` flag + cycle_count +
    last_cycle_duration + last_error observability.
  - Checkpoint (`_load_checkpoint` / `_save_checkpoint`, lines 109-156): reads
    cycle_count and last_checkpoint (stored as `_last_autopsy_count`) from
    `intelligence_worker_state` WHERE scope='intelligence'; a missing
    table/row means "first run" — never an error on the live path. Save goes
    through the async audit queue (upsert on scope). Note the checkpoint
    semantic is the AUTOPSY COUNT, not a timestamp — used only to preserve
    admission telemetry across restarts.
  - `tick` (lines 162-197): wall-clock throttle (`interval_sec` default 30);
    increments cycle; measures duration with perf_counter; any exception →
    last_error set + event=FAILURE logged, returns False (never propagates).
  - `_refresh_once` (lines 203-211): drives the three sub-steps, each
    individually guarded by `_run(name, fn)` so one failure can't abort the
    cycle.
  - `_refresh_autopsies` (lines 224-271): for every strategy family (bounded
    500 records), builds an autopsy for each CLOSED executed record that lacks
    one (`load_autopsy(ticket) is None` skip — idempotent);
    ticket = record.execution_id OR record.idempotency_key (execution_id is
    the broker ticket; the fallback only fires on reconciliation gaps);
    passes record + decomposition + realized numbers + exit_mechanism +
    Phase 08 flags into build_autopsy; new_autopsies accumulate into
    `_last_autopsy_count` (checkpointed).
  - `_refresh_evolution` (lines 272-281): evolution.scan() — bounded
    discovery pass; logs discovered count.
  - `_refresh_behavior` (lines 283-313): analyze_canonical_trades bounded to
    200 most recent closed trades with the CURRENT behavior-v1/anomaly-v1
    versions; skips already-analyzed tickets; logs batch summary.
- HOT PATH / PERFORMANCE: invoked via asyncio.to_thread from the LiveEngine
  periodic task (never the tick hot path); 30s minimum cycle gap; bounded per
  sub-step (500 records/strategy, 200 trades/batch); all DB writes queued;
  queue joined only in the offline behavior batch path.
- EDGE CASES & PITFALLS:
  - The worker does NOT call lifecycle.finalize_exit nor perform any
    position-timeline finalization despite the docstring's "update position
    intelligence (finalize any open timelines)" claim — the lifecycle tracker
    is wired by LiveEngine directly; the worker's lifecycle dependency is
    effectively unused in `_refresh_once` (see findings).
  - `_last_autopsy_count` is a running PER-SESSION counter; the checkpoint
    stores it but `_load_checkpoint` only takes the MAX of prior values —
    restarting preserves the maximum seen, never zeroes admission telemetry.
  - `format_intelligence_worker_status` reports autopsy_count from the
    private `_last_autopsy_count` — a session counter, not the persisted table
    count; the dashboard should treat it as "autopsies produced by this
    worker since (last) start", not total.
  - `tick()` returns False both when throttled and when a cycle failed —
    callers cannot distinguish "skip" from "failure" without reading
    last_error; documented behavior.