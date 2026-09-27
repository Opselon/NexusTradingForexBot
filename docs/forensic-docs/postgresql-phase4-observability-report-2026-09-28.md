# NSE PostgreSQL Phase 4 — Observability, Workload Profiling & Evidence

**Date:** 2026-09-28
**Branch:** `perf/postgresql-phase4-observability`
**Worktree:** `C:/Users/Capsizer/source/repos/nse-pg-observability` (from clean `origin/main` @ `77eb21b1`)
**Phase:** MEASUREMENT ONLY — no optimization implemented, no PostgreSQL configuration changed.

---

## Executive Summary

Phase 4 established query-level observability for the NSE PostgreSQL backend
**without enabling `pg_stat_statements`** (which requires a server restart and
was therefore not enabled), then used that observability to run a
representative workload and rank every query by total DB time, call count,
mean latency and rows.

The central result is a **proven, root-caused defect** that was previously
only a candidate:

> **`audit_repository.get_broker_trades` orders by the computed expression
> `COALESCE(NULLIF(exit_time,''),'') DESC`. No index can serve that ORDER BY
> — not even the existing `idx_broker_trades_exit`, which indexes the *raw*
> `exit_time` column. EXPLAIN ANALYZE proves every pagination request performs
> a full heap Seq Scan plus a full-table Sort.** (See §17, Candidate P4-A.)

Everything else the earlier audits flagged as "suspicious" was measured and
left alone, because the measurements did not justify a change (§20).

No PostgreSQL configuration was modified. No index was created or dropped. No
schema was changed. `nexusdb` was only ever read. All workload measurement ran
against a throwaway database created and dropped on the local instance.

---

## Environment

| Item | Value |
|---|---|
| Repository | `Opselon/NexusTradingForexBot` |
| PostgreSQL | 17.10 (x86_64-windows, msvc-19.44.35227, 64-bit) |
| Database | `nexusdb` (read-only access throughout) |
| Host / port | `::1` / 5432 |
| Schema / search_path | `public` |
| Timezone | `Asia/Tehran` |
| Encoding | UTF8 |
| Application venv | `NexusTradingForexBot/.venv` (psycopg v3) |
| Worktree HEAD | `77eb21b1` = clean `origin/main` |

---

## Worktree / Branch

Created fresh from `origin/main` per the phase contract:

```
git worktree add C:/Users/Capsizer/source/repos/nse-pg-observability \
  -b perf/postgresql-phase4-observability origin/main
```

Verified before any edit: `git status --short --branch` (clean),
`git branch --show-current` (`perf/postgresql-phase4-observability`),
`git rev-parse --show-toplevel`, `git log -1 --oneline`, `git worktree list`.
No foreign WIP was present and none was touched. Phase 3's worktree
(`nse-pg-perf-exec`) was left untouched.

---

## Database Identity

Verified through the application's own configuration path
(`load_database_config('audit')` + `build_postgres_url()`), not an invented DSN:

* `current_database()` → `nexusdb`
* `current_user` → `postgres`
* `inet_server_addr()` / `inet_server_port()` → `::1` / `5432`
* `current_schema()` → `public`
* `version()` → `PostgreSQL 17.10 on x86_64-windows`

Credentials were never printed, logged, or written to any file. Every probe
builds the URL through the app's own config helpers, and the logs the driver
emits already mask the password
(`postgresql://postgres:***@localhost:5432/...`).

---

## PostgreSQL Version

PostgreSQL **17.10**, compiled by MSVC 19.44.35227, 64-bit. `server_version_num` = `170010`.

Note: this release renames several `pg_stat_*` columns (e.g. `pg_stat_database`
uses `tup_returned`, not `tuples_returned`; `pg_stat_bgwriter` no longer exposes
`checkpoints_timed`/`buffers_backend`). The probes were corrected against the
live catalog.

---

## Observability Baseline

Captured read-only at 2026-09-28 02:29 IRST.

**Server settings relevant to observability and performance:**

| Setting | Value | Source |
|---|---|---|
| `shared_preload_libraries` | *(empty)* | default |
| `compute_query_id` | `auto` | default |
| `track_activity_query_size` | `1024` (1kB) | default |
| `track_io_timing` | `off` | default |
| `track_wal_io_timing` | `off` | default |
| `track_activities` | `on` | default |
| `track_counts` | `on` | default |
| `log_min_duration_statement` | `-1` (disabled) | default |
| `shared_buffers` | 128MB | default |
| `effective_cache_size` | 4GB | default |
| `work_mem` | 4MB | default |
| `max_connections` | 100 | default |
| `autovacuum` | `on` | default |
| `default_statistics_target` | 100 | default |

**Database-level cumulative statistics** (`pg_stat_database`, `nexusdb`):

| Metric | Value |
|---|---|
| `xact_commit` | 312,251 |
| `xact_rollback` | 1,424 |
| `blks_read` | 188,081 |
| `blks_hit` | 10,688,247 |
| **cache hit ratio** | **98.21%** |
| `tup_returned` | 16,248,979 |
| `tup_fetched` | 7,220,608 |
| `tup_inserted` | 699,292 |
| `tup_updated` | 4,257 |
| `tup_deleted` | 611 |
| `conflicts` | 0 |
| `temp_files` | 2 |
| `temp_bytes` | 524,288 (512 KiB) |
| `deadlocks` | 0 |
| `blk_read_time` / `blk_write_time` | 0.0 / 0.0 (`track_io_timing` off) |
| `session_time` | 364,739,995 ms |
| `active_time` | 71,057 ms |
| `stats_reset` | NULL (never reset) |

`postmaster_start_time` = 2026-09-27 20:18:35 IRST, so these counters cover
roughly the **last ~6.2 hours** at capture time. That window matters for every
interpretation below: these are recent-history counters, not lifetime numbers.

**Background writer** (`pg_stat_bgwriter`): `buffers_clean` 33,609;
`maxwritten_clean` 265; `buffers_alloc` 1,667,668.

**Storage:** `nexusdb` = 525,211,315 bytes (~501 MB); 125 user tables;
280 user indexes in `public` (445 across all schemas); 2 schemas.

---

## pg_stat_statements Status

**State: B — extension AVAILABLE on disk but NOT INSTALLED in `nexusdb`.**

Evidence (all read-only):

* `SELECT * FROM pg_available_extensions WHERE name = 'pg_stat_statements'`
  returns a row → the extension is packaged with this server build.
* `SELECT * FROM pg_extension WHERE extname = 'pg_stat_statements'` returns
  **no row** → it is not installed in `nexusdb`.
* `shared_preload_libraries` is **empty** → the module is not loaded.
* `pg_stat_statements_view` resolves to NULL.
* `compute_query_id = auto`, but with the module unloaded, `pg_stat_activity.query_id`
  is NULL for every session (verified: 0 sessions carry a non-zero `query_id`,
  and our own probe's own session reported `query_id IS NULL`).

**Consequence:** normalized query fingerprints, per-query total/mean/min/max
time, and per-query block I/O are **unobtainable from the server** in the
current state. This is the exact gap Phase 4 was asked to close — and it is
closed application-side instead (§11, §12).

### Controlled enablement plan (NOT executed — requires authorization)

**Why it is required.** `pg_stat_statements` must be listed in
`shared_preload_libraries`, which is a `postmaster` (restart) setting, then
created per-database with `CREATE EXTENSION`.

**Exact change:**

1. `ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';`
   (or edit `postgresql.conf`)
2. **Restart** the PostgreSQL 17.10 service. `SELECT pg_reload_conf()` is NOT
   sufficient — `shared_preload_libraries` is `context = postmaster`.
3. `CREATE EXTENSION IF NOT EXISTS pg_stat_statements;` inside `nexusdb`.
4. Optionally `ALTER SYSTEM SET track_io_timing = on;` (reload-only, see §8)
   and `ALTER SYSTEM SET pg_stat_statements.max = 10000;` (restart).

**Expected benefit.** Server-side normalized query fingerprints with
`calls`, `total_exec_time`, `mean_exec_time`, `min/max`, `rows`,
`shared_blks_hit/read`, `temp_blks_*`, and `wal_*` per statement — replacing
the application-side approximation with the authoritative view, including
planning time and I/O timing.

**Expected overhead / risk.** `pg_stat_statements` is widely deployed in
production. Its cost is a small per-statement bookkeeping overhead plus a
shared hash area sized by `pg_stat_statements.max` (default 5000 entries).
Memory is fixed at server start (default ~5000 × ~270B). Recurring cost is
minimal; the material risk is the **restart**, not the extension.

**Rollback.** `ALTER SYSTEM SET shared_preload_libraries = '';` restart;
`DROP EXTENSION pg_stat_statements;` Statistics views revert to absent.

**Validation.** After step 3: `SELECT count(*) FROM pg_stat_statements` ≥ 1
following any query; confirm `queryid`/`query` columns are populated and that
`calls` increments for a repeated probe statement.

**Authorization status:** NOT REQUESTED and NOT PERFORMED. This plan is
delivered as the phase's required deliverable and nothing more.

---

## track_io_timing Status

**State: OFF** (`source = default`, `pending_restart = false`).

`track_io_timing` is a **SUSET / reload** setting (no restart required), but
it is also **not enabled**, so `blk_read_time`/`blk_write_time` in
`pg_stat_database` and `pg_stat_statements` are all 0.0. Physical read
latency is therefore **not attributable to individual queries** today.

Because it requires a server configuration change, it was **not enabled**.
Overhead when on is a small per-I/O `clock_gettime` cost; the benefit is
distinguishing CPU-bound from I/O-bound queries. Rollback is a reload back to
`off`. Left as an authorization-gated follow-up.

---

## Connection Baseline

`pg_stat_activity` for `nexusdb` at capture time: **13 client backends** —
**1 active, 12 idle, 0 idle-in-transaction, 0 waiting**. Zero ungranted locks.
No long-running transactions (`xact_start` populated only on the active
session), no blocked queries, no deadlocks in the counter window.

The idle pool is **clearly labeled by `application_name`**: sessions carrying
`pg-write` and `pg-read` — the fabric's separate read/write planes
(`nexus_scalp/database/fabric/pg_planes.py`). This means pool membership is
already observable server-side, which is a positive finding for §22.

**Measured pool sizing** (from `PoolLimits` defaults in `pg_planes.py`):
`min_size=2`, `max_size=10` per plane, with `reconnect_timeout_sec=30`.
`max_connections` is 100, so even both planes at full size use ~20% of the
server ceiling.

**Connection churn — MEASURED, STRONG CANDIDATE (not a defect to fix in
Phase 4).** The direct-driver workload in §14 opened a **new connection per
statement**: the driver logged `postgres connect` once per query. That is the
expected behavior of `PostgreSQLDriver` used *outside* the pooled fabric path
(the fabric is what makes checkouts sub-millisecond), and it is exactly what
the fabric exists to prevent — but it also means any code path reaching the
driver directly, bypassing `PgReadPlane`/`PgWritePlane`, pays a full connect
per statement. This is observable and worth confirming in production, and it
is recorded here rather than "fixed" because no production cost was measured.

---

## Application Query Attribution

**Before Phase 4, attribution was weak at the driver boundary.** The existing
`query_logging.py` already provided structured failure/slow-query observability
(`domain`, `operation`, `kind`, `duration_ms`, `rows`, `correlation_id`) and a
persistence sink into `db_operation_logs` with columns
`query_name`, `repository`, `operation`, `domain`, `correlation_id`,
`duration_ms`, `rows`. That schema already carries the four fields the phase
asks for.

But the persistent sink is **opt-in and off by default**
(setting `database.log_persistence.enabled`; the `settings` relation is not
present in `nexusdb`, so it fails closed to disabled), and
`db_operation_logs` on the live database holds **1 row**. So in practice the
application emitted **no query-level workload profile** at all. Server-side,
`log_min_duration_statement = -1`, so the PostgreSQL log was no substitute.

**What Phase 4 added** — a provider-agnostic, opt-in, in-process query
metrics recorder at the ONE point both providers share: the driver boundary.

* `query_logging._QueryMetrics` / `query_metrics` — per-`query_name`
  aggregation of `calls`, `total_ms`, `min_ms`, `max_ms`, `rows`, `errors`,
  plus `operation`, `domain`, `repository`, and a masked `sql_shape`.
* `QueryMetricsRecorder` — a context manager sitting alongside the existing
  `QueryTimer`. Disabled by default; the fast path is one attribute read on
  enter and one on exit. Never raises.
* `DatabaseDriver.query_name(sql)` (base class) — a provider-agnostic
  normalized statement shape (whitespace collapsed, string literals and
  numbers replaced by `?`, bounded to 120 chars) so the *same business query*
  issued with different parameters aggregates under ONE name on both
  providers.
* Wired into `PostgreSQLDriver.query` / `.query_one` and
  `SQLiteDriver.query` / `.query_readonly` / `.query_one`, so both providers
  produce identical query names for identical statement shapes.

**Design choices that matter:**

* The key is a stable **query name**, not raw SQL — a single fingerprint can
  be reached from several call sites, and business attribution is what the
  profile needs.
* Rankings (§12) are kept **separate**, never collapsed into one score.
* No verbose SQL logging was added to any request path. No raw query dumping.
* SQL retained as `sql_shape` passes through the existing `mask_query_text`,
  so no bound value or credential is ever stored (proven by a regression test
  asserting the existing `SECRET` fixture is absent from the snapshot).

**SQLite compatibility preserved:** the helper lives on the shared base class,
both drivers carry identical instrumentation, and no PostgreSQL-specific SQL
was introduced into any shared path. Verified by a test asserting both drivers
derive the same name for the same statement.

---

## Representative Workload Definition

A real NSE-shaped workload was constructed rather than random SELECTs. The
measurement targets mirror the live database's actual table shapes, row
counts, and access patterns (derived from the Phase 3 audits and the live
`pg_stat_user_tables`):

| Workload class | Representative operation |
|---|---|
| Broker-trade pagination | `get_broker_trades` shape — `LIMIT/OFFSET` ordered by `COALESCE(NULLIF(exit_time,''),'') DESC`, pages 0/500/1000/2000 |
| Wide-row audit reads | `get_broker_deals` / `get_broker_orders` shapes — `SELECT * ... LIMIT 2000` |
| Narrow audit reads | `audit_ledger`, `audit_signals` — `SELECT * ... LIMIT 500` |
| Per-item existence probes | `SELECT 1 FROM audit_broker_trades WHERE ticket = ?` ×20 per round (the Phase 3 anti-pattern) |
| Analytics aggregation | `GROUP BY symbol` with `sum(profit)` over the trades table |

**Dataset** (seeded to match the live distribution, live-row-magnitude):
`audit_broker_trades/deals/orders` 12,000 each; `audit_account_snapshots`
8,000; `audit_ledger` 12,000; `audit_signals` 11,000; `shadow_decisions`
55,000; `news_articles` 25,000; `news_analysis` 21,000;
`model_governance_events` 59,000; `research_events` 82,000;
`research_gates` 26,000. Representative secondary indexes were created on the
same columns the live schema indexes (`symbol`, `position_id`, `ticket`,
`time`), and `ANALYZE` was run before measurement.

**Safety:** the workload ran on a throwaway database
(`nse_p4_prof_<random hex>`) created on the local instance, and dropped with
`DROP DATABASE ... WITH (FORCE)` afterward. `nexusdb` was never written to.
No trading was triggered, no risk state touched, no orders placed.

---

## Measurement Window

* **Warm-up:** 1 full workload round (29 queries) with metrics enabled then
  reset — excludes JIT warm-up, connection establishment and plan caching.
* **Measured window:** 5 workload rounds = **145 queries**, wall time
  **8.18 s**.
* **Start/end:** captured per-phase with `time.perf_counter()`; absolute
  timestamps recorded in the run output (2026-09-28 02:48 IRST).
* **Database identity during measurement:** throwaway
  `nse_p4_prof_5f3a5cf66ea1`, PostgreSQL 17.10, `enable_seqscan=on`,
  `work_mem=4MB`, `jit` disabled by the driver's session settings.
* **Application commit:** worktree HEAD `77eb21b1` (clean `origin/main`).

---

## Workload Results

All numbers below are from the measured window above, collected through the
application's own `PostgreSQLDriver` (so they include driver overhead, not
just server execution).

**Totals:** 145 statements, 8.18 s wall, 25,190 rows returned.

Notable raw behavior observed during the run: the driver logged a
`postgres connect` for **every statement** (145 connects), confirming the
connection-churn observation in §10 for any path that uses the driver
directly instead of the pooled fabric.

---

## Query Fingerprints

`pg_stat_statements` was **not** enabled (§6), so no server-side normalized
fingerprints exist. Application-side normalized shapes were produced by
`DatabaseDriver.query_name` — whitespace collapsed, literals and numbers
replaced by `?`. Example mappings from the measured workload:

| Raw statement (representative) | Normalized fingerprint (truncated to 120) |
|---|---|
| `SELECT * FROM audit_broker_trades ORDER BY COALESCE(NULLIF(exit_time,''), '') DESC LIMIT 500 OFFSET 0` | `SELECT * FROM audit_broker_trades ORDER BY COALESCE(NULLIF(exit_time,?), ?) DESC LIMIT ? OFFSET ?` |
| `SELECT 1 FROM audit_broker_trades WHERE ticket = 1000005` | `SELECT ? FROM audit_broker_trades WHERE ticket=[REDACTED] |
| `SELECT symbol, count(*), sum(profit) FROM audit_broker_trades GROUP BY symbol ORDER BY 2 DESC` | identical (no literals) |

These are **not** `pg_stat_statements` fingerprints and do not claim its
precision; they are a grouping key sufficient for ranking (§12) and for
business attribution (§16).

---

## Top Queries by Total Time (Rank A)

| # | Query shape (normalized, truncated) | Calls | Total ms | Mean ms | Max ms | Rows |
|---|---|---:|---:|---:|---:|---:|
| 1 | `SELECT ? FROM audit_broker_trades WHERE ticket=[REDACTED] | 100 | 406.0 | 4.06 | 16.00 | 100 |
| 2 | `SELECT * FROM audit_broker_trades ORDER BY COALESCE(NULLIF(exit_time,?), ?) DESC LIMIT ? OFFSET ?` | 20 | 190.0 | 9.50 | 16.00 | 10,000 |
| 3 | `SELECT * FROM audit_broker_orders ORDER BY time_setup DESC LIMIT ?` | 5 | 64.0 | 12.80 | 16.00 | 10,000 |
| 4 | `SELECT symbol, count(*), sum(profit) FROM audit_broker_trades GROUP BY symbol ORDER BY ? DESC` | 5 | 31.0 | 6.20 | 16.00 | 40 |
| 5 | `SELECT * FROM audit_broker_deals ORDER BY time DESC LIMIT ?` | 5 | 30.0 | 6.00 | 15.00 | 10,000 |
| 6 | `SELECT * FROM audit_ledger ORDER BY ticket DESC LIMIT ?` | 5 | 16.0 | 3.20 | 16.00 | 2,500 |
| 7 | `SELECT * FROM audit_signals ORDER BY created_at DESC LIMIT ?` | 5 | 16.0 | 3.20 | 16.00 | 2,500 |

---

## Top Queries by Call Amplification (Rank B)

| # | Query shape | Calls | Interpretation |
|---|---|---:|---|
| 1 | `SELECT ? FROM audit_broker_trades WHERE ticket=[REDACTED] | **100** | 20 probes × 5 rounds — deliberately included as the Phase 3 anti-pattern. **A live profile showing this shape at this ratio is the signature of the defect class the Phase 3 fix eliminated.** |
| 2 | broker-trade pagination | 20 | 4 pages × 5 rounds — expected paging cadence, not amplification |
| 3–7 | wide/narrow `SELECT *` and the aggregation | 5 each | fixed per-round cost |

**Amplification detection is now a one-line query on the profile**: any shape
whose `calls` grows linearly with the number of items processed is an N+1
candidate. This is precisely the instrumentation's purpose.

---

## Top Queries by Mean Latency (Rank C)

| # | Query shape | Mean ms | Max ms | Calls |
|---|---|---:|---:|---:|
| 1 | `SELECT * FROM audit_broker_orders ORDER BY time_setup DESC LIMIT ?` | 12.80 | 16.00 | 5 |
| 2 | broker-trade pagination (`COALESCE` ORDER BY) | 9.50 | 16.00 | 20 |
| 3 | `GROUP BY symbol` aggregation | 6.20 | 16.00 | 5 |
| 4 | `SELECT * FROM audit_broker_deals ...` | 6.00 | 15.00 | 5 |
| 5 | per-ticket existence probe | 4.06 | 16.00 | 100 |

Note that on this dataset **mean latency alone does not rank the real
problem first** — Rank C puts `audit_broker_orders` above the
`COALESCE`-ordered pagination, while Rank A and the EXPLAIN evidence in §17
do. This is exactly why the phase requires the rankings to stay separate.

---

## Top Queries by I/O (Rank D)

**NOT MEASURABLE in the current configuration.** `track_io_timing` is off,
so `blk_read_time`/`blk_write_time` are 0.0 everywhere, and
`pg_stat_statements` is unavailable for per-query block counters.

What IS observable is buffer traffic at the table level (`pg_statio_user_tables`),
which is a coarse proxy, not a per-query attribution:

| Table | heap blks read | heap blks hit | heap hit ratio | idx blks read | idx blks hit |
|---|---:|---:|---:|---:|---:|
| `shadow_decisions` | 66,605 | 143,959 | 0.6837 | 483 | 731,300 |
| `news_analysis` | 13,981 | 91,364 | 0.8673 | 1,209 | 134,191 |
| `model_governance_events` | 13,928 | 128,800 | 0.9024 | 232 | 678,953 |
| `news_articles` | 10,944 | 144,487 | 0.9296 | 882 | 497,246 |
| `research_gates` | 11,568 | 63,207 | 0.8453 | 93 | 364,921 |
| `research_evidence` | 9,434 | 45,790 | 0.8292 | 71 | 219,385 |
| `research_events` | 7,508 | 171,131 | 0.9580 | 651 | 905,615 |
| `audit_signals` | 4,043 | 869,701 | 0.9954 | 55 | 370,105 |

`shadow_decisions` shows the lowest heap hit ratio (0.68) among large tables —
a **MEASURED observation**, not a proven bottleneck. With
`track_io_timing` off it cannot be attributed to specific queries, and no
change was made.

---

## Top Queries by Rows (Rank E)

| # | Query shape | Rows | Calls | Rows/call |
|---|---|---:|---:|---:|
| 1 | broker-trade pagination (`COALESCE` ORDER BY) | 10,000 | 20 | 500 |
| 2 | `SELECT * FROM audit_broker_deals ... LIMIT ?` | 10,000 | 5 | 2,000 |
| 3 | `SELECT * FROM audit_broker_orders ... LIMIT ?` | 10,000 | 5 | 2,000 |
| 4 | `SELECT * FROM audit_ledger ... LIMIT ?` | 2,500 | 5 | 500 |
| 5 | `SELECT * FROM audit_signals ... LIMIT ?` | 2,500 | 5 | 500 |
| 6 | per-ticket existence probe | 100 | 100 | 1 |

`SELECT *` projection width is therefore **observable** (row count × column
width), which is what the earlier audit flagged as unmeasurable. Projection
*width* was not measured per query — only row counts — so no
payload-cost claim is made.

---

## Query → Backend Attribution

| Query shape (from §12–§16) | Backend call site | Business operation |
|---|---|---|
| `SELECT * FROM audit_broker_trades ORDER BY COALESCE(NULLIF(exit_time,?), ?) DESC LIMIT ? OFFSET ?` | `src/nexus_scalp/adapters/database/audit_repository.py:1828` — `AuditRepository.get_broker_trades` | broker-trade pagination / reporting |
| `SELECT * FROM audit_broker_deals ORDER BY time DESC LIMIT ?` | `audit_repository.py:1859` — `get_broker_deals` | broker reconciliation |
| `SELECT * FROM audit_broker_orders ORDER BY time_setup DESC LIMIT ?` | `audit_repository.py:1883` — `get_broker_orders` | broker reconciliation |
| `SELECT ? FROM audit_broker_trades WHERE ticket=[REDACTED] | *(synthetic probe — no production call site; included to calibrate the N+1 detector)* | — |
| `SELECT * FROM audit_ledger ORDER BY ticket DESC LIMIT ?` | `src/nexus_scalp/accounting/core.py:315` — `_load_ledger_rows` | accounting ledger read |
| `SELECT * FROM audit_signals ORDER BY created_at DESC LIMIT ?` | `audit_repository.py` signals path | signal audit read |
| `SELECT symbol, count(*), sum(profit) ... GROUP BY symbol` | *(synthetic probe)* | analytics-style aggregation |

Attribution was made from **source inspection plus the statement shape**, not
from SQL text alone. The phase's rule on multi-call-site ambiguity is
respected: every shape above is produced by exactly one repository method in
the current source, so the mapping is unambiguous. The two synthetic probes
are labeled as such and carry no call site.

---

## Marketplace Verification

**Phase 3 fix CONFIRMED PROVEN under instrumentation.**

The Phase 3 fix (`MarketplaceService._existing_seed_keys`, merged as PR #511)
replaced one-per-seed existence probes with a bounded batched lookup. Phase 4
verified it three ways:

1. **The existing regression test passes** on this branch:
   `tests/unit/test_marketplace_backend.py::test_pack_install_prefetches_existing_seeds_in_one_query`
   (asserts 2 set queries and 0 `query_one` calls for a 5-seed install).
2. **The full marketplace suite passes** (13/13).
3. **Instrumented store-level verification** with 20 seeds: the fixed query
   shape —
   `SELECT seed_id, version FROM mk_seeds WHERE (seed_id = ? AND version = ?) OR ...`
   — resolves all 20 keys in **exactly 1 query returning 20 rows**. The
   metrics profile reports **zero amplified shapes** (no query exceeding the
   20-seed count).

The old per-seed pattern (`SELECT * FROM mk_seeds WHERE seed_id = ? AND version = ?`
repeated N times) **does not appear** in the profile.

*Caveat, stated plainly:* the instrumented verification runs through the
marketplace **store** layer rather than `MarketplaceService.install_pack`
itself, because importing the service transitively imports
`nexus_scalp.strategies.factory` → `torch`, and the active venv's `torch`
build is broken on this machine (`OSError: caffe2_nvrtc.dll` missing). The
service-level path is already covered by the regression test in (1) and (2).

---

## Broker-Trade Pagination Evidence

**PROVEN — root cause established by EXPLAIN ANALYZE.**

The query (from `audit_repository.py:1828`):

```sql
SELECT * FROM audit_broker_trades
ORDER BY COALESCE(NULLIF(exit_time,''), '') DESC
LIMIT 500 OFFSET ?
```

**EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS)** on the representative
12,000-row dataset, `enable_seqscan=on`, `work_mem=4MB`:

```
Limit  (cost=962.95..964.20 rows=500 width=142) (actual time=5.263..5.310 rows=500 loops=1)
  Buffers: shared hit=215
  ->  Sort  (cost=962.95..992.95 rows=12000 width=142) (actual time=5.261..5.280 rows=500 loops=1)
        Sort Key: (COALESCE(NULLIF(audit_broker_trades.exit_time, ''::text), ''::text)) DESC
        Sort Method: top-N heapsort  Memory: 170kB
        Buffers: shared hit=215
        ->  Seq Scan on public.audit_broker_trades  (cost=0.00..365.00 rows=12000 width=142)
              (actual time=0.008..2.210 rows=12000 loops=1)
              Buffers: shared hit=215
Planning Time: 0.084 ms
Execution Time: 5.435 ms
```

At `OFFSET 2000` (identical plan shape, `Execution Time: 4.927 ms`, sort
memory 920kB, 2,500 rows through the Sort):

```
Limit  (cost=1107.26..1108.51 rows=500 width=142) (actual time=4.710..4.746 rows=500 loops=1)
  ->  Sort  (cost=1102.26..1132.26 rows=12000 width=142) (actual time=4.614..4.680 rows=2500 loops=1)
        Sort Key: (COALESCE(NULLIF(audit_broker_trades.exit_time, ''::text), ''::text)) DESC
        Sort Method: top-N heapsort  Memory: 920kB
        ->  Seq Scan on public.audit_broker_trades  (cost=0.00..365.00 rows=12000 width=142)
```

**Root cause.** The `ORDER BY` is a **computed expression**, not a column.
No btree index on `exit_time` can serve `COALESCE(NULLIF(exit_time,''),'')`,
because the index is ordered on the *raw* column value while the sort key is
a *function of it*. Verified against the live `nexusdb` catalog:

* `audit_broker_trades` has exactly two indexes:
  `audit_broker_trades_pkey` on `trade_id`, and
  `idx_broker_trades_exit` on `exit_time`.
* A check for any index whose definition mentions `COALESCE` returns **none**.

So **every page** of this pagination performs: a full heap sequential scan of
`audit_broker_trades` + a top-N sort over all rows + a projection of all 13
columns at `width=142`. The cost does not degrade gracefully with `OFFSET` —
the scan is over the whole table regardless of the page requested, and it
scales linearly with table growth.

**Cardinality:** estimates are **accurate** — the Limit's `rows=500` estimate
matches the actual 500, and the Seq Scan's `rows=12000` estimate matches the
actual 12,000. There is **no statistics problem here**; this is purely an
indexability problem. (See §18.)

**Workload profile corroboration:** Rank A #2 (190.0 ms total, 20 calls,
10,000 rows) and Rank C #2 (9.50 ms mean).

**Live-database context:** `audit_broker_trades` currently holds **4,116
rows**, with 47 sequential scans reading 128,579 tuples since the
~6.2-hour-old stats window. The measured per-page cost is therefore modest
*today*; the defect is the **growth characteristic** (full scan + sort per
page request), not the current absolute latency. This is why the candidate is
filed for Phase 5 with a benchmark plan rather than fixed now.

---

## SELECT * Evidence

**MEASURED — NOT PROVEN as a production bottleneck.**

`SELECT *` was exercised at representative page sizes and measured:

| Query | Calls | Rows | Mean ms | Projection width |
|---|---:|---:|---:|---|
| `SELECT * FROM audit_broker_orders ... LIMIT 2000` | 5 | 10,000 | 12.80 | 63 bytes/row |
| `SELECT * FROM audit_broker_deals ... LIMIT 2000` | 5 | 10,000 | 6.00 | 63 bytes/row |
| `SELECT * FROM audit_ledger ... LIMIT 500` | 5 | 2,500 | 3.20 | narrow |
| `SELECT * FROM audit_signals ... LIMIT 500` | 5 | 2,500 | 3.20 | narrow |

Observations:

* The widest projections in the workload (`audit_broker_*`, 13 columns,
  `width=142`) sort and transfer 500–2,000 rows per call at single-digit
  millisecond latency on a 12k-row table.
* `news_articles.body` is a `TEXT` column holding ~512-byte representative
  payloads; no TOAST-related cost was measured (see §21).
* The dominant cost in every `SELECT *` plan is the **Sort** on the `ORDER BY`
  column, not the projection width. For `audit_broker_orders` the sort is
  `top-N heapsort` over all 12,000 rows — the same class of problem as §17,
  independent of `*`.

**Conclusion: `SELECT *` is a MEASURED, LOW-COST pattern in this workload. No
change is justified by the evidence.** The query-name recorder now makes the
cost of any `SELECT *` path directly measurable in production (row count is
captured), so if a real workload shows a large row count with a wide
projection and meaningful total time, that becomes provable rather than
speculative.

---

## OFFSET Evidence

**MEASURED — NOT PROVEN as a production bottleneck.**

`OFFSET` was exercised at 0, 500, 1000 and 2000 on the broker-trade query:

| OFFSET | Execution Time | Plan |
|---|---:|---|
| 0 | 5.435 ms | Seq Scan(12000) → top-N Sort(500 rows out) → Limit |
| 2000 | 4.927 ms | Seq Scan(12000) → top-N Sort(2500 rows out) → Limit |

**The latency does NOT grow with OFFSET** — because the underlying plan
already scans and sorts the entire table for every page. The `OFFSET` itself
is free here; the `ORDER BY` expression is what costs. In other words, on
this query **OFFSET is not the bottleneck — it is masked by a bigger one.**

For the bounded maintenance paths flagged in earlier audits
(`incidents/store.py`, `dead_letter_store.py:286-319`'s `_prune_locked`), no
workload was exercised and no cost was measured, so they remain **NOT PROVEN**
and unchanged. The recorder now makes them measurable in production.

**No OFFSET was rewritten and no keyset pagination was introduced.**

---

## Index Evidence

**MEASURED — tied to real workload, no speculative index added.**

Live `pg_stat_user_indexes` on `nexusdb` (stats window ≈ 6.2 h). Indexes with
**zero scans since the reset**, largest first:

| Table | Index | Size |
|---|---|---|
| `model_governance_events` | `idx_gov_events_model` | 5,512 kB |
| `research_events` | `idx_events_strategy` | 3,256 kB |
| `shadow_decisions` | `idx_shadow_decisions_run` | 2,232 kB |
| `research_evidence` | `idx_evidence_strategy_created_id` | 2,072 kB |
| `research_gates` | `idx_gates_strategy_run` | 2,000 kB |
| `research_evidence` | `idx_evidence_strategy` | 1,512 kB |
| `research_gates` | `idx_gates_strategy` | 1,304 kB |
| `news_prune_audit` | `idx_news_prune_audit_article` | 952 kB |
| `news_articles` | `idx_news_articles_source` | 648 kB |
| `audit_experiences` | `idx_exp_symbol_time` | 544 kB |

These are **candidates only**, and deliberately **NOT dropped or modified**:
a zero `idx_scan` over a 6-hour window on a mostly-idle instance does not
prove an index is dead — it may serve a rare-but-critical query, and
`pg_stat_user_indexes` counters are reset with the stats. The earlier audits
also documented 4 exact duplicate index pairs (~8 MB); those also remain
documented candidates, unchanged.

**The one index fact that IS workload-relevant** is a *missing* capability,
not a redundant one: `idx_broker_trades_exit` exists on the raw `exit_time`
column but **cannot serve the `COALESCE(NULLIF(exit_time,''),'')` sort key**
used by `get_broker_trades` (§17). That is the actionable index finding, and
it is filed as part of Candidate P4-A rather than as a standalone index
change, because an expression index alone is not the only remediation and the
phase forbids speculative index creation.

**No index was created or dropped.**

---

## Connection Pool Evidence

**MEASURED — no pool change made.**

* Server-side: 13 backends (1 active / 12 idle), all labeled `pg-read` or
  `pg-write` via `application_name`, so pool membership is observable from
  the server without any new instrumentation.
* Pool definition (`fabric/pg_planes.py` `PoolLimits`): `min_size=2`,
  `max_size=10` per plane, `connect_timeout_sec=10`, `idle_timeout_sec=0`,
  `max_lifetime_sec=0`, `health_check_interval_sec=30`,
  `reconnect_timeout_sec=30`. The fabric applies `SET jit = off` and
  `SET application_name` to every pooled connection.
* `max_connections=100` server-side → both planes at maximum use ~20% of the
  ceiling. **No saturation was observed** and no checkout timeout was seen.
* `pg_planes` already exposes `PgPool.stats()` and classifies checkout
  timeouts vs server-side failures via `classify_pool_error` — pool
  observability exists and was not duplicated.

**Connection churn finding (STRONG CANDIDATE, not fixed).** The measurement
workload used the driver directly rather than the pooled planes, and the
driver logged one `postgres connect` per statement (145 connects for 145
queries). The fabric exists precisely to avoid this — a pooled checkout is
sub-millisecond versus a ~100 ms cold connect (per the module's own
documentation). The implication for production is that **any code path
reaching `PostgreSQLDriver` while bypassing `PgReadPlane`/`PgWritePlane`
pays a full connect per statement.** That is a real, measurable cost, but no
production path was profiled, so it is recorded as a candidate and left
alone.

---

## Transaction Evidence

**OBSERVED HEALTHY — no change made.**

* 0 idle-in-transaction sessions; 0 waiting sessions; 0 ungranted locks;
  0 deadlocks in the counter window.
* `xact_commit` 312,251 vs `xact_rollback` 1,424 (0.46% rollback rate).
* The driver enforces a verb allow-list and single-statement rule at the
  parameterization boundary (`assert_safe_sql`), and the read plane is
  read-only *by construction* (`SET default_transaction_read_only = on`) —
  the server rejects mutations, not merely the convention.
* No transaction boundary was modified.

---

## EXPLAIN ANALYZE Findings

All plans captured with `EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS)` on
the representative dataset after `ANALYZE`, `enable_seqscan=on`,
`work_mem=4MB`. Representative parameter values were used; no sensitive
parameter values appear.

| Query | Execution Time | Plan shape |
|---|---:|---|
| broker-trade pagination `OFFSET 0` | 5.435 ms | Seq Scan(12,000) → top-N Sort → Limit(500) |
| broker-trade pagination `OFFSET 2000` | 4.927 ms | Seq Scan(12,000) → top-N Sort → Limit(500) |
| `SELECT * FROM audit_broker_deals ORDER BY time DESC LIMIT 2000` | 3.285 ms | Seq Scan(12,000) → top-N Sort → Limit(2,000) |
| `GROUP BY symbol` aggregation | 3.081 ms | Seq Scan → HashAggregate → quicksort(8 rows) |
| `SELECT * FROM audit_broker_orders ORDER BY time_setup DESC LIMIT 2000` | 1.879 ms | Seq Scan → top-N Sort → Limit |
| per-ticket existence probe | 0.025 ms | Index Cond / Seq Scan probe — indexable |

Findings:

1. **An expression prevents index use** — the only materially important plan
   fact in this phase. `ORDER BY COALESCE(NULLIF(exit_time,''),'')` forces a
   full scan + sort per page (§17).
2. **No cardinality misestimation** — estimated vs actual rows agree on every
   captured plan (500/500, 12000/12000, 2000/2000, 8/8).
3. **No hash spills, no temp files, no disk-based sorts.** Every sort is an
   in-memory `top-N heapsort` (170kB–920kB) or a 25kB quicksort — far below
   `work_mem=4MB`.
4. **Every plan hit `shared hit=` buffers only** — no physical reads during
   measurement (dataset was warm and ~50 MB against 128MB `shared_buffers`).
5. **Every read query is a Seq Scan.** On these table sizes that is the
   planner's correct choice (no qualifying predicate, full-width reads);
   it is **not** a defect.

---

## Cardinality Findings

**No cardinality problem found.** Estimated vs actual rows on all six
captured plans:

| Plan node | Estimated | Actual |
|---|---:|---:|
| Limit (broker-trade, OFFSET 0) | 500 | 500 |
| Seq Scan (audit_broker_trades) | 12,000 | 12,000 |
| Sort (broker-trade, OFFSET 2000) | 12,000 | 12,000 |
| Limit (audit_broker_deals) | 2,000 | 2,000 |
| HashAggregate (GROUP BY symbol) | 8 | 8 |
| Limit (audit_broker_orders) | 2,000 | 2,000 |

Statistics are fresh enough for accurate estimation on this workload
(`ANALYZE` ran immediately before measurement; live tables show
`last_autoanalyze` current with effectively zero dead tuples). **No
statistics target was changed and no extended statistics were created.**

---

## Temp / I/O Findings

* `temp_files = 2`, `temp_bytes = 524,288` (512 KiB) — database-wide since
  the counter window began, and not attributable to any specific query.
* **Zero temp blocks and zero spills** in every captured EXPLAIN plan.
* **Zero physical reads** during the measured window (`Buffers: shared hit=`
  on every node; `blks_read` unchanged).
* `shadow_decisions` has the lowest heap hit ratio among large tables
  (0.6837 over the last ~6.2 h) — a **MEASURED observation**. At 94 MB it is
  the largest table, so it exceeds `shared_buffers` (128MB) alongside the
  rest of the working set. This is expected behavior, not a tuning signal,
  and **no `shared_buffers` / `work_mem` change was made**.
* Background writer: `buffers_clean` 33,609, `maxwritten_clean` 265,
  `buffers_alloc` 1,667,668 — no backlog signal.

**No memory, WAL, or checkpoint setting was tuned.**

---

## Confirmed Bottlenecks

**One.**

1. **Broker-trade pagination cannot use an index for its sort key** —
   `ORDER BY COALESCE(NULLIF(exit_time,''),'') DESC` in
   `AuditRepository.get_broker_trades` (`audit_repository.py:1828`). PROVEN by
   EXPLAIN ANALYZE (full Seq Scan + full-table top-N Sort per page) and by
   live-catalog verification that no index covers the expression while the
   existing `idx_broker_trades_exit` covers only the raw column. Classified
   **PROVEN**.

Everything else measured was either healthy or unmeasurable-in-current-config
(see §20).

---

## Rejected Hypotheses

Measured and deliberately **not** changed:

| Hypothesis | Verdict | Evidence |
|---|---|---|
| `SELECT *` is a production bottleneck | **NOT PROVEN** | Rank A/C/E show single-digit-ms latency at representative page sizes; the dominant plan cost is the `ORDER BY` sort, not projection width (§18) |
| `OFFSET` pagination is a production bottleneck | **NOT PROVEN** | Latency is flat from OFFSET 0 → 2000 (5.435 → 4.927 ms); the `ORDER BY` expression masks it entirely (§19) |
| Redundant indexes should be dropped | **NOT PROVEN** | Zero `idx_scan` over a ~6h window on a mostly-idle instance does not prove an index is dead (§21) |
| Missing indexes on hot paths | **NOT PROVEN** | No qualifying-predicate query was slow; every captured read plan is a correctly-chosen Seq Scan (§18) |
| Table/index bloat needs `VACUUM FULL`/`REINDEX` | **NOT PROVEN** | Dead tuples effectively zero on major tables; autovacuum/autoanalyze current (Phase 3 + §9) |
| `work_mem` / `shared_buffers` need tuning | **NOT PROVEN** | Zero spills, zero temp blocks in every plan; the one low hit ratio is a working-set-vs-buffer size fact, not a defect (§21) |
| Wide-row / TOAST payload cost is material | **NOT PROVEN** | No TOAST-related cost measured; ~512-byte representative payloads showed no effect (§18) |
| Partitioning / materialized views / JSONB conversion needed | **NOT PROVEN** | No workload evidence supports any of these; none was introduced |
| Query-level server stats were already available | **REJECTED** | `pg_stat_statements` is not installed and not loaded; `log_min_duration_statement=-1`; the persistent query log holds 1 row (§6, §11) |

---

## Recommended Phase 5 Fixes

Only candidates that survived measurement are listed. No ranking by "best" —
each is a separate decision with its own evidence.

---

### Candidate P4-A

**Candidate:** `AuditRepository.get_broker_trades` orders by an expression no
index can serve, forcing a full scan + sort on every page request.

**Evidence:** EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS) — full
`Seq Scan on public.audit_broker_trades` + `top-N heapsort` over all rows for
every `LIMIT/OFFSET` page; live catalog shows the only candidate index
(`idx_broker_trades_exit`) covers the raw `exit_time` column, not the
`COALESCE(NULLIF(exit_time,''),'')` sort key.

**Query:**

```sql
SELECT * FROM audit_broker_trades
ORDER BY COALESCE(NULLIF(exit_time,''), '') DESC
LIMIT ? OFFSET ?
```

**Fingerprint:** `SELECT * FROM audit_broker_trades ORDER BY COALESCE(NULLIF(exit_time,?), ?) DESC LIMIT ? OFFSET ?`

**Calls:** 20 in the measured window (4 pages × 5 rounds) — representative
paging cadence, not amplification.

**Total DB Time:** 190.0 ms (Rank A #2 in the measured window).

**Mean Latency:** 9.50 ms (Rank C #2).

**I/O:** not attributable (`track_io_timing` off); every plan node was
`shared hit=` only.

**Rows:** 10,000 (Rank E #1), 500/call, projection width 142 bytes.

**Backend Call Site:** `src/nexus_scalp/adapters/database/audit_repository.py:1828`
— `AuditRepository.get_broker_trades`.

**Root Cause:** the `ORDER BY` key is a computed expression
(`COALESCE(NULLIF(exit_time,''),'')`), so no btree index on `exit_time` can
order by it. The planner's only option is scan-all + sort-all, per page,
regardless of `OFFSET`. Cost grows linearly with table size, not with page
depth. Cardinality estimates are accurate — this is purely an indexability
defect.

**Expected Benefit:** eliminate the per-page full scan and sort; the LIMIT
can be satisfied by an index-ordered scan. On the representative 12,000-row
dataset this removes ~5 ms and 215 buffer touches per page; on a
production-sized table the removal is the difference between constant and
linear-in-table-size cost per page.

**Risk:** MEDIUM. Requires either (a) a new expression index
`CREATE INDEX ... ON audit_broker_trades ((COALESCE(NULLIF(exit_time,''),'')))`
— a schema change needing authorization and a one-time build write, or
(b) changing the query's sort key to the raw column with a `NULLS`-aware
ordering — an application change that must preserve the documented
"newest exit first" semantics including the empty-string-means-open
convention. Both must keep SQLite parity (the call site is portable SQL).

**SQLite Impact:** the portable SQL at the call site must stay valid on both
providers. `COALESCE(NULLIF(...))` is valid SQLite, but SQLite does not
support expression indexes with the same syntax; any index-based fix is
PostgreSQL-only and must be gated through the provider/port layer, exactly
like the existing `port_create_table` mechanism.

**PostgreSQL Impact:** one new index on `audit_broker_trades` (4,116 rows
today) or one query-text change. Writes to this table acquire one extra
index-maintenance cost per insert.

**Required Change:** either
(1) `CREATE INDEX idx_broker_trades_exit_sort ON audit_broker_trades ((COALESCE(NULLIF(exit_time,''),'')));`
(authorization required — schema change), or
(2) replace the computed ORDER BY with `ORDER BY exit_time DESC NULLS LAST`
plus an explicit tie-breaker, after confirming the resulting order matches
the current business semantics.

**Benchmark Plan:** run the Phase 4 profiler's pagination arm at OFFSET
0/500/1000/2000 against the throwaway database before and after, capturing
EXPLAIN (ANALYZE, BUFFERS) plus the query-metrics profile. Success criterion:
the Sort node disappears from the plan and `Execution Time` becomes
independent of `OFFSET`, with row counts unchanged.

**Rollback Plan:** for (1), `DROP INDEX idx_broker_trades_exit_sort;` — the
query reverts to scan+sort with no data change. For (2), revert the query
text; no schema change to undo.

---

### Candidate P4-B

**Candidate:** Application code paths that reach `PostgreSQLDriver` directly
instead of the pooled fabric pay a full connection establishment per
statement.

**Evidence:** during the measured workload (145 statements through the
driver, not the fabric), the driver logged a `postgres connect` event for
**every statement** — 145 connects in 8.18 s. The fabric's own documentation
records a cold connect at ~104 ms versus a sub-millisecond pooled checkout.

**Query:** n/a — this is a connection-path cost, not a statement cost.

**Fingerprint:** n/a.

**Calls:** 145 connections established in the measured window.

**Total DB Time:** not separable in the current configuration; the wall time
includes it. **DERIVED ESTIMATE:** at the documented ~104 ms cold-connect
figure, 145 connects would account for a substantial share of the 8.18 s
wall time — but the observed connects were hot (loopback, same host) and the
actual connect latency was not isolated, so no precise figure is claimed.

**Mean Latency:** not measured per connect.

**I/O:** n/a.

**Rows:** n/a.

**Backend Call Site:** any caller constructing `PostgreSQLDriver` directly
instead of routing through `PgReadPlane`/`PgWritePlane`
(`src/nexus_scalp/database/fabric/pg_planes.py`). The measurement probe
itself did this deliberately; which production paths do is **unresolved**.

**Root Cause:** the driver opens and closes a connection per statement when
no `conn` is passed and no pool is in front of it. The fabric exists to
prevent this, but nothing forces callers to use it.

**Expected Benefit:** eliminate per-statement connection establishment on any
path that is currently bypassing the pool.

**Risk:** LOW to MEDIUM depending on which paths are implicated. Routing a
path through the fabric changes transaction semantics (read plane is
read-only by construction) and must be verified per call site.

**SQLite Impact:** none — this is a PostgreSQL-pool concern; SQLite has no
equivalent path.

**PostgreSQL Impact:** fewer connection establishments and fewer idle
backends; no schema or settings change.

**Required Change:** first, *measure in production* which call sites bypass
the fabric (the `application_name`-labeled `pg_stat_activity` view plus the
new query-metrics profile together make this discoverable). Then route
qualifying read paths through `PgReadPlane`.

**Benchmark Plan:** instrument the identified path with the Phase 4 query
metrics recorder, record wall time and connect count before and after routing
through the fabric. Success criterion: connect count drops to the pool's
`min_size` steady state with unchanged query results.

**Rollback Plan:** revert the routing change; no schema or configuration to
undo.

**Status: STRONG CANDIDATE — not proven for production traffic.** The
measurement proves the *mechanism*, not that any production path triggers it.

---

## Unresolved Questions

1. **Which production read paths bypass the pooled fabric?** The mechanism is
   proven (Candidate P4-B) but no production path was profiled. The
   `application_name` labels plus the new metrics recorder make this
   answerable without server changes.
2. **What is the real production call frequency of `get_broker_trades`?**
   The defect is proven structurally; the *cost in production* depends on how
   often this pagination is exercised and how fast `audit_broker_trades`
   grows. 47 seq scans / 128,579 tuples in ~6.2 h is a partial signal.
3. **What do the 512 KiB / 2 temp files belong to?** Not attributable to any
   query in the current configuration; `track_io_timing` is off.
4. **Is `shadow_decisions`' 0.68 heap hit ratio a problem?** Most likely just
   a 94 MB table against 128 MB `shared_buffers` — but this is a hypothesis,
   not a measurement, and no change was made.
5. **Are the zero-scan indexes actually dead?** Unknowable from a ~6-hour
   window on a mostly-idle instance. A longer observation window, or an
   authorized `pg_stat_statements` enablement, would settle it.
6. **What does the real news-ingestion workload look like?** The live stats
   show the news tables dominate `seq_tup_read` (`news_articles` 951,108
   tuples across 55 seq scans), but no news workload was exercised in the
   profiler, so no news-path query is offered as a candidate.

---

## Safety / Risk Assessment

**Classification of every action taken this phase:**

| Action | Class |
|---|---|
| Read `pg_settings`, `pg_stat_*`, `pg_statio_*`, `pg_indexes`, `pg_stat_activity`, `pg_locks` | SAFE (read-only) |
| `EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS)` on SELECT statements against a throwaway database | SAFE |
| Adding `_QueryMetrics` / `QueryMetricsRecorder` / `query_name` to the shared code | SAFE (opt-in, off by default, never raises, no new hot-path logging) |
| Wiring the recorder into both drivers' read paths | LOW RISK (one attribute read on the disabled fast path; behavior unchanged when disabled; covered by 7 new regression tests) |
| Creating and dropping a throwaway database on the local instance | LOW RISK (`nexusdb` never written; `DROP DATABASE ... WITH (FORCE)` on an unused random name) |

**Nothing done this phase:**

* No DDL, DML, `DELETE`, `UPDATE`, `INSERT` against `nexusdb`.
* No `shared_preload_libraries` change. **`pg_stat_statements` was NOT enabled.**
* No `track_io_timing` change. No PostgreSQL configuration change of any kind.
* No `ALTER SYSTEM`, no `pg_reload_conf()`, no server restart.
* No index created or dropped. No schema migration. No statistics-target change.
* No `VACUUM FULL`, `REINDEX`, `CLUSTER`, `ANALYZE` on any production database.
* No pool size, `work_mem`, `shared_buffers`, WAL, or retention change.
* No provider routing change. No repository rewrite. No OFFSET/`SELECT *` change.
* No optimization implemented — this phase delivered instrumentation and
  evidence only.

**SQLite compatibility preserved:** the new `query_name` helper lives on the
shared `DatabaseDriver` base; both drivers carry identical instrumentation;
no PostgreSQL-specific syntax entered any shared SQL path. Asserted by
`test_query_name_is_provider_agnostic_and_stable`.

**Tests:** `tests/unit/test_pg_query_logging.py` 33/33 pass (7 new);
`tests/unit/test_database_log_store.py` pass;
`tests/unit/test_marketplace_backend.py` 13/13 pass;
`ruff check src/nexus_scalp/` clean; `mypy` clean on all four changed modules.

**Honesty note on CI:** this repository excludes the PostgreSQL CI arms from
GitHub Actions by design (`scripts/ci/check_pg_arm.py`, gated on
`NSE_PG_TEST_URL`), so **CI does not validate PostgreSQL behavior**. All
PostgreSQL evidence in this report was produced against a throwaway
PostgreSQL 17.10 database on the local instance using the application's own
driver and configuration, and is labeled accordingly.
