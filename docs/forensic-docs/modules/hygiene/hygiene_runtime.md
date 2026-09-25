# src/nexus_scalp/hygiene/hygiene_runtime.py

- PURPOSE: RuntimeCleanupScheduler (TASK-22) — continuous runtime database
  hygiene conductor (spec §2, §3, §5): application running → background
  cycle → health → cleanup → report. tick()/run_cycle() called from the
  engine loop; run_cycle executes via asyncio.to_thread — NEVER on the tick
  path. Light cycles: scan → plan → clean(bounded) → consistency → index;
  deep cycles add index health + quarantine review (spec §15 cycle
  telemetry + cooldown-gated Telegram REPORT).
- ARCHITECTURE LAYER: Application (scheduler/conductor).
- RESPONSIBILITY: cadence (light 30min / deep 6h / telegram 1h), first-run
  INITIAL AUDIT (spec §4, persisted DATABASE_HYGIENE_INITIAL_REPORT),
  worker construction policy (mode resolution), quarantine integration
  (MOVE→MARK), cycle telemetry aggregation, status.
- DEPENDENCIES: hygiene worker_runner (MANAGED_DATABASES,
  DatabaseHygieneWorker), consistency, index_health, quarantine, report,
  state; logging (stdlib).
- CONNECTS TO: LiveEngine loop (tick), worker_runner, web diagnostics
  (status), Telegram (text builders consumed by the engine notifier).
- KEY CONCEPTS:
  - Safety (spec §21-23): scheduled cycles NEVER delete unless
    apply_deletes=True AND mode is SAFE_CLEAN AND execution_mode != LIVE.
    Default dry_run=True. _ensure_worker (line 121): apply_deletes =
    settings.apply_deletes AND NOT dry_run AND execution_mode != "LIVE";
    mode = SAFE_CLEAN (apply_deletes, not aggressive) / AGGRESSIVE_CLEAN
    (explicit) / AUDIT_ONLY (default — the scheduler's natural state is
    audit-only).
  - IndexHealthMonitor polling_mode = execution_mode in (PAPER, LIVE) —
    live polling targets skip the unused-index advisory.
  - run_cycle (line 165): first run → _run_initial_audit; worker.
    run_cycle(MANAGED_DATABASES keys); per-db deleted/archived/rows
    scanned aggregated; build_cycle_telemetry with quarantined=0 (the
    scheduler quarantines via quarantine_rows separately); deep cycles
    attach index_health report; timestamps advanced; returns
    {cycle, telemetry, result}.
  - _run_initial_audit (line 231): per managed DB — plan_database (worker)
    + consistency scan + index scan (read-only URI connections); report
    persisted (persist_initial_audit); _audit_done latched (one-shot).
  - quarantine_rows (line 294): row_id resolved by first present of
    id/ticket/article_id/analysis_id/rowid/_rowid; rows without any are
    skipped; MOVE→MARK into QuarantineStore — the SOURCE row is only
    deleted when the CleanupExecutor's own gates approve.
  - Telegram reporting is READ-ONLY here: the scheduler only BUILDS texts
    (telegram_text_for_cycle/initial) and records mark_telegram_sent();
    delivery is the engine's notifier responsibility.
  - status() (line 347): enabled/dry_run/apply_deletes/execution_mode/
    worker_mode/next_light_in/cycle_number/initial_audit_done/quarantine
    stats/worker_state.
- HOT PATH / PERFORMANCE: all destructive steps bounded by worker budgets
  (spec §13/§14); a busy DB defers (busy_timeout), never forces; cadence
  thresholds are time.monotonic — no wall-clock drift.
- EDGE CASES & PITFALLS: `quarantined=0` is hard-coded in cycle telemetry
  even after quarantine_rows ran (the count is never wired in); deep-cycle
  gating uses worker.run_cycle failing → result.error with verification
  FAILED but the scheduler still reports cycle success; _audit_done latch
  means a DB appearing AFTER first run never gets the initial-audit
  treatment; batch_size only applied when ≥10; execution_mode defaults to
  "PAPER" when unspecified — a LIVE runtime must pass execution_mode="LIVE"
  explicitly or the scheduler could run in SAFE_CLEAN mode.