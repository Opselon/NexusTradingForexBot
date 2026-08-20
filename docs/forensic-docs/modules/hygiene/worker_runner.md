# src/nexus_scalp/hygiene/worker_runner.py

- PURPOSE: DatabaseHygieneWorker (TASK-11) — background orchestrator of
  OBSERVE → CLASSIFY → PLAN → VALIDATE → CLEAN → VERIFY. Production
  posture: default first-run mode AUDIT_ONLY (spec §2 — never delete on
  debut); operator switches to SAFE_CLEAN explicitly; AGGRESSIVE_CLEAN is a
  separate explicit activation; every cycle builds plan → applies
  (bounded) → verifies → persists run; busy DB → DEFER (never force);
  LIVE trading mode → conservative (cache/temp/retention only); never on
  the tick hot path (spec §19, via asyncio.to_thread / scheduled task).
- ARCHITECTURE LAYER: Application (background orchestrator).
- RESPONSIBILITY: MANAGED_DATABASES map (audit → artifacts/audit.db,
  news → artifacts/news.db, candle_intel → artifacts/candle_intel.db),
  cycle orchestration (plan/apply/verify per DB), run history recording,
  crash recovery marker, db_integrity_digest (spec §64 before==after).
- DEPENDENCIES: hygiene package (WorkerMode/WorkerState), archive.
  read_only_connect, state.HygieneStateStore, worker components
  (CleanupExecutor/HygienePlanner/HygieneScanner/financial_aggregates),
  hashlib/os/sqlite3/time.
- CONNECTS TO: hygiene_runtime (run_cycle driver), engine startup (mode
  policy), CLI, tests.
- KEY CONCEPTS:
  - run_cycle (line 137): PAUSED state → early error return; IN_PROGRESS
    run row recorded BEFORE work (crash → INTERRUPTED at next startup via
    recover_interrupted, spec §66 — never blindly resumed); per DB:
    busy check (2s timeout + busy_timeout 2000, failure → BUSY_DEFERRED),
    read-only plan, executor.apply_plan with apply_deletes gated on
    self.apply_deletes AND mode SAFE/AGGRESSIVE; per-db run rows recorded
    (bytes_freed hard-coded 0).
  - Verification: overall verification = PASS only when EVERY db result is
    PASS/SKIPPED_DRY_RUN/NOT_RUN — otherwise CHECK (not FAILED; FAILED is
    reserved for the top-level exception path). last_cleanup/last_success
    only set when verification == PASS.
  - status() (line 91): persisted state + execution_mode + mode +
    apply_deletes + managed dbs + db sizes (main + -wal).
  - plan_database (line 120): public read-only planning for one DB
    (UNKNOWN_DATABASE / DB_NOT_FOUND errors surface as dicts).
  - db_integrity_digest (line 262): sha256 over every table's row count +
    financial aggregates — the spec §64 BEFORE == AFTER proof primitive
    (protected tables must be provably untouched).
- HOT PATH / PERFORMANCE: off-loop by design; per-db connections are
  short-lived; full-table COUNT(*) scans inside digest/scan only during
  cycles; busy defer prevents hammering the live engine's WAL.
- EDGE CASES & PITFALLS: run_cycle early-returns {error: PAUSED} WITHOUT
  recording a run row; overall verification CHECK vs FAILED semantics —
  executor errors under a db key with verification "PASS" can still yield
  PASS overall if other dbs pass (a single db with errors and
  verification PASS + errors list will pass the all() — errors don't fail
  verification unless the executor set verification FAILED); bytes_freed
  always 0 (never computed post-delete); the initial IN_PROGRESS row for
  the multi-db run (database "a+b+c") is REPLACED by per-db rows with the
  same run_id (INSERT OR REPLACE) — the aggregate row disappears from
  history; recover_interrupted runs AFTER the cycle, so a crash mid-cycle
  leaves IN_PROGRESS rows to be marked at the NEXT run.