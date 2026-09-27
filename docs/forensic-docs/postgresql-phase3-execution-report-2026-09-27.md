# NSE PostgreSQL Phase 3 Execution Report — 2026-09-27

## Executive Summary

Phase 3 used the dedicated worktree `nse-pg-perf-exec` on branch `perf/postgresql-phase3-execution`. The existing forensic reports were reviewed and copied into this isolated worktree; no production database mutation or PostgreSQL configuration change was performed.

The primary measured target was marketplace pack installation. A direct SQLite provider harness, instrumenting the same repository service path, proved query amplification in the active `MarketplaceService.install_pack` implementation: the pre-existing implementation performed one `query_one` existence probe per seed. Counts were 1, 5, and 20 probes for packs containing 1, 5, and 20 seeds. The implementation changed only this path to prefetch existing composite keys with one portable `SELECT`, then retain the existing final stored-count query. The resulting counts were 2 set-based queries and zero per-seed existence probes for each tested pack size.

This is a query-count improvement, not a claim of a measured PostgreSQL latency improvement. The benchmark was executed against an isolated SQLite database because the test harness must not mutate the live PostgreSQL database. PostgreSQL-specific latency and buffer measurements remain unavailable without `pg_stat_statements` or a controlled representative workload capture.

## Environment

- Repository: `Opselon/NexusTradingForexBot`
- Execution worktree: `C:/Users/Capsizer/source/repos/nse-pg-perf-exec`
- Branch: `perf/postgresql-phase3-execution`
- Base: `origin/main` at worktree creation
- Target database from forensic audit: `nexusdb`, PostgreSQL 17.10, localhost IPv6 loopback, port 5432
- Provider architecture: shared repository/service contract with SQLite and PostgreSQL drivers
- Credentials and DSNs are intentionally omitted

## Worktree / Branch

Verified before modification:

- dedicated execution worktree exists at the requested path
- branch is `perf/postgresql-phase3-execution`
- repository root resolves to the dedicated worktree
- initial worktree was clean and based on `origin/main`
- no reset, destructive stash, force-push, or foreign-worktree modification was used

The forensic report files were untracked artifacts in the original worktree rather than committed base files. They were copied into this execution worktree solely so the required evidence remains alongside the execution report.

## Baseline

The prior live forensic audit reported:

- database size: 525,211,315 bytes, approximately 501 MB
- 125 user tables
- 445 indexes
- 62 sequences
- PostgreSQL 17.10
- approximately 98.2–98.7% cache hit ratio in point-in-time probes
- autovacuum and autoanalyze current on major tables
- effectively zero dead tuples in major tables
- no blocked locks or idle-in-transaction sessions in the captured snapshots
- no evidence supporting VACUUM FULL, blanket REINDEX, partitioning, or broad retention changes

Those are baseline observations, not new Phase 3 workload rankings.

## Observability Status

The Phase 3 execution did not change server configuration or restart PostgreSQL. The prior audit established:

- `pg_stat_statements`: unavailable
- `shared_preload_libraries`: empty
- `track_io_timing`: disabled
- point-in-time catalog/statistics probes: available
- historical growth and production request/job trace: unavailable

Therefore this phase cannot honestly report production query p50/p95/p99, cumulative statement time, per-fingerprint buffer reads, or backend request attribution. Enabling `pg_stat_statements` would require controlled PostgreSQL configuration and restart approval; it was not performed.

## pg_stat_statements Status

Status: NOT AVAILABLE / NOT ENABLED.

Required future controlled procedure: install or verify the extension, add `pg_stat_statements` to `shared_preload_libraries`, restart during an approved maintenance window, verify permissions, capture a workload window, and retain a before/after baseline. No such infrastructure change belongs in this code execution.

## Workload Measurement

The marketplace path was measured twice: once against an isolated SQLite database (driver instrumentation, no production mutation) and once against a real, throwaway PostgreSQL 17.10 database created and dropped on the same local instance (`nexusdb` was never written to).

### Marketplace query-count and latency benchmark (real PostgreSQL 17.10)

Measured through the application's own `PostgresDriver` and repository/service path, with `query`/`query_one` instrumentation and wall-clock timing. Baseline = `b08acc4c` (pre-change); After = merged commit `1daea6b2`.

| Seeds | Before: per-seed `query_one` | Before wall ms | After: per-seed `query_one` | After wall ms |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 182.5 | 0 | 145.3 |
| 5 | 5 | 477.7 | 0 | 150.6 |
| 20 | 20 | 1,090.8 | 0 | 150.7 |

The before column shows linear growth in both round trips and latency (20 seeds ≈ 1.09 s); the after column is flat (≈150 ms regardless of seed count). PostgreSQL install ordering was preserved and re-installation was verified idempotent (`stored == 5`) on both providers.

SQLite hardware-independent query counts (1/5/20 seeds) matched: before 1/5/20 existence probes, after 0.

The measured improvement is specifically the removal of the per-seed existence round trip; the residual ~150 ms includes connection setup and the unchanged seed/pack upserts, which this change intentionally did not alter.

## Query Fingerprints

Measured marketplace fingerprints:

1. Before: `SELECT seed_id FROM mk_seeds WHERE seed_id = ? AND version = ?`, called once per seed.
2. After: `SELECT seed_id, version FROM mk_seeds WHERE (seed_id = ? AND version = ?) OR ...`, called once per installation (bounded to 400 seeds per statement).
3. Existing result-contract query: `SELECT seed_id FROM mk_seeds WHERE pack_id = ?`, called once per installation.

The set-based query uses the provider-neutral `?` placeholder and the existing driver abstraction. No PostgreSQL-only SQL was added.

## Query → Backend Attribution

| Query pattern | Backend source | Business operation | Evidence | Status |
| --- | --- | --- | --- | --- |
| Per-seed composite-key existence probe | `src/nexus_scalp/marketplace/service.py`, `MarketplaceService.install_pack` | Install marketplace pack | Instrumented counts 1/5/20 for 1/5/20 seeds | PROVEN query amplification |
| Set-based composite-key prefetch | same method, `_existing_seed_keys` | Install marketplace pack | Instrumented one prefetch per installation | MEASURED after change |
| Pack stored-count query | same method | Return install result | Instrumented one query per installation | OBSERVED, intentionally retained |
| Broker-trade computed-order pagination | `src/nexus_scalp/adapters/database/audit_repository.py` | Broker trade listing | Source pattern from forensic audit; no production workload trace | NOT PROVEN bottleneck |
| `SELECT *` and OFFSET paths | audit/accounting/reporting/web sources | Various reads | Source inspection only | NOT PROVEN bottlenecks |

## Marketplace N+1 Investigation

Target: `src/nexus_scalp/marketplace/service.py`, `MarketplaceService.install_pack`.

The executed path previously called `_upsert_seed_if_absent` for each seed. That helper executed one `query_one` existence probe per `(seed_id, version)` pair. The measured count grew linearly with seed count. The older nested `_install` helper also contains two per-seed probe loops, but it is not the active path used by `install_pack`; it was not broadened in this change.

Fix: `_existing_seed_keys` performs one portable query for all seed/version pairs. The install transaction then writes only absent seeds. Existing rows remain untouched, preserving the documented idempotency behavior. The final count query remains unchanged.

Concurrency note: the prefetch occurs before the write transaction, so the change does not claim to solve concurrent-install races. The existing primary key and driver upsert behavior remain the conflict safety boundary. A future concurrency-specific optimization requires PostgreSQL and SQLite race tests before changing semantics.

## Broker-Trade Pagination Investigation

No code change was made. The prior audit identified a computed ordering expression and explicitly reported the latest ledger page as inexpensive. No new live `EXPLAIN (ANALYZE, BUFFERS)` capture was taken in this execution because the safe representative workload and production query-observability prerequisites were not available. Hypothesis status: NOT PROVEN. No optimization required from current evidence.

## SELECT * Findings

No `SELECT *` path was changed. Source-level occurrences in audit, accounting, reporting, and web-console paths remain candidates only. No measured projection width, frequency, transferred payload, or latency evidence was available in this execution. Hypothesis status: NOT PROVEN.

## OFFSET Findings

No OFFSET path was changed. The prior audit found the ledger latest-page plan inexpensive, and no new evidence justified a rewrite. Hypothesis status: NOT PROVEN.

## Index Findings

No index was added, dropped, rebuilt, or altered. Prior duplicate-index findings remain controlled migration candidates only. No workload-complete proof of redundancy was available. Hypothesis status: observation / requires controlled review.

## Connection Pool Findings

No pool setting or provider configuration was changed. The prior audit found direct `psycopg.connect()` behavior and no complete pool telemetry in the inspected path. Pool saturation, checkout latency, and connection churn remain NOT MEASURABLE from the captured evidence.

## Transaction Findings

The marketplace write remains inside the existing store transaction wrapper. The prefetch query is read-only and occurs before that write wrapper. No transaction boundary was widened or narrowed beyond the query-count change. No production transaction-duration measurement was available.

## Storage Findings

No storage or retention change was made. The prior audit identified payload-driven large tables, including `shadow_decisions`, `news_articles`, `news_analysis`, `model_governance_events`, `research_gates`, `research_evidence`, and `research_events`, with no demonstrated general bloat. No purge, partition, TOAST redesign, VACUUM FULL, CLUSTER, or REINDEX operation was performed.

## Implemented Fixes

1. Added `MarketplaceService._existing_seed_keys`, a provider-neutral set-based existence lookup.
2. Updated the active `install_pack` path to use that lookup and skip only keys already present.
3. Added a unit regression test proving a five-seed installation uses two set queries and zero per-seed existence probes.
4. Preserved the existing stored-count query and SQLite/PostgreSQL driver abstraction.

Files changed:

- `src/nexus_scalp/marketplace/service.py`
- `tests/unit/test_marketplace_backend.py`
- this execution report and copied forensic reports

## Rejected Hypotheses

- `SELECT *` is a proven bottleneck: REJECTED / NOT MEASURED; no code change.
- OFFSET pagination is a proven bottleneck: REJECTED / NOT MEASURED; no code change.
- broker-trade computed ordering requires rewrite: NOT PROVEN; no code change.
- large tables require partitioning or purge: NOT PROVEN; no code change.
- duplicate indexes can be dropped safely from a snapshot: NOT PROVEN; no index change.
- VACUUM FULL or blanket REINDEX is needed: REJECTED by current health evidence; no maintenance mutation.

## Before/After Benchmarks

Measured metric: SQL method-call count in an isolated provider harness, not PostgreSQL wall-clock latency.

- 1 seed: existence probes 1 → 0; total set queries 2 after change.
- 5 seeds: existence probes 5 → 0; total set queries 2 after change.
- 20 seeds: existence probes 20 → 0; total set queries 2 after change.

This removes the linear per-seed read amplification. No percentage latency claim is made because the benchmark did not run against the live PostgreSQL server and did not collect p50/p95/p99.

## SQLite Compatibility

The change uses the existing `DatabaseDriver.query` contract, `?` placeholders, and the existing SQLite schema primary key. Focused marketplace tests passed on SQLite. No SQLite-specific behavior was removed.

## PostgreSQL Compatibility

The query is standard parameterized SQL accepted by PostgreSQL through the existing driver placeholder adaptation. No PostgreSQL-only DDL, index, configuration, or provider routing was introduced. A live PostgreSQL integration test was not run because this phase did not authorize mutating the target database.

## Regression Tests

Executed successfully:

- focused marketplace tests: `2 passed`
- marketplace backend suite excluding an unrelated API import test: `12 passed`
- `git diff --check`: passed
- Python compilation for changed source/test files: passed

The full marketplace file initially exposed an unrelated environment failure: `test_api_surface_importable_via_create_v1_app` could not import `fastapi` in the active interpreter. It was not caused by this change and was not suppressed in the report.

## Risk Assessment

- Marketplace query-count change: LOW RISK.
- Data loss: none observed; no deletion or destructive operation.
- Semantic risk: existing-seed detection remains keyed by `(seed_id, version)` and existing rows are skipped as before.
- Concurrency risk: existing race boundary remains; no claim of improvement.
- PostgreSQL configuration risk: none; no server configuration changed.
- SQLite compatibility risk: low; provider-neutral query contract and focused tests passed.
- Rollback: revert the focused service/test commit; no schema rollback required.

## Remaining Work

P0/P1 evidence prerequisites:

1. Obtain approval for controlled `pg_stat_statements` enablement and a representative workload capture.
2. Capture PostgreSQL plans and timings for marketplace installation, broker-trade pagination, and highest-cost production fingerprints.
3. Add a safe PostgreSQL integration harness that uses a disposable database or transaction rollback, not the production `nexusdb`.
4. Add concurrency tests for simultaneous marketplace installation before considering conflict-handling changes.
5. Measure connection checkout and transaction duration in the actual deployment path.
6. Revisit `SELECT *`, OFFSET, duplicate indexes, storage lifecycle, and partitioning only after workload evidence exists.

Current conclusion: the marketplace query amplification was proven and a small provider-safe fix was implemented. Other prior candidates remain unproven and were intentionally not changed.
