# src/nexus_scalp/research/worker.py

- PURPOSE: PHASE 09B isolated, restart-safe, observable background worker for
  Strategy Research / Validation (spec 31/32/42): rebuild dataset from the
  immutable ledger → bounded discovery → validate new candidates through the
  full gate chain.
- ARCHITECTURE LAYER: Application (periodic task; invoked via
  asyncio.to_thread from the LiveEngine periodic task so it never blocks the
  asyncio loop).
- RESPONSIBILITY — five-point CONTRACT (docstring lines 14-24):
  1. NEVER blocks trading (bounded reads + queued writes);
  2. FAILURE-ISOLATED (`[RESEARCH_WORKER] event=FAILURE`, continues next
     cycle; can never crash the live engine);
  3. RESTART-SAFE (start/stop manage cycle counter + persisted checkpoint
     research_worker_state);
  4. IDEMPOTENT (no new data ⇒ no-op cycle);
  5. NEVER executes/modifies/closes an order (no adapter/order manager/risk
     engine).
- DEPENDENCIES: AuditRepository, ExperienceLedger, ResearchPipeline,
  observability.logging; lazily the seeder + ResearchObservabilityStore.
- CONNECTS TO: LiveEngine periodic task, /api/research/worker health
  endpoint (format_research_worker_status), research tables.

- KEY CONCEPTS:
  - Lifecycle: start() idempotent (loads checkpoint), stop() idempotent
    (saves checkpoint). Checkpoint (lines 118-157): reads cycle_count +
    last_checkpoint (validated_count) from research_worker_state WHERE
    scope='research'; save via the audit queue (upsert on scope); a missing
    table/row = "first run", never an error.
  - tick (lines 163-206): wall-clock throttle (interval_sec default 60);
    increments cycle, measures perf duration, emits heartbeat; any exception
    → last_error + FAILURE log + heartbeat FAILED; returns False both when
    throttled and when failed (callers must read last_error to distinguish).
  - `_refresh_once` (lines 212-227): seed → dataset → (if dataset changed)
    discovery → validation; else DATASET_UNCHANGED no-op. Each step isolated
    by `_run` (lines 260-270). Returns work_done.
  - `_dataset_changed` (lines 229-234): content-addressed rebuild guard
    (spec 23): compares dataset_id to the last seen; an unchanged ledger ⇒
    same id ⇒ discovery/validation skipped (no forced rebuilds).
  - `_refresh_seed` (lines 236-258): seeds built-in strategies via
    strategies.seeder.seed_builtin_candidates (safe to run every cycle —
    upsert preserves existing validation results); counts as real work only
    on the FIRST cycle (work_done false thereafter).
  - `_refresh_dataset` (lines 272-288): pipeline.dataset_builder.build();
    logs DATASET_REBUILT when id changed.
  - `_refresh_discovery` (290-304): builds dataset if missing, discovers,
    stores `_candidates`.
  - `_refresh_validation` (306-331): bounded to
    MAX_VALIDATIONS_PER_CYCLE=5; each candidate through
    pipeline.validate_candidate; `_validated_count` increments (checkpointed
    via save).
  - `_emit_heartbeat` (334-366): best-effort beat via ResearchObservability
    Store (never raises) — feeds the /api/research/worker health classifier
    (HEALTHY/DEGRADED/STUCK/FAILED).
- HOT PATH / PERFORMANCE: 60s minimum cycle gap; bounded reads (10000/
  strategy, 100000 outcome rows); all writes queued; runs off the tick path.
- EDGE CASES & PITFALLS:
  - `_refresh_validation` clears `_candidates` even when validation was
    interrupted mid-loop — candidates beyond the first 5 are dropped by the
    in-memory list but remain DISCOVERED in the registry; next cycle with an
    unchanged dataset SKIPS discovery (rebuild guard), so those candidates
    are never validated until the ledger changes again.
  - `_dataset_changed` returns True when `_dataset` is None... actually it
    returns True on None (line 233), but `_refresh_once` calls
    `_refresh_dataset` FIRST when `_dataset` is stale, so the guard only
    acts after a dataset was built in-session.
  - `_seeded_once` gates work_done only; the seeding itself (queue write)
    happens every cycle — cheap but not literally skip-on-no-change.
  - heartbeat `_queued_jobs`/`_failed_jobs`/`_current_strategy` read from
    attributes that are never set (getattr defaults 0/"") — always zero in
    this implementation.
  - `format_research_worker_status.validated_count` reports the session/
    checkpoint counter (maximum seen), not a registry count — dashboard
    semantics: "validations performed by this worker since last reset".