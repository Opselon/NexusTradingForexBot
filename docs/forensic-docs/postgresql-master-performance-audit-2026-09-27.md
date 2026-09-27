# NSE PostgreSQL Master Performance Audit — 2026-09-27

Scope: read-only forensic audit of the active NSE PostgreSQL provider and backend source. No application code, PostgreSQL configuration, provider routing, schema, index, table, or data was changed.

Evidence status:

- VERIFIED means returned by live PostgreSQL catalog/statistics queries or directly observed in source.
- SOURCE PATTERN means code contains a possible cost pattern; it is not claimed to be a runtime bottleneck without workload evidence.
- NOT MEASURABLE means the current installation lacks the required observability or historical baseline.

## Executive Summary

The active provider is confirmed as PostgreSQL 17.10, database `nexusdb`, on localhost:5432. The database is 525,211,315 bytes (501 MB), with 125 public tables and 445 indexes. Current health is stable: autovacuum and autoanalyze are current on the largest tables, dead tuples are effectively zero, no blocked sessions were observed, and the captured cache hit ratio is approximately 98.2% using the latest counters (10,049,917 hits / 10,233,297 total blocks).

The largest measurable storage cost is legitimate wide historical/event data, not demonstrated bloat. `shadow_decisions` is the largest relation at 94 MB total, followed by `news_articles` at 79 MB, `news_analysis` at 45 MB, `model_governance_events` at 40 MB, `research_gates` at 29 MB, `research_evidence` at 24 MB, and `research_events` at 23 MB.

The most important investigation limitation remains query observability. `pg_stat_statements` is absent, `shared_preload_libraries` is empty, and `track_io_timing` is off. Therefore no honest production ranking of query latency, total DB time, calls, temp blocks, WAL by statement, or backend query attribution can be produced. The report does not fabricate those values.

The strongest evidence-backed findings are:

1. Storage is payload-driven: large TEXT/serialized payloads and TOAST account for substantial space in news, research, strategy, audit, and shadow tables.
2. Four exact duplicate index pairs exist and consume approximately 8 MB in total. They are controlled migration candidates, not automatic drops.
3. Source contains `SELECT *` and OFFSET pagination in audit/accounting/reporting paths. These are review candidates, not proven bottlenecks.
4. `audit_repository.py` has a bounded queue writer using batches up to 500 and `executemany`, which is a positive baseline.
5. Application-level PostgreSQL pooling was not found in the inspected provider path. `PostgreSQLDriver.connect()` calls `psycopg.connect()` directly, while the base/proxy driver caches one connection in some paths. Pool min/max/waiter metrics are therefore NOT MEASURABLE from the current implementation and must not be inferred from `pooling_enabled=True`.
6. WAL counters show 8,575,023,513 bytes since the WAL statistics reset on 2026-09-24, but current database size and reset age do not establish a write-amplification problem. A workload-normalized write-rate measurement is required.

## Verified Environment

| Field | Value | Evidence |
| --- | --- | --- |
| Database | `nexusdb` | `current_database()` |
| User | `postgres` | `current_user` |
| Schema | `public` | `current_schema()` |
| Server address | `::1` | `inet_server_addr()` |
| Server port | 5432 | `inet_server_port()` |
| PostgreSQL | 17.10 | `current_setting('server_version')` |
| Version number | 170010 | `server_version_num` |
| Time zone | `Asia/Tehran` | `TimeZone` |
| Encoding | UTF8 | `server_encoding`, database encoding |
| Collation / ctype | `English_United States.1252` | `pg_database.datcollate/datctype` |
| Active provider | PostgreSQL | `load_database_config('audit')` |
| Password handling | secret-store resolution | application configuration path; secret not printed |

The connection was made through the application configuration using `load_database_config('audit')` and `build_postgres_url()`. No DSN containing a password appears in this report.

Additional identity/operational fields not yet captured in the read-only pass are PostgreSQL postmaster start time, observer role privilege membership (`pg_monitor`/`pg_read_all_stats`), replication role/status, replication slots, and archive status. Their absence is an evidence gap, not a claim that replication or archiving is absent.

## PostgreSQL Health

### Connections and transactions

| Metric | Observed |
| --- | ---: |
| `max_connections` | 100 |
| Client backends during probe | 1 |
| Client idle sessions | 0 observed |
| Idle-in-transaction sessions | 0 observed |
| Active transaction age | probe transaction only; effectively 0 seconds |
| Deadlocks | 0 in `pg_stat_database` |
| Commits since stats reset | 183,075 |
| Rollbacks since stats reset | 1,402 |
| Rollback fraction | approximately 0.76% of commit+rollback counters |
| Longest client transaction | none observed |

The rollback fraction is a counter ratio, not proof of failed business transactions; PostgreSQL/application rollback semantics require request-level attribution.

### Locks

No ungranted locks or blocker/blocked pairs were observed. The only observed locks belonged to the read-only probe and were granted `AccessShareLock`/catalog locks. This is a point-in-time observation, not proof that no lock waits occur under production workload.

### Database I/O and cache

`pg_stat_database` latest counters:

- blocks read: 183,380
- blocks hit: 10,049,917
- calculated hit ratio: approximately 98.21%
- temp files: 2
- temp bytes: 524,288
- block read/write timing: 0.0 because `track_io_timing=off`
- idle-in-transaction time: 1,742.6 seconds in the cumulative statistics view

The current session snapshot showed no active temp spill. Two historical temp files totaling 512 KiB do not identify a query or prove memory pressure.

### WAL and checkpoint pressure

`pg_stat_wal`:

- WAL records: 14,513,717
- full-page images: 1,519,840
- WAL bytes: 8,575,023,513
- WAL buffers full: 27,248
- WAL writes: 160,914
- WAL syncs: 0
- WAL statistics reset: 2026-09-24 16:07:19 +03:30

`pg_stat_bgwriter` exposed aggregate background-writer counters but not a complete checkpoint timing/history series in this PostgreSQL 17 installation. Relevant settings are `checkpoint_timeout=300s`, `checkpoint_completion_target=0.9`, `max_wal_size=1024MB`, `min_wal_size=80MB`, `fsync=on`, `synchronous_commit=on`, and `wal_compression=off`.

WAL bytes divided by current database size is not a valid write-amplification metric: the counters cover a different time interval, include updates/full-page images, and the current database may contain data created before the reset. No WAL setting change is recommended from this evidence.

## Database Size

- Database size: 525,211,315 bytes / 501 MB.
- Public tables: 125.
- Indexes: 445.
- Sequences: 62.
- Views: 0.
- Materialized views: 0.
- Extensions: `plpgsql` only.

The absence of views/materialized views is not a defect. No repeated expensive analytical query has been measured that would justify introducing either.

## Table Storage Ranking

| Table | Rows | Heap | Index | TOAST | Total | Dead | Growth | Cause |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `shadow_decisions` | 55,342 | 86 MB | 7.1 MB | 56 kB | 94 MB | 0 | Not measurable | Wide shadow decision payloads; sampled tuple ~1,560 B; payload text total ~108 MB |
| `news_articles` | 24,939 | 17 MB | 6.7 MB | 55 MB | 79 MB | 19 | Not measurable | Article body/summary/serialized metadata |
| `news_analysis` | 20,939 | 16 MB | 3.1 MB | 26 MB | 45 MB | 0 | Not measurable | Serialized analysis fields and repeated analysis history |
| `model_governance_events` | 59,166 | 27 MB | 13 MB | 40 kB | 40 MB | 0 | Not measurable | Append-like governance events and three large indexes |
| `research_gates` | 26,140 | 22 MB | 5.4 MB | 1.0 MB | 29 MB | 0 | Not measurable | Result payloads; sampled maximum 192,714 chars |
| `research_evidence` | 18,316 | 18 MB | 4.6 MB | 984 kB | 24 MB | 0 | Not measurable | Evidence content; sampled maximum 192,714 chars |
| `research_events` | 82,726 | 15 MB | 8.4 MB | 40 kB | 23 MB | 0 | Not measurable | Append-only event records and indexes |
| `strategy_registry` | 4,109 | 8.0 MB | 680 kB | 8.3 MB | 17 MB | 0 | Not measurable | Wide serialized strategy definitions; sampled tuple ~3,616 B |
| `audit_experiences` | 5,239 | 2.4 MB | 2.1 MB | 10 MB | 15 MB | 0 | Not measurable | Immutable decision payloads; sampled tuple ~2,344 B |
| `audit_signals` | 11,302 | 10 MB | 1.5 MB | 40 kB | 12 MB | 0 | Not measurable | Wide signal rows and payloads |
| `news_analyzed_hashes` | 23,497 | 5.1 MB | 5.6 MB | 40 kB | 11 MB | 0 | Not measurable | Hash history plus duplicate index |
| `factory_events` | 4,449 | 6.2 MB | 776 kB | 872 kB | 8.0 MB | 0 | Not measurable | Wide factory event payloads |
| `factory_candidates` | 4,108 | 6.5 MB | 1.0 MB | 56 kB | 7.6 MB | 0 | Not measurable | Wide candidate definitions |
| `news_entities` | 61,374 | 5.7 MB | 1.3 MB | 40 kB | 7.2 MB | 0 | Not measurable | Normalized child rows |
| `news_ai_analysis` | 2,767 | 4.6 MB | 456 kB | 648 kB | 5.8 MB | 0 | Not measurable | Wide serialized AI analysis |
| `position_lifecycle_events` | 3,888 | 5.2 MB | 568 kB | 40 kB | 5.8 MB | 0 | Not measurable | Lifecycle event payloads |
| `news_impacts` | 23,408 | 4.0 MB | 1.3 MB | 40 kB | 5.3 MB | 0 | Not measurable | Impact/event history |
| `news_analysis_runs` | 27,399 | 3.9 MB | 1.2 MB | 40 kB | 5.3 MB | 0 | Not measurable | Analysis run history |
| `news_junk_hashes` | 9,382 | 2.3 MB | 2.3 MB | 40 kB | 4.7 MB | 0 | Not measurable | Hash history plus duplicate index |
| `news_topics` | 42,403 | 2.8 MB | 944 kB | 40 kB | 3.8 MB | 0 | Not measurable | Normalized topic rows |

Growth is NOT MEASURABLE from one snapshot. The timestamp ranges describe data age, not storage deltas. Daily relation-size snapshots are required before 30/90/180/365-day projections.

## TOAST Analysis

The catalog confirms significant TOAST allocation:

- `news_articles`: 55 MB TOAST, approximately 70% of its 79 MB total.
- `news_analysis`: 26 MB TOAST, approximately 58% of its 45 MB total.
- `audit_experiences`: 10 MB TOAST, approximately 67% of its 15 MB total.
- `strategy_registry`: 8.3 MB TOAST, approximately 49% of its 17 MB total.
- `research_gates`: 1.0 MB TOAST.
- `research_evidence`: 984 kB TOAST.
- `factory_events`: 872 kB TOAST.
- `news_ai_analysis`: 648 kB TOAST.

Payload measurements from bounded table aggregates/samples:

- `news_articles.body`: average 1,952.5 characters; maximum 56,692; total body text approximately 48.7 MB.
- `research_evidence.content`: average approximately 938 characters; maximum 192,714.
- `research_gates.result`: average approximately 726 characters; maximum 192,714.
- `audit_experiences.payload`: average approximately 2,982 characters; maximum 3,096.
- `shadow_decisions.payload`: average approximately 1,956 characters; maximum 1,977; aggregate payload text approximately 108 MB.

P95/P99 distributions were not produced in the initial read-only pass. They require a bounded query using `percentile_cont` over `length()` or `pg_column_size()` per payload column. The current evidence supports payload inflation as a storage cause, but not that the payloads are redundant or removable.

## Bloat Analysis

No significant bloat is demonstrated.

Evidence:

- `shadow_decisions`: 55,342 live, 0 dead.
- `news_articles`: 24,939 live, 19 dead, dead/live ratio approximately 0.0008.
- All other top relations: 0 dead tuples in the captured snapshot.
- Autovacuum and autoanalyze timestamps are current for the largest tables.
- No long-running client transaction was observed that would prevent cleanup.

This is a data-size/payload-size problem, not a proven heap-bloat problem. Exact free-space/bloat percentages are NOT MEASURABLE from `pg_stat_user_tables` alone; a vetted page-level bloat query or extension would be needed. `VACUUM FULL`, CLUSTER, and blanket REINDEX are rejected.

## Vacuum / Analyze Health

| Area | Evidence | Finding |
| --- | --- | --- |
| Autovacuum | `autovacuum=on` | Enabled |
| Statistics tracking | `track_counts=on` | Enabled |
| Top-table autovacuum | Current timestamps on heavy tables | Healthy in snapshot |
| Top-table autoanalyze | Current timestamps on heavy tables | Healthy in snapshot |
| Modifications since analyze | 0 on most top tables; 4 on governance events | No stale-statistics signal |
| Vacuum progress | No active vacuum progress row | No vacuum currently running during probe |

No table-specific autovacuum overrides were found in the captured public-table settings query. No autovacuum tuning change is justified by current evidence.

## Query Performance

### Query observability state

`pg_stat_statements` is unavailable because:

- the extension is absent from `pg_extension`;
- querying `pg_stat_statements` returns `UndefinedTable`;
- `shared_preload_libraries` is empty;
- PostgreSQL statistics require the module to be loaded at server start, so enabling it requires an approved configuration change and PostgreSQL restart;
- `track_io_timing` is off, so statement I/O timing is unavailable even in catalog views.

Required controlled change, not performed:

1. Install/use the PostgreSQL `pg_stat_statements` extension available to the server package.
2. Add `pg_stat_statements` to `shared_preload_libraries` through the approved server configuration workflow.
3. Restart PostgreSQL during an approved maintenance window.
4. Run `CREATE EXTENSION pg_stat_statements` as an authorized database owner/superuser if not already present.
5. Capture a representative workload window without calling `pg_stat_statements_reset()` during the window.
6. Export normalized query metrics and correlate operation names from the backend.

Risk: MEDIUM operational risk because restart/configuration is required. Benefit: high measurement value. Rollback: remove the extension from preload configuration and restart through the same controlled workflow; extension removal is not required for normal rollback. No automatic change was made.

### Required top-query table

| Rank | Fingerprint | Calls | Total ms | Avg ms | Rows | Read Blocks | Hit Blocks | Root Cause |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| N/A | N/A — `pg_stat_statements` unavailable | N/A | N/A | N/A | N/A | N/A | N/A | OBSERVABILITY |

This table is intentionally unavailable rather than fabricated.

## Query Fingerprints

No runtime fingerprints are available. Source-level fingerprint candidates are listed under Backend Query Attribution and SELECT * Analysis. They must be confirmed with `pg_stat_statements`, application traces, or database logs before ranking.

## Execution Plans

A safe plain `EXPLAIN (FORMAT JSON)` was run for:

```sql
SELECT * FROM audit_ledger ORDER BY ticket DESC LIMIT 50;
```

Observed plan:

- `Limit`
- backward `Index Scan` on `audit_ledger_pkey`
- estimated relation rows: 435
- estimated output rows: 50
- estimated plan width: 650 bytes

This proves that the examined latest-ticket pagination shape can use the primary-key ordering index. It does not provide execution latency or buffer counts because `EXPLAIN ANALYZE` was not run and no query workload rank exists.

No production `EXPLAIN ANALYZE` was run for unbounded or potentially high-cost queries. Actual plan/latency validation belongs in a controlled, bounded workload window or a representative clone.

## Cardinality Problems

No estimate-vs-actual cardinality problem can be proven without `EXPLAIN ANALYZE` or a representative execution plan. No statistics-target or extended-statistics change is recommended.

The data model has possible logical cardinality questions, not proven planner errors:

- `news_analysis` has 20,939 rows and 474 more rows than its distinct `article_id` count would allow for one analysis per article. This may represent multiple runs/providers and requires lineage inspection.
- `research_events` has 82,726 rows and no duplicate `event_id` count in the prior focused probe.
- `news_articles` has 3 rows marked duplicate and no duplicate `article_hash` count in the focused probe.

## I/O Analysis

Highest cumulative heap reads in `pg_statio_user_tables` were observed on:

1. `shadow_decisions`
2. `model_governance_events`
3. `news_analysis`
4. `research_gates`
5. `research_evidence`
6. `news_articles`
7. `research_events`
8. `audit_signals`

These relations are also among the largest/heaviest tables, so the counters establish I/O concentration but not inefficient access. With `track_io_timing=off`, time-per-read is unavailable. No missing index is inferred from heap reads alone.

## Cache Analysis

The latest database counters imply approximately 98.21% block-hit ratio. This is healthy for the captured cumulative workload but does not prove low per-request latency. `shared_buffers=128 MB` and `effective_cache_size=4 GB` are configuration facts, not evidence that memory needs changing. No memory tuning is recommended.

## Temp File Analysis

Two temporary files totaling 512 KiB are recorded in `pg_stat_database`. `log_temp_files=-1` disables temp-file logging, and `track_io_timing=off` disables I/O timing. Root query/operator is therefore NOT MEASURABLE. Do not increase `work_mem` based on this evidence.

## WAL / Checkpoint Analysis

The database generated approximately 8.58 GB of WAL since 2026-09-24 16:07:19, with 1.52 million full-page images and 27,248 full WAL buffers. This is a baseline counter, not a diagnosis. There is no per-statement WAL attribution because `pg_stat_statements` is absent and no WAL-log workload window exists.

The current settings (`fsync=on`, `synchronous_commit=on`, `max_wal_size=1 GB`, checkpoint timeout 300 seconds) are not changed. A proper WAL audit requires simultaneous samples of WAL bytes, committed/inserted/updated/deleted rows, application workload rate, and checkpoint counters.

## Connection Pool

The inspected PostgreSQL implementation does not expose measurable pool min/max/waiters:

- `src/nexus_scalp/database/drivers/postgres_driver.py:166-181`: `PostgreSQLDriver.connect()` calls `psycopg.connect()` directly.
- `src/nexus_scalp/database/drivers/base.py:205`: base driver stores a connection in `_conn` when used through its shared path.
- `src/nexus_scalp/database/drivers/proxy.py:100-103`: proxy obtains and stores a driver connection.
- Search of database/adapters source found no `ConnectionPool`, `psycopg_pool`, `pool_size`, `max_size`, `min_size`, waiter, or acquisition-latency implementation.

`DatabaseConfig.pooling_enabled=True` is therefore not evidence that a server-side/application pool is active. The current live session snapshot showed one client backend and no leak/idle-in-transaction signal. Pool utilization, acquisition latency, and waiters are NOT MEASURABLE and require application instrumentation.

## Lock / Transaction Analysis

No blocked or waiting lock was observed. No long-running client transaction was observed. The current probe held only its own read locks. This is a healthy point-in-time state, not a guarantee under concurrent trading/news/research workloads.

## Backend Query Attribution

### Backend Hotspots

| File | Function / region | Query or pattern | Calls | DB Cost | Root Cause | Fix |
| --- | --- | --- | ---: | ---: | --- | --- |
| `src/nexus_scalp/adapters/database/audit_repository.py:1376-1382` | broker trade listing | `SELECT * ... LIMIT ? OFFSET ?` | N/A | N/A | BACKEND_REPOSITORY / QUERY candidate | Measure offsets, result widths, and caller frequency; then consider projection/keyset pagination |
| `src/nexus_scalp/adapters/database/audit_repository.py:3708-3716` | audit ledger listing | `SELECT * ... LIMIT ? OFFSET ?` | N/A | N/A | BACKEND_REPOSITORY / QUERY candidate | Same; no global replacement without usage evidence |
| `src/nexus_scalp/adapters/database/audit_repository.py:3772-3776` | ledger point lookup | `SELECT * ... WHERE ticket = ?` | N/A | N/A | BACKEND_REPOSITORY candidate | Project only required columns if consumer contract permits |
| `src/nexus_scalp/accounting/core.py:314-317` | ledger search | `SELECT * FROM audit_ledger ... ORDER BY ... LIMIT ?` | N/A | N/A | BACKEND_REPOSITORY candidate | Measure call frequency and returned width |
| `src/nexus_scalp/accounting/core.py:787-790` | ledger point lookup | `SELECT * FROM audit_ledger WHERE ticket = ?` | N/A | N/A | BACKEND_REPOSITORY candidate | Same |
| `src/nexus_scalp/reporting/queries.py:103-105,137-140` | report assembly | `SELECT *` over a join | N/A | N/A | BACKEND_REPOSITORY / QUERY candidate | Verify required columns and join cardinality before projection |
| `src/nexus_scalp/marketplace/service.py:155-189` | `MarketplaceService.install_pack._install` | per-seed existence query in two loops | N/A | N/A | BACKEND_REPOSITORY / SERVICE / N+1 candidate | Measure install frequency and seed counts; replace with set-based existence/conflict-aware path only after parity tests |
| `src/nexus_scalp/adapters/database/audit_repository.py:2301-2340` | audit worker | batches up to 500; `executemany` | N/A | N/A | INGESTION, positive pattern | Preserve; benchmark batch size under representative workload |

The exact service/API/worker callers for each read path must be traced from call sites during a query-observability pass. Source grep alone does not establish runtime call counts.

## N+1 Analysis

One concrete query-in-loop candidate was found in `src/nexus_scalp/marketplace/service.py:155-189`, `MarketplaceService.install_pack._install`:

- the installation transaction loops over every seed and calls `query_one` to test existence;
- a second per-seed loop queries each seed again and may write it;
- query count therefore grows with seed count, with two per-seed read passes before considering writes.

This is a HIGH-confidence source pattern and a P1 measurement candidate, but it is not yet a proven production bottleneck: runtime installation frequency, seed counts, query latency, and transaction duration were not captured. The least-risk experiment is a representative installation trace comparing per-seed reads with one set-based existence query or conflict-aware writes. Preserve idempotency and SQLite behavior.

Counter-evidence: `src/nexus_scalp/accounting/core.py:433-453` uses chunked `IN` queries of 400 tickets, and `src/nexus_scalp/reporting/queries.py:86-107,120-140` uses temporary-ticket-table joins and `executemany`; these paths are already set-based and should not be rewritten as generic N+1 fixes.

Required runtime proof is a capture around representative API/worker calls with query fingerprints, correlation IDs, query count, and transaction duration. Do not rewrite repositories based on source shape alone.

## SELECT * Analysis

Confirmed source patterns:

- `audit_repository.py:1378`, `1396-1421`, `3708-3716`, `3772-3776`, `3793-3794`
- `accounting/core.py:316-317`, `789`
- `reporting/queries.py:105`, `139`
- `web/db_console.py:381-384` (`database_table_preview`, an operator-selected generic table preview)
- additional model/diagnostic paths found by source search

The database evidence shows wide rows in several tables, so projecting fewer columns could matter on high-frequency paths. The web console preview is intentionally generic and should not be treated like a hot business endpoint. However, no runtime call count, bytes transferred, deserialization time, or endpoint latency is available. `SELECT *` is classified as a P2 measurement candidate, not a proven bottleneck.

## Pagination Analysis

OFFSET pagination exists in:

- `audit_repository.py:1378-1382` (`AuditRepository.get_broker_trades`), ordered by computed `COALESCE(NULLIF(exit_time,''),'')`, which is not the same evidence as the primary-key latest-page plan;
- `audit_repository.py:3708-3716` (`AuditRepository.list_audit_entries`);
- `src/nexus_scalp/incidents/store.py:520-543` (`IncidentStore.list`);
- `src/nexus_scalp/web/db_console.py:350-384` (`database_table_preview`);
- `src/nexus_scalp/adapters/database/dead_letter_store.py:286-319`, where OFFSET is bounded maintenance logic to find a keep floor, not user pagination.

The inspected ledger latest-page plan uses a backward primary-key index scan and is estimated at only 435 rows in the current table, so no current deep-offset problem is proven for that path. The broker-trade computed ordering may require a different plan and must be explained separately. Keyset pagination should be considered only if callers request large offsets as the tables grow.

## Batch Write Analysis

`audit_repository.py:2301-2340` drains a queue in batches up to 500 and groups statements for `executemany`. This is evidence of batching rather than one-row/one-transaction writes on that path. Other high-volume writers require runtime write counters before claiming they lack batching. No change is made.

## Repository Analysis

The provider abstraction is preserved:

```text
Repository contract
    -> DatabaseDriver
        -> SQLiteDriver / PostgreSQLDriver
```

`PostgreSQLDriver` uses lazy psycopg import and provider-specific placeholder/type translation. Any optimization must remain provider-neutral at shared repository boundaries or live inside provider-specific driver/dialect implementations. SQLite must remain supported and must receive contract tests for any shared repository change.

No broad ORM layer was identified in the inspected database path; SQL is issued through repository/driver code. ORM-specific lazy-loading conclusions are not applicable without evidence.

## Data Lifecycle

| Data group | Proposed classification | Rationale |
| --- | --- | --- |
| `shadow_decisions` | HOT/WARM, then COLD/ARCHIVE | High-volume shadow evidence; preserve model-comparison reproducibility |
| `news_articles`, `news_analysis`, `news_ai_analysis` | WARM/COLD/RETAIN subject to news policy | Text and analysis history; duplicate/old does not mean deletable |
| `model_governance_events` | IMMUTABLE/RETAIN | Governance and model lifecycle evidence |
| `research_gates`, `research_evidence`, `research_events` | COLD/RETAIN or verified archive | Research reproducibility and lineage |
| `strategy_registry` | COLD/RETAIN by lifecycle | Wide definitions; superseded versions need lineage policy |
| `audit_experiences`, `audit_signals`, `position_lifecycle_events` | IMMUTABLE/RETAIN | Trading/risk/audit correctness |
| `news_junk_hashes`, analyzed hashes | WARM/COLD, policy-controlled | Deduplication state may be operationally useful; no deletion inferred |

No table is classified PURGEABLE solely from age. Any future purge needs domain ownership, dependency analysis, preview, bounded transactions, progress, audit record, and post-delete maintenance.

## Retention Opportunities

The highest-value retention design opportunity is not immediate deletion. It is separating operational query horizons from immutable historical storage:

1. define completed shadow-run retention and archive verification;
2. define news raw-body versus derived-analysis retention separately;
3. define research evidence reproducibility requirements;
4. preserve all trading, risk, financial, governance, and audit records unless the business owner authorizes otherwise;
5. capture daily size growth before setting numeric retention periods.

## Purge Design

Future purge implementation, if approved, must:

- preview candidate rows and dependencies;
- select immutable cutoff keys, not a moving unbounded predicate;
- delete in bounded batches, approximately 5,000 rows/transaction where table/workload permits;
- commit between batches;
- log purge ID, table, cutoff, batch count, rows, and operator/policy version;
- verify dependent counts and retained lineage;
- support rollback by archive/reinsert, not by assuming a transaction can span the entire purge;
- schedule ordinary VACUUM afterward if deletion creates dead tuples;
- never use `VACUUM FULL` as a generic cleanup.

## Partitioning Assessment

Partitioning is not justified by current evidence. The database is 501 MB, growth rate is unavailable, no retention purge pain is measured, and no partition-pruning workload has been demonstrated. Candidate tables such as `shadow_decisions`, `research_events`, or news history should only be partitioned after evidence shows sustained growth, time-local access, and measurable maintenance/retention benefit. Partitioning would require migration, query/index/foreign-key review, operational complexity, and SQLite-specific compatibility decisions.

## View / Materialized View Assessment

There are no views or materialized views. No repeated expensive analytical query has been measured because `pg_stat_statements` is unavailable. No view or materialized view is proposed. A future candidate must show repeated query cost, acceptable staleness, refresh cost, storage cost, and concurrency benefit.

## Index Ranking

| Index | Table | Size | Scans | Purpose | Redundant? | Action |
| --- | --- | ---: | ---: | --- | --- | --- |
| `idx_gov_events_model` | `model_governance_events` | 5.4 MB | 0 | `(model_id,timestamp)` access | Unknown | Observe full workload; do not drop |
| `idx_gov_events_ts` | `model_governance_events` | 3.3 MB | 0 | timestamp access | Unknown | Observe full workload; do not drop |
| `research_events_event_id_key` | `research_events` | 3.2 MB | 82,726 | Unique event identity | No | Keep |
| `idx_events_strategy` | `research_events` | 3.2 MB | 0 | `(strategy_id,occurred_at)` | Unknown | Plan/query review |
| `news_articles_article_hash_key` | `news_articles` | 3.1 MB | 25,803 | Unique deduplication | No | Keep |
| `model_governance_events_event_id_key` | `model_governance_events` | 2.9 MB | 59,166 | Unique event identity | No | Keep |
| `news_analyzed_hashes_pkey` | `news_analyzed_hashes` | 2.8 MB | 23,497 | Primary key/hash uniqueness | Yes with nonunique duplicate | Keep constraint index |
| `idx_news_analyzed_hashes_hash` | `news_analyzed_hashes` | 2.8 MB | 725 | Same `(article_hash)` key | Exact duplicate | Controlled review/drop candidate |
| `shadow_decisions_shadow_decision_id_key` | `shadow_decisions` | 2.6 MB | 55,342 | Unique shadow identity | No | Keep |
| `idx_shadow_decisions_run` | `shadow_decisions` | 2.2 MB | 0 | `(run_id,timestamp)` | Unknown | Observe actual run queries |
| `idx_evidence_strategy_created_id` | `research_evidence` | 2.0 MB | 0 | `(strategy_id,created_at,evidence_id)` | Prefix-overlap candidate | Compare with shorter index |
| `idx_gates_strategy_run` | `research_gates` | 2.0 MB | 0 | `(strategy_id,research_run_id,order_index)` | Unknown | Observe actual gate queries |

## Duplicate Index Analysis

Confirmed exact duplicates:

1. `news_analyzed_hashes_pkey (article_hash)` and `idx_news_analyzed_hashes_hash (article_hash)`, 2.8 MB each.
2. `news_junk_hashes_pkey (article_hash)` and `idx_news_junk_hashes_hash (article_hash)`, 1.16 MB each.
3. `audit_experience_outcomes_idempotency_key_key (idempotency_key)` and `idx_exp_outcome_key (idempotency_key)`, 160 kB each.
4. `release_metadata_pkey (key)` and `idx_release_metadata_key (key)`, 8 kB each.

Combined duplicate storage is approximately 8 MB. Primary/unique constraint indexes must remain. Only the redundant non-constraint indexes are controlled migration candidates after checking migrations, deployment tooling, and a complete workload window. A zero scan count alone is insufficient.

Prefix-overlap candidates exist in research indexes, but no drop recommendation is made without query plans. An index with a longer key can support a different ordering or selective predicate than its prefix.

## BRIN / Partial / Covering Index Assessment

- BRIN: not justified. Current candidate tables are modest in size and no physical-correlation/selectivity measurement or large time-range query workload is available.
- Partial indexes: no runtime evidence was captured for high-value predicates such as `status='ACTIVE'`, `deleted_at IS NULL`, or `archived=false`. No partial index is proposed.
- INCLUDE/covering indexes: no query-level workload evidence establishes heap-fetch cost or a repeated index-only-scan opportunity. No covering index is proposed.
- GIN/GiST/JSONB indexes: the schema stores many serialized TEXT values rather than verified JSONB operator workloads. No GIN/index-expression change is proposed.

## Backend Hotspots and Root Cause Classification

| File | Function / region | Query | Calls | DB Cost | Root Cause | Fix |
| --- | --- | --- | ---: | ---: | --- | --- |
| `audit_repository.py` | listing methods at 1376 and 3708 | `SELECT *` + OFFSET | NOT MEASURABLE | NOT MEASURABLE | BACKEND_REPOSITORY | Measure then project/keyset selectively |
| `accounting/core.py` | ledger search/lookup at 314 and 787 | `SELECT *` | NOT MEASURABLE | NOT MEASURABLE | BACKEND_REPOSITORY | Measure consumer columns |
| `reporting/queries.py` | report joins at 103 and 137 | `SELECT *` join | NOT MEASURABLE | NOT MEASURABLE | QUERY / BACKEND_REPOSITORY | Measure join cardinality and transfer |
| `audit_repository.py` | queue writer at 2301-2340 | batch `executemany` | NOT MEASURABLE | NOT MEASURABLE | INGESTION, positive pattern | Preserve and benchmark |
| `postgres_driver.py` | `connect()` at 166-181 | direct psycopg connection | NOT MEASURABLE | NOT MEASURABLE | CONNECTION_POOL observability gap | Instrument lifecycle/acquisition; do not increase pool blindly |

## N+1 Analysis

Status: UNKNOWN, not proven. No request/job trace provides queries per request/job or parent-child multiplicity. Required evidence is a capture around representative API/worker calls with query fingerprints and correlation IDs. Do not rewrite repositories based on source shape alone.

## Optimization Plan

| Priority | Problem | Evidence | Fix | Benefit | Risk | Rollback |
| --- | --- | --- | --- | --- | --- | --- |
| P0 | Query cost cannot be ranked | `pg_stat_statements` absent; preload empty | Controlled enablement and workload window | Enables real query attribution | MEDIUM | Revert configuration via approved restart; no code rollback required |
| P1 | Marketplace installation has a concrete per-seed query loop | `marketplace/service.py:155-189`; two per-seed read passes; runtime cost not yet measured | Capture representative install query count/latency, then evaluate one set-based existence query or conflict-aware write | Potentially removes O(N) round trips while preserving idempotency | MEDIUM | Keep existing path; change only after SQLite/PostgreSQL parity tests |
| P1 | Future growth not controlled | Largest tables are append/wide data; growth history absent | Define lifecycle/archive policy and collect daily growth | Predictable size and retention | MEDIUM/HIGH | Archive restore; deletion requires approval |
| P1 | Duplicate non-constraint indexes | Four exact duplicate pairs; ~8 MB | Controlled migration review, then remove only confirmed duplicates | ~8 MB index space and write-maintenance reduction | MEDIUM | Recreate index from captured definition |
| P2 | Potential read overfetch | Exact `SELECT *` source paths; wide rows | Measure then add explicit projections | Lower transfer/deserialization if workload is high | LOW/MEDIUM | Restore prior repository projection |
| P2 | Potential deep pagination cost | OFFSET source paths; current ledger only 435 rows | Measure large offsets, then add keyset path selectively | Stable deep-page latency if demonstrated | MEDIUM | Retain old API path |
| P2 | Payload inflation | TOAST and multi-kilobyte rows; max 192,714 chars | Measure p95/p99/access; redesign only if justified | Lower storage/I/O only if payloads are redundant | MEDIUM/HIGH | Versioned schema/archive rollback |
| P3 | Pool metrics absent | No pool implementation/metrics found | Instrument connection lifecycle and acquisition | Detect leaks/starvation | LOW | Remove instrumentation |
| P3 | WAL rate unknown | 8.58 GB since reset, no workload denominator | Sample WAL plus row/write rates over time | Validates write pressure | LOW | Stop metrics collection |
| P4 | Partitioning/materialized views | No measured need | Do not implement | Avoids unnecessary complexity | N/A | N/A |

## Risk Matrix

### SAFE / LOW

- Record daily database/table/index sizes.
- Capture `pg_stat_activity`, locks, WAL, checkpoints, and relation stats periodically.
- Add application operation names, provider, duration, rows, transaction ID, and correlation ID to database metrics without secrets.
- Preserve the existing bounded batch writer.

### MEDIUM

- Enable `pg_stat_statements` and possibly `track_io_timing` through a controlled PostgreSQL restart.
- Remove a redundant non-constraint index after migration/deployment review and a workload window.
- Introduce explicit projections or keyset pagination behind provider-neutral contracts after benchmarks.

### HIGH / DESTRUCTIVE

- Delete or archive news, research, shadow, telemetry, governance, trading, financial, or audit records.
- Alter payload schema, partition tables, or change retention semantics.
- Drop primary/unique constraint indexes, tables, or schemas.
- Change provider routing.

## SQLite Compatibility Assessment

No SQLite code or provider routing was changed. Future shared repository optimizations must be expressed through repository contracts and tested against both providers. PostgreSQL-only features such as `pg_stat_statements`, BRIN, GIN, `INCLUDE`, `CREATE STATISTICS`, and PostgreSQL-specific pagination/index migrations must remain in PostgreSQL operational/migration layers and must not leak into SQLite SQL paths. Any projection/keyset API change requires SQLite and PostgreSQL contract tests.

## Baseline Metrics

| Metric | Baseline |
| --- | ---: |
| Database size | 525,211,315 bytes / 501 MB |
| Tables | 125 |
| Indexes | 445 |
| Sequences | 62 |
| Cache hit ratio | approximately 98.21% latest counters |
| Dead tuples in largest table | 0 in `shadow_decisions`; 19 in `news_articles` |
| Temp files / bytes | 2 / 524,288 |
| WAL since reset | 8,575,023,513 bytes |
| Current client backends | 1 observed |
| Blocked locks | 0 observed |
| `pg_stat_statements` | unavailable |
| Query latency ranking | NOT MEASURABLE |
| Pool utilization | NOT MEASURABLE |
| Historical growth | NOT MEASURABLE |

## Before / After Measurement Contract

No optimization was implemented, so there is no fabricated after-value. The required experiment format for any approved change is:

| Metric | Before | After | Change |
| --- | ---: | ---: | ---: |
| Latency | NOT MEASURABLE for current workload | N/A | N/A |
| Calls | NOT MEASURABLE | N/A | N/A |
| Buffers read/hit | NOT MEASURABLE per query | N/A | N/A |
| Rows | Per-table estimates available; per query unavailable | N/A | N/A |
| DB size | 501 MB | N/A | N/A |
| Index size | Relation values captured | N/A | N/A |
| Write cost | WAL cumulative only | N/A | N/A |

## Recommended Roadmap

1. P0: obtain approval for query observability. Enable `pg_stat_statements` through the PostgreSQL configuration/restart workflow and instrument backend operation names/correlation IDs.
2. P0: collect a representative workload window covering trading/audit, news, research, governance, and shadow workers.
3. P1: produce the real normalized query cost matrix and safe execution plans. Separate total cost, latency, frequency, I/O, temp, and WAL.
4. P1: decide lifecycle/retention ownership for shadow, news, research, and telemetry data; archive before purge.
5. P1: verify duplicate index candidates against query plans, migrations, constraints, and write benchmarks; remove only approved non-constraint duplicates.
6. P2: benchmark explicit projections and deep-pagination alternatives only on high-frequency/high-offset paths.
7. P2: profile payload p95/p99 and TOAST access before schema redesign.
8. P3: add daily growth and WAL dashboards, plus pool/transaction acquisition metrics.
9. P4: reassess BRIN, partitioning, materialized views, extended statistics, and covering indexes only after the measured workload exists.

## Evidence Appendix

Live read-only artifacts generated during this audit:

- `C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/nse_pg_master_probe.py`
- `C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/nse_pg_master.json`
- `C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/nse_pg_audit.json`
- `C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/nse_pg_deep.json`
- `C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/nse_pg_focus.json`

Key catalog/statistics sources queried:

- `pg_database`
- `pg_namespace`
- `pg_class`
- `pg_index`
- `pg_attribute`
- `pg_constraint`
- `pg_stat_database`
- `pg_stat_activity`
- `pg_locks`
- `pg_stat_user_tables`
- `pg_stat_user_indexes`
- `pg_statio_user_tables`
- `pg_settings`
- `pg_stat_wal`
- `pg_stat_bgwriter`
- `pg_stat_progress_vacuum`
- `information_schema.columns`
- `pg_indexes`

Exact backend source evidence inspected:

- `src/nexus_scalp/database/config.py`
- `src/nexus_scalp/database/drivers/postgres_driver.py`
- `src/nexus_scalp/database/drivers/base.py`
- `src/nexus_scalp/database/drivers/proxy.py`
- `src/nexus_scalp/adapters/database/audit_repository.py`
- `src/nexus_scalp/adapters/database/dead_letter_store.py`
- `src/nexus_scalp/accounting/core.py`
- `src/nexus_scalp/reporting/queries.py`

No implementation change was made. The report deliberately distinguishes verified database facts, source patterns, and metrics that remain unavailable until query observability is approved and enabled.