# PERF-DEADLETTER / PERF-HEALTH / PERF-EXEC-TRACE — performance & reliability wave

Date: 2026-09-10 · HEAD base 7fbb8d34 (main) · Mission: audit AND fix end-to-end,
do not stop at findings. Owner: Hermes (performance audit + fix wave).

## 1. Dead-letter root cause — PROVEN from code + production DB (not telemetry)

Production evidence (container nexus-scalp-core, image v9.0.11 built 2026-09-09,
live DB probed READ-ONLY):

- `audit_dead_letter` = 553,206 rows; `audit.db` = 1,006,555,136 bytes.
- Rate: ~10 rows/s sustained (46k rows/hour, 2026-09-09T20h .. 2026-09-10T08h).
- Error distribution (GROUP BY error_message):
  - `table audit_signals has no column named request_id`   440,470
  - `table audit_guard_telemetry has no column named window_start`  93,065
  - `table position_lifecycle_events has no column named event_key`  16,929
  - `table strategy_registry has no column named strategy_id`  1,484
  - `table audit_account_snapshots has no column named timestamp`  784
  - `table audit_experiences has no column named experience_id`  312
  - `table audit_orders has no column named symbol`  154
  - `table experience_model_registry has no column named model_id`  6
  - `table research_worker_state has no column named scope`  1
  - `table intelligence_worker_state has no column named scope`  1
  - ALL `error_type=OperationalError`, ALL `payload_note='audit worker
    batch-retry failure'` (the worker batch-salvage dead-letter path).

Root-cause chain (each link verified live):

1. The container entrypoint runs the TASK-10 startup migration gate
   (`nexus db migrate`) BEFORE the engine (docker/entrypoint.sh step 3).
2. On a fresh volume, `DatabaseMigrationEngine._create_baseline_tables`
   (src/nexus_scalp/database/engine.py:411) creates EVERY manifest table as an
   `id INTEGER PRIMARY KEY` skeleton. The manifest (database/manifest.py)
   declares audit_signals/audit_guard_telemetry/audit_orders/
   audit_account_snapshots/audit_experiences/strategy_registry/
   experience_model_registry/research_worker_state with ZERO columns, so the
   skeletons are bare `id`-only tables. PRAGMA table_info on the live DB
   confirms: `audit_signals -> [id]`, `audit_guard_telemetry -> [id]`,
   `strategy_registry -> [id, context_matrices]`,
   `audit_account_snapshots -> [id, account_source]`,
   `audit_orders -> [id, ticket, order_id, execution_id]`,
   `audit_experiences -> [id, correction_of, record_version, ...]`.
3. The app bootstrap `AuditRepository._create_sqlite_tables` is
   `CREATE TABLE IF NOT EXISTS` — a NO-OP on the existing skeletons. Its
   `_add_column_if_missing` loops heal only EXTRAS (execution_mode,
   reason_code, spread_usd, ...), never the CORE columns (request_id, symbol,
   window_start, ...).
4. Every producer INSERT therefore fails with OperationalError
   "no column named X" inside the audit worker batch; the batch-recovery
   salvage path (audit_repository.py `_process_queue_worker`) re-executes each
   row, fails again, and dead-letters EVERY row — ~10/s for ~15h = 553k rows,
   ~1.8KB/row (~1.4KB avg query+args) = ~1GB.
5. The same failure shape repeats on every restart because the skeleton
   never heals: the same failure is persistently re-produced.

Producer/call path: log_signal (policy → audit worker), _log_guard_telemetry,
PositionLifecycleTracker.record_event (intelligence/lifecycle.py:382),
StrategyRegistry.upsert (research/registry.py:147), ModelRegistry._persist
(experience/provenance.py:152), ExperienceLedger (experience/ledger.py:211),
research/intelligence worker checkpoints (worker.py:161 / 154) — all enqueue
into the shared `AuditRepository._queue`; the background worker is the single
dead-letter producer.

FIX (root cause): `src/nexus_scalp/database/app_columns.py` (NEW,
stdlib-only, slim-tooling import-safe) declares `APP_REQUIRED_COLUMNS` —
the machine-readable column contract of every audit-domain table's real
INSERTs (verified against the app DDL by the repro test). `engine.py
_create_baseline_tables` now heals baseline skeletons with it
(PRAGMA-gated, additive-only, fail-loud, event=BASELINE_SKELETON_HEALED).
Regression: `tests/unit/test_perf_deadletter_skeleton_repro.py` — RED on
HEAD (21 failures incl. `no such column: order_id` crash at HEAD's own
bootstrap), GREEN after the fix (gate→bootstrap→log_signal→flush ⇒ 1 row
in audit_signals, 0 dead-lettered).

Also fixed en route: 31cc2003 added AUDIT-0009 (registry expected = 9) but
skipped the manifest bump — `test_all_domains_manifest_version_equals_
registry_expected` was failing at HEAD 7fbb8d34. Manifest AUDIT_SCHEMA_VERSION
8 → 9 (this wave).

## 2. Bounded dead-letter retention (unbounded-growth kill-switch)

Even with the schema fixed, a future producer failure loop must not be able
to grow audit.db unboundedly. `DeadLetterStore` (dead_letter_store.py) now:

- caps `audit_dead_letter` at the NEWEST `max_rows` rows
  (default 20,000 ≈ 36MB at the measured ~1.8KB/row);
- prunes throttled (≤1 pass / 30s; first pass always due via None sentinel —
  monotonic-boot trap avoided) from the record() path;
- deletes in short batched rowid-anchored transactions (never one giant
  DELETE; WAL-concurrency safe; source of truth = live COUNT, not the
  in-memory write counter);
- warns ONCE per overflow event (re-armed when under cap) — loss of old
  diagnostics is loud, never a per-row log flood;
- forensic value preserved: newest rows kept (current failure signature),
  per-event WARNING with pruned count, `dead_letter_pruned_rows` exposed on
  the AuditRepository facade + `audit_dead_letter_pruned_rows` in
  debug_snapshot (_risk_section);
- cleanup safety: deletes from `audit_dead_letter` ONLY — pinned by tests
  (ledger row, account snapshot, runtime_risk_state HALT row survive a full
  overflow cycle).

Tests: `tests/unit/test_dead_letter_retention_cap.py` (5: cap/newest-keep,
throttle, protected tables, facade counter, HALT+ledger survive).

## 3. /health DB check (PERF-HEALTH)

Measured on the real 1,006MB audit.db (container, read-only probes):
`PRAGMA integrity_check` = ~600ms/call (3 runs: 608.6/600.5/600.4ms);
full `HealthEngine().overall()` = 2,418ms first / 654ms warm. The Docker
healthcheck polls /health every 15s (docker-compose interval: 15s) →
thousands of full scans/day, ~4% CPU duty + page-cache/PSI pressure on a
3.8GB VM.

Fix: `/health` (web/diagnostics_state_routes.py) caches the verdict block on
`app.state.health_probe_cache` for `_HEALTH_TTL_SEC = 60s` (≥ the 15s probe
interval). Probe #1 computes the full sweep; probes #2..N inside the TTL are
O(1) cache reads. Verdict semantics and the 503 NOT-READY contract are
unchanged (docker/healthcheck.sh still parses the verdict from the 503 body).
Integrity checking is NOT weakened: it remains the authority in
`nexus doctor`, `nexus health`, `/api/v1/system/health`, the hygiene
VerificationEngine, and deploy/forensic gates — all uncached, explicit,
operator/maintenance paths.

Tests: `tests/unit/test_perf_health_probe_cache.py` (5: TTL ≥ compose
interval, engine-runs-once pin, TTL expiry, 503 NOT-READY contract unchanged,
integrity_check still present in the explicit diagnostic paths).

Deterministic before/after measurement (real 1,006MB prod DB copy, repo
code, single process):
- warm page cache (container, probe conditions): integrity_check 600ms/call,
  full sweep 3.6s; cold cache on this host: integrity_check 161-170s/call.
- AFTER: probe #1 (cache miss, full sweep) 169,998ms cold / ~3.6s warm —
  unchanged, by design (first probe computes truth);
- AFTER: probe #2/#3 (cache hit) 3.17ms / 2.01ms; engine overall() executed
  exactly ONCE across 3 probes (also pinned by
  test_health_probe_reuses_cached_verdict);
- net effect on the 15s docker poll: per-probe DB cost 600ms→0ms (warm) /
  161s→0ms (cold), i.e. the request path no longer scans the database.

## 4. EXEC_TRACE volume (PERF-EXEC-TRACE)

Measured: 20,249 `[EXEC_TRACE]` lines in the container docker-log census
(2026-09-10 06:44–09:04 window; bursts >700 lines/min), one INFO line per
tick, overwhelmingly NO_TRADE/STANDARD_EVAL/blocked_by=None. The code comment
claimed "the same throttle as radar telemetry" but the emit at
policy.py:1420 was UNCONDITIONAL.

Fix: rate-limit routine NO_TRADE traces in `SignalPolicy.evaluate_probabilities`:
- always emit trade-relevant decisions: non-NO_TRADE action, stage
  FINAL_DECISION, any blocked_by gate, DEDUP_GATE re-surface;
- routine NO_TRADE churn emits ≤1 line / `exec_trace_interval_sec` (4s
  default), suppressed evaluations are COUNTED and the count rides on the
  next emitted line (`trace_suppressed=N`) — the reduction itself stays
  observable;
- a trade-relevant emit resets the window. Expected steady-state: ≥99%
  volume cut at tick rates (e.g. ~10 ticks/s → ≤15 lines/min from ~600).

Tests: `tests/unit/test_perf_exec_trace_throttle.py` (4 behavioral, via a
root-logger capture: rate-limit pin, suppressed-count reporting, gated
decision always emits, non-NO_TRADE always emits + window reset).

## 5. Safety guard pins (mission-required regressions)

`tests/unit/test_perf_safety_contract_pins.py`:
- PERSISTED_HALTED remains fail-closed after the wave: persisted HALT row →
  resolve_boot_decision.trading_allowed=False, row intact, release required;
- LIVE/PAPER separation: the LIVE/PAPER provenance columns
  (audit_signals.account_source, audit_account_snapshots.account_source,
  audit_ledger.account_source) exist after gate→bootstrap heal so PAPER rows
  stay excludable from LIVE metrics; runtime_risk_state is untouched by
  retention (HALT row survives a full dead-letter overflow cycle).

## 6. Validation state (all real runs on this host, slim Linux venv)

- Focused new batteries: 55 tests green (skeleton repro 21, retention 5,
  health cache 5, exec-trace 4, safety pins 2, dead-letter split 18 —
  split battery pre-existing, still green).
- Neighbors: test_policy, test_docker_startup_phase21 (minus pre-existing
  env-dependent test_docker_01 needing NSE_WEB_AUTH_TOKEN in .env — fails
  identically on a clean HEAD stash), test_storage_policy,
  test_storage_lifecycle_wiring, test_database_platform_task_db,
  test_obs_trace_chain, test_research_archive_round3 → 85 passed, 0 failed.
- Migration-safety CI lane (`scripts/ci/check_migration_safety.py`): RC=0.
- `nse smoke --fast --json`: overall_status=PASS, 0 critical failures,
  1740ms.
- ruff check (repo-wide): clean; ruff format --check (repo-wide, 1902
  files): clean; mypy src: Success, no issues in 580 files.
- Memory: not re-measured in-container (old image still running); the wave
  does not add hot-path allocations; retention prunes bounded batches off
  the tick path (INV-001 respected — record() runs on the audit worker
  thread, not the event loop).

## 7. Rollout note (operator)

The running container image still carries the OLD code; the DB it wrote is
1GB. After rebuilding the image with this fix (`docker compose up -d
--build`), the first `db migrate`/engine boot heals nothing (the DB already
has real columns? NO — the live DB's audit_signals is STILL the bare
skeleton). The heal in the migration baseline only fires on version-0/fresh
DBs. For the POISONED live DB the operator should either drop the 1GB
artifacts volume (fresh start, loses paper ledger) or run:
  `python - <<'EOF' (heal script: PRAGMA-gated ADD COLUMN from
  APP_REQUIRED_COLUMNS + DELETE dead-letter backlog)` — the committed
  migration-baseline heal covers fresh installs; the poisoned-DB repair is a
  one-liner using the same contract (see scratch/repair_deadletter_skeleton.py).
Then `VACUUM` reclaims the ~1GB (operator action, `nexus db` — auto-VACUUM
is deliberately refused mid-market).
