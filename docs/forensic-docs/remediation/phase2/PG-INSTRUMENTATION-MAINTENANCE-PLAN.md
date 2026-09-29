# PostgreSQL Instrumentation — Maintenance & Rollback Plan

**Lane:** `agent/hermes/remediation-instrumentation`
**Base:** `9d035f3a` (origin/main, includes #567)
**Contract ref:** Part 4 §6 (query statistics), §13 (query plan protocol), §15 (hot-path
latency contract p50/p95/p99), §53 (before/after table), §56 (total-path budget)
**Status:** DRAFT — not executed. Awaiting explicit authorization for the restart step.

## 0. Purpose

Two instrumentation layers were authorized. This document is the **PostgreSQL-native**
layer. The driver-boundary layer is implemented separately in
`src/nexus_scalp/database/query_logging.py` and answers a different question
(which repository operation, which code path). Neither replaces the other.

The driver layer cannot supply: per-statement execution time, calls, rows, shared
blocks hit/read, temp blocks, or WAL-related statistics. That attribution is only
available server-side.

## 1. Current PostgreSQL state (measured 2026-09-29, read-only)

Collected by `scripts/db_remediation/pg_instrumentation_probe.py`
(lane: `agent/hermes/remediation-instrumentation`).

| Setting | Value | Context | Restart required? |
|---|---|---|---|
| `server_version` | 17.10 | internal | n/a |
| `config_file` | `C:/Program Files/PostgreSQL/17/data/postgresql.conf` | postmaster | n/a |
| `data_directory` | `C:/Program Files/PostgreSQL/17/data` | postmaster | n/a |
| `shared_preload_libraries` | *(unset — empty)* | postmaster | **YES** |
| `track_io_timing` | `off` | superuser | **NO** |
| `track_wal_io_timing` | `off` | superuser | **NO** |
| `log_min_duration_statement` | `-1` (disabled) | superuser | **NO** |
| `log_statement` | `none` | superuser | **NO** |
| `compute_query_id` | `auto` | superuser | **NO** |
| `track_counts` | `on` | superuser | **NO** |
| `autovacuum` | `on` | sighup | **NO** |
| `shared_buffers` | 128 MB (16384 x 8kB) | postmaster | yes |
| `effective_cache_size` | 4 GB (524288 x 8kB) | user | no |
| `work_mem` | 4 MB | user | no |
| `maintenance_work_mem` | 64 MB | user | no |
| `max_connections` | 100 | postmaster | yes |

Installed extensions: `plpgsql` only.

**Available to install (present in the server build, default_version):**

| Extension | default_version | installed | Use for |
|---|---|---|---|
| `pg_stat_statements` | **1.11** | no | §6 per-statement stats: calls, total/mean time, rows, shared blocks, temp blocks, WAL |
| `pgstattuple` | 1.5 | no | §34/§48 real bloat measurement (dead tuple ratio per table) |
| `pg_buffercache` | 1.5 | no | buffer-pool inspection (optional, low priority) |

### 1.1 The important discovery: the plan splits in half

Four of the six needed settings are **`superuser` context with no restart required**.
Only `shared_preload_libraries` requires a restart. Therefore the plan is executed
in two stages with very different risk profiles:

- **Stage 1 — zero downtime, zero restart.** `track_io_timing`, `track_wal_io_timing`,
  `log_min_duration_statement`, `log_statement`. Applied by `ALTER SYSTEM` + `SELECT
  pg_reload_conf()`. Live, reversible in seconds, no connection loss.
- **Stage 2 — one restart in a maintenance window.** `shared_preload_libraries` +
  `CREATE EXTENSION pg_stat_statements`. Required for the per-statement table.

This is a materially smaller blast radius than "restart PostgreSQL to get query
statistics."

### 1.2 Environment constraints (measured)

- Current process is **not an administrator** (`net session` denied). Service control
  (`sc` / `net stop|start`) will fail without elevation.
- `C:/Program Files/PostgreSQL/17/data/postgresql.conf` **is writable** by this user.
- `pg_ctl.exe` exists at `C:/Program Files/PostgreSQL/17/bin/pg_ctl.exe` but is
  **not on PATH**. A restart must therefore go through `pg_ctl` with an explicit
  path, or through an elevated service control command the operator runs.
- The service name is `postgresql-x64-17` (WIN32_OWN_PROCESS), currently running.
- The PostgreSQL cluster is a **local single-instance dev/ops machine**, not a
  managed cloud database. There is no replica and no HA layer to fail over to.

**Consequence:** the restart in Stage 2 must either (a) be executed by the operator
with elevation, or (b) be executed by this agent via `pg_ctl` if the account is
granted the privilege. This is the one step that cannot be automated away.

## 2. Planned change

### Stage 1 — no restart (proposed, not applied)

```sql
ALTER SYSTEM SET track_io_timing = on;
ALTER SYSTEM SET track_wal_io_timing = on;
ALTER SYSTEM SET log_min_duration_statement = 500;   -- ms; 0.5s slow-query floor
ALTER SYSTEM SET log_statement = 'none';              -- keep as-is; duration log is enough
SELECT pg_reload_conf();
```

Rationale:
- `track_io_timing = on` is required for `EXPLAIN (ANALYZE, BUFFERS)` to report
  actual read/write time per node (§13) and for `pg_stat_statements` block timings.
  Historically discouraged for overhead; on modern hardware the cost is negligible
  and the Phase-1 baseline has no I/O timing at all.
- `track_wal_io_timing = on` supplies the WAL column §6 asks for "where available."
- `log_min_duration_statement = 500` gives an **application-independent** slow-query
  record as a cross-check on the driver-boundary aggregator. 500 ms is chosen as a
  floor that captures pathological queries (the Phase-1 hot paths are full-table
  scans, which are the ones that will trip it) without flooding the log at the
  current high call volume. Deliberately NOT set to 0: §77 forbids turning
  observability into a high-volume workload, and a 0 threshold logs every statement.

### Stage 2 — restart in maintenance window (proposed, not applied)

```ini
# postgresql.conf
shared_preload_libraries = 'pg_stat_statements'
```

```sql
-- after restart, in the target database
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
ALTER SYSTEM SET "pg_stat_statements.max" = 20000;
ALTER SYSTEM SET "pg_stat_statements.track" = 'top';
SELECT pg_reload_conf();
```

`track = top` (not `all`): nested statements are not tracked. This bounds the
statement-count growth §77 is concerned about and matches a workload where the
driver layer already attributes the call site. `max = 20000` bounds shared memory
for the hash; the default 5000 is too small for a 126-table / 281-index workload
that accumulates many normalized shapes.

## 3. Restart procedure (Stage 2)

Ordered, with verification at each step.

```text
1.  Confirm no long-running transactions:
      SELECT count(*) FROM pg_stat_activity WHERE state <> 'idle';
2.  Confirm no active replication slots / hot standbys:
      SELECT * FROM pg_replication_slots;        -- expect empty on this instance
3.  Notify: application connections will be dropped. Expected outage is the
    PostgreSQL startup time only (single local instance, ~5-15 s).
4.  Checkpoint for a fast, clean restart:
      SELECT pg_checkpoint();                     -- or: CHECKPOINT;
5.  Stop:
      "C:/Program Files/PostgreSQL/17/bin/pg_ctl.exe" stop \
        -D "C:/Program Files/PostgreSQL/17/data" -m fast
    (or, elevated:  net stop postgresql-x64-17)
6.  Verify stopped:  pg_ctl status  ->  "pg_ctl: no server running"
7.  Start:
      "C:/Program Files/PostgreSQL/17/bin/pg_ctl.exe" start \
        -D "C:/Program Files/PostgreSQL/17/data" -w
    (or, elevated:  net start postgresql-x64-17)
8.  Verify up:   SELECT version(); SELECT now(); SELECT postmaster_start_time();
9.  Verify preload took effect:
      SELECT setting FROM pg_settings WHERE name = 'shared_preload_libraries';
      -- expect 'pg_stat_statements'
10. Create the extension in nexusdb:
      CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
11. Verify the view exists and is collecting:
      SELECT count(*) FROM pg_stat_statements;   -- expect > 0 after first query
12. Record the activation timestamp. This is the T0 for all future §53
    before/after comparisons. Counters in pg_stat_statements reset on
    extension creation, so every "before" number must be read after T0.
```

## 4. Rollback procedure

Rollback is exact and verified at each stage.

### Rollback Stage 1 (no restart)

```sql
ALTER SYSTEM SET track_io_timing = off;
ALTER SYSTEM SET track_wal_io_timing = off;
ALTER SYSTEM SET log_min_duration_statement = -1;
SELECT pg_reload_conf();
```

`ALTER SYSTEM` writes to `postgresql.auto.conf`. Verification:

```sql
SELECT name, setting, source, pending_restart FROM pg_settings
WHERE name IN ('track_io_timing','track_wal_io_timing','log_min_duration_statement');
```

If `postgresql.auto.conf` is somehow unreadable or corrupt, delete the three lines
from it and reload — `postgresql.conf` values remain the source of truth.

### Rollback Stage 2 (restart required)

```sql
DROP EXTENSION IF EXISTS pg_stat_statements;
```

```ini
# remove the line, leaving shared_preload_libraries unset (its original state)
# shared_preload_libraries = 'pg_stat_statements'
```

Then restart (procedure §3 steps 3-8). After rollback:

```sql
SELECT setting FROM pg_settings WHERE name = 'shared_preload_libraries';  -- expect ''
SELECT * FROM pg_extension WHERE extname = 'pg_stat_statements';          -- expect no rows
```

The original state of `shared_preload_libraries` was **empty/unset**, so rollback is
to a known-good baseline, not to a guessed value.

## 5. Verification (post-activation)

```sql
-- Instrumentation is live
SELECT extname, extversion FROM pg_extension WHERE extname = 'pg_stat_statements';
SELECT name, setting FROM pg_settings
WHERE name LIKE 'pg_stat_statements.%' OR name = 'track_io_timing';

-- §6 top statements by total time
SELECT queryid, calls, round(total_exec_time::numeric, 2) AS total_ms,
       round(mean_exec_time::numeric, 4) AS mean_ms, rows,
       shared_blks_hit, shared_blks_read, temp_blks_read, temp_blks_written
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;
```

The collected evidence artifact is written by
`scripts/db_remediation/pg_stat_statements_collect.py` (to be implemented in this
lane) into `docs/forensic-docs/remediation/phase2/`.

## 6. Expected operational impact

- **Stage 1:** none to queries. Adds I/O timing collection and a slow-query log at
  a 500 ms floor. Log volume grows only by the number of statements exceeding
  500 ms; given the Phase-1 baseline (repeated full-table scans) this is expected
  to be small in statement count but high in per-statement diagnostic value.
  `track_io_timing` overhead is a per-I/O timing call; on NVMe-class storage this
  is sub-microsecond and does not register against the measured hot paths.
- **Stage 2:** one restart, seconds of downtime. No data loss (fast shutdown +
  checkpoint first). `pg_stat_statements` consumes a bounded hash table in shared
  memory (~20000 entries) — single-digit MB at `max = 20000`. Sampling overhead is
  negligible; this extension is standard on production PostgreSQL.
- **Application:** the NSE engine is **not currently running** on this host
  (verified: no listener on the NSE ports). There are no application connections to
  drop. This makes the present window unusually safe for a restart — but the plan
  is written to be safe with connections present too.
- **No silent fallback (§75):** enabling instrumentation does not change the
  provider or any failover path. Provider remains `postgresql` (F-011).

## 7. What this does NOT give us

Stated plainly, because §6 forbids implying more coverage than exists:

- It does not give per-**transaction** attribution; statements are aggregated by
  normalized query shape. The driver-boundary layer supplies the
  repository/operation attribution.
- `pg_stat_statements` does not record a **plan**. §13's plan capture still
  requires `EXPLAIN (ANALYZE, BUFFERS)` run explicitly (Stage 1 makes those
  timings accurate).
- It does not give p50/p95/p99. It gives mean, min, max per statement shape.
  Percentiles come from the driver-boundary reservoir (§15).
- It does not retroactively measure the pre-activation period. The Phase-1
  baseline's 30.5 B `seq_tup_read` remains the only pre-instrumentation evidence,
  and it remains cumulative-since-stats-reset.

## 8. Explicit request

Stage 1 can be applied now with **no restart and no risk** — it is `superuser`
context plus `pg_reload_conf()`.

Stage 2 requires a restart. I am not an administrator on this host, so service
control will fail; `postgresql.conf` is writable and `pg_ctl.exe` is present, so
either path is viable **if authorized**.

Requested:
1. Authorization to execute Stage 1 immediately (no restart).
2. Authorization to execute Stage 2, and confirmation of which restart path to use:
   `pg_ctl` run by this agent, or an elevated `net stop`/`net start` run by the
   operator.
3. Or: direction to skip Stage 2 and rely on the driver-boundary layer plus
   Stage 1's slow-query log only.
