# Phase 1 — Zero-Write Forensic Baseline (Database Remediation Program)

Generated (UTC): 2026-09-29T02:40:50Z
Baseline commit (this branch): `6c8de59b8f513e2c65d2d61f299a824f0b9f5e89`

Part 4 of the execution protocol, Phase 1. **Zero-write.** The PostgreSQL
session is opened with `default_transaction_read_only = on` and every probe
is a `SELECT` against catalog/statistics views. No `VACUUM`, `ANALYZE`,
`REINDEX`, DDL, DML or configuration change was executed. SQLite targets are
opened through a `mode=ro` URI.

Reproduce with:

```
# PostgreSQL
.venv/Scripts/python.exe scripts/db_remediation/baseline_collect_pg.py \
    --dsn "host=127.0.0.1 port=5432 dbname=nexusdb user=postgres password=<pw>" \
    --outdir docs/forensic-docs/remediation/phase1 --label phase1b-20260929

# SQLite
.venv/Scripts/python.exe scripts/db_remediation/baseline_collect_sqlite.py \
    --outdir docs/forensic-docs/remediation/phase1 --label phase1-20260929 <db> [<db> ...]

python scripts/db_remediation/phase1_report.py --dir docs/forensic-docs/remediation/phase1   # regenerates this file
```

---

## 1. Environment and observation window

- Server: `PostgreSQL 17.10 on x86_64-windows, compiled by msvc-19.44.35227, 64-bit`
- Database: `nexusdb` at `127.0.0.1/32:5432`
- Database size: **604 MB** (633,190,067 bytes)
- Encoding / timezone: UTF8 / Asia/Tehran
- Postmaster start: 2026-09-29T01:19:26.112235+03:30
- Baseline captured: 2026-09-29T06:03:29.738352+03:30
- Statistics reset (`pg_stat_wal.stats_reset`): **2026-09-24T16:07:19.881357+03:30**
- Relations: 126 tables, 281 indexes

**Critical window fact.** The stats window covers the period since `2026-09-24T16:07:19`. The only relevant source fix on `origin/main`
is PR #561 (`audit_signals` scan amplification), which merged at
`2026-09-29 02:00:20 +03:30`. At capture time the engine had **no listener**
on any NSE port and `pg_stat_activity` held **0 backends** and **0 locks**,
so *no query has executed against the fixed code inside this statistics
window*. Every cumulative counter below therefore records **pre-fix**
behaviour, and no post-merge runtime observation exists.

---

## 2. Instrumentation ceiling — what is NOT measurable

Per sec.6, an unavailable metric is stated, never fabricated:

- `pg_stat_statements`: **ABSENT** (`shared_preload_libraries` is empty, source=default).
  -> Query-level rankings by total/mean execution time, calls, rows, shared
  blocks, temp blocks and WAL are **NOT MEASURABLE WITH CURRENT
  INSTRUMENTATION**.
- `compute_query_id = auto` gives no usable `pg_activity.query_id` without
  that extension.
- `track_io_timing = off` and `track_wal_io_timing = off` -> `blk_read_time`,
  `blk_write_time`, `wal_write_time`, `wal_sync_time` all read `0.0`. These
  zeros are an **instrumentation choice, not a measurement of zero latency**,
  and must not be reported as latencies.
- `log_min_duration_statement = -1` -> no slow-query log exists to mine.

Consequence for sec.15 / sec.53: **p50/p95/p99 per hot path are not
obtainable from the current instrumentation.** They require either enabling
`pg_stat_statements` (shared_preload_libraries + restart — a controlled
configuration operation, not a read-only audit step) or an application-side
driver-boundary aggregator. Both are implementation-phase items and are
listed under section 7.

---

## 3. Standard before/after table (sec.53)

`After` is `PENDING` for every row: no remediation in this program has been
implemented or merged yet, so an after-value would be fabricated.

Metric                              | Before                | After     | Delta | Evidence
------------------------------------|-----------------------|-----------|-------|---------
Database size                       | 604 MB                | PENDING   | -     | identity probe
Tables / indexes                    | 126 / 281               | PENDING   | -     | footprint, index probes
TOAST bytes (all relations)         | 193.5 MB              | PENDING   | -     | footprint probe
seq_tup_read (db total)             |        30,824,574,472 | PENDING   | -     | pg_stat_database
tup_fetched (db total)              |           156,309,388 | PENDING   | -     | pg_stat_database
returned/fetched amplification      |                197.2x | PENDING   | -     | derived
audit_signals seq_scan              |             3,220,578 | PENDING   | -     | pg_stat_user_tables
audit_signals seq_tup_read          |        30,548,787,365 | PENDING   | -     | pg_stat_user_tables
audit_orders idx_tup_fetch          |            61,661,025 | PENDING   | -     | pg_stat_user_tables
zero-scan indexes (count)           | 91                    | PENDING   | -     | pg_stat_user_indexes
zero-scan index bytes               | 10.7 MB               | PENDING   | -     | pg_stat_user_indexes
tables never vacuumed/analyzed      | 81                    | PENDING   | -     | pg_stat_user_tables
WAL bytes since stats reset         |                 9.03 G | PENDING   | -     | pg_stat_wal
checkpoint write_time               |                 5753 s | PENDING   | -     | pg_stat_checkpointer
per-hot-path query count            | NOT MEASURABLE        | PENDING   | -     | no pg_stat_statements
p50 / p95 / p99 latency             | NOT MEASURABLE        | PENDING   | -     | no pg_stat_statements
Db bytes added/hour                 | NOT MEASURABLE        | PENDING   | -     | no growth sampling yet
purge duration                      | NOT MEASURABLE        | PENDING   | -     | no purge subsystem verified

---

## 4. Findings

Each finding carries a stable ID. Severity is *measured*, not assigned by feel.

### F-001 — audit_signals scan amplification — the dominant avoidable work in the cluster

**FACT.** A 7,453-row, 12 MB table accounts for 30,548,787,365 of the database's 30,824,574,472 `seq_tup_read` (99.1%), i.e. 4,098,858 rows read per live row.

**MEASUREMENT.**
- `seq_scan` = 3,220,578
- `seq_tup_read` = 30,548,787,365
- `idx_scan` = 618,219, `idx_tup_fetch` = 20,599,498
- `n_live_tup` = 7,453, total size 12 MB
- `heap_blks_hit` = 4,187,208,742 — the single largest buffer-hit consumer in `pg_statio_user_tables`
- derived: 4,098,858 tuples read per live row

**REPRODUCTION.** `baseline_collect_pg.py` probes `table_stats` + `statio`; source mapping and plan evidence are in `docs/forensic-docs/p1-audit-scan-amplification.md`.

**ROOT CAUSE.** Consumers issued predicates that select ~100% of the table (7-day window equal to the whole retention period, `action='NO_TRADE'` at ~92% selectivity, `id IN (SELECT ... LIMIT 20000)` where 20000 > row count), so the planner's sequential scan was correct and no index could have helped.

**CHANGE.** Already implemented upstream by PR #561 (bounded latest-N tails + a high-water-mark memo on the 5 Hz SSE section). **No change is made by this baseline.**

**BEFORE.** 3,220,578 seq scans / 30,548,787,365 tuples read, accumulated entirely BEFORE the fix merged.

**AFTER.** PENDING — and note that *zero* post-fix runtime has been observed (section 1).

**TRADEOFF.** The bounded tail (2000 rows) changes `/stats` and `/funnel` from a full distribution to a latest-N distribution. That is a deliberate semantic change and must be documented as BEFORE != AFTER for the aggregation window.

**TEST.** Existing: `tests/unit/test_p1_audit_scan_amplification.py` (550 lines). Missing: a regression guard that fails if a new consumer reintroduces an unbounded window, and the sec.69 runtime regression guard.

**LIMITATION.** Cumulative counters cannot attribute the split between the SSE path, the operator routes and the CLI. Attribution requires the driver-boundary aggregator described in section 7.

### F-002 — The most expensive confirmed defect has no post-merge runtime evidence

**FACT.** PR #561 merged at 2026-09-29 02:00:20, postmaster started 2026-09-29 01:19:26, and at capture the engine was not running. The fix has therefore never executed against this database.

**MEASUREMENT.**
- `pg_stat_activity` rows (excluding the collector session) = 0
- `pg_locks` rows of interest = 0
- no listener on the configured `NSE_WEB_ACTUAL_PORT` (59273) or 8080
- `nexus.pid` present since 2026-09-27 10:17 (last boot stamp)
- sessions_abandoned = 548 of 262,268 sessions

**REPRODUCTION.** Port check: `netstat -ano | grep LISTENING`. Liveness: the `activity` and `locks` probes in the baseline JSON.

**ROOT CAUSE.** Verification was stopped at 'CI green + merged'. Sec.83/§84 forbid declaring a remediation complete on merge; this program's Phase 12 does not exist yet.

**CHANGE.** None (measurement finding).

**BEFORE.** No post-fix baseline exists.

**AFTER.** PENDING — requires Phase 12 post-merge observation as the program's own §83 step requires.

**TRADEOFF.** None.

**TEST.** A controlled observation window (idle + active + burst) with the delta of `seq_scan` / `seq_tup_read` / `idx_scan` for `audit_signals` across a known start timestamp. Sec.84 requires the window be long enough to separate the change from noise.

**LIMITATION.** The benefit of PR #561 is currently asserted from a static-ledger probe (50 tail reads -> 1), not from production delta.

### F-003 — audit_orders: index-fetch amplification larger than audit_signals

**FACT.** A 2,457-row table is read through indexes 61,661,025 times — 25,096 index fetches per live row — with 3,229,612 index scans. This was not identified by any prior audit.

**MEASUREMENT.**
- `idx_scan` = 3,229,612
- `idx_tup_fetch` = 61,661,025
- `n_live_tup` = 2,457
- derived: 25,096 fetches per live row
- `seq_scan` = 85 (by contrast, near-zero)

**REPRODUCTION.** `table_stats` probe in the baseline JSON, filtered to `audit_orders`.

**ROOT CAUSE.** Not yet established. The pattern is 3,229,612 index probes returning 19.1 rows each — a high-frequency point lookup (order/ticket reconciliation is the probable caller) that has not yet been traced to a cadence.

**CHANGE.** None (Phase 1 is read-only).

**BEFORE.** 3,229,612 index scans, 61,661,025 tuples fetched.

**AFTER.** PENDING.

**TRADEOFF.** None at this stage. Must not be dismissed as 'small table, so cheap' — the row count is small, the call count is not.

**TEST.** Phase 2 must locate the caller and cadence, then build the sec.15 hot-path budget and a sec.69 regression guard on query count.

**LIMITATION.** Needs the query inventory (sec.8) and driver-boundary instrumentation. Absent those, the frequency is inferred from counters rather than observed.

### F-004 — audit_experience_outcomes: 2.81M index scans against a 3,471-row table

**FACT.** An outcomes/reconciliation table shows 2,807,889 index scans and 8,929,306 seq tuples read for 3,471 live rows.

**MEASUREMENT.**
- `idx_scan` = 2,807,889
- `idx_tup_fetch` = 1,174,944
- `seq_scan` = 4,218, `seq_tup_read` = 8,929,306
- `n_live_tup` = 3,471
- derived: 2,573 seq tuples per live row

**REPRODUCTION.** `table_stats` probe, filtered to `audit_experience_outcomes`.

**ROOT CAUSE.** Not yet established. Probable polling reconciler; needs sec.8 trace.

**CHANGE.** None (Phase 1 is read-only).

**BEFORE.** `idx_scan` = 2,807,889, `seq_tup_read` = 8,929,306.

**AFTER.** PENDING.

**TRADEOFF.** None at this stage.

**TEST.** Phase 2 trace + sec.29 polling test (no new data / 1 row / 10 rows / burst).

**LIMITATION.** Same instrumentation gap as F-003.

### F-005 — 91 of 281 indexes have never been scanned (10.7 MB of index storage)

**FACT.** Index storage is maintained on every write for indexes that no query in the statistics window has used.

**MEASUREMENT.**
- 91 of 281 indexes show `idx_scan = 0`
- combined footprint 10.7 MB (11,247,616 bytes)
- largest: `idx_gov_events_model` (5.4 MB), `idx_gates_strategy` (1.3 MB), `idx_exp_request` (0.7 MB), `idx_news_articles_source` (0.7 MB)
- invalid / not-ready indexes: 0 (none — no rebuild debt found)

**REPRODUCTION.** `index_stats` probe filtered on `idx_scan = 0`.

**ROOT CAUSE.** Unknown per index. A zero scan count inside a 4.6-day window is a workload observation, NOT proof of redundancy.

**CHANGE.** None. Sec.49 makes index removal a gated decision requiring usage stats, DDL and constraint dependency, migration references and foreign tooling; the statistics window here is too short to satisfy it.

**BEFORE.** 91 unused indexes, 10.7 MB.

**AFTER.** PENDING.

**TRADEOFF.** Dropping an index that a rare-but-critical path depends on is far more expensive than the storage saved. `model_governance_events` alone carries a 5.65 MB index that may serve a cold governance/audit query.

**TEST.** Sec.49 gate checklist per candidate index, then a write-cost and storage measurement over a longer window.

**LIMITATION.** The window is ~4.6 days and the workload is a live trading system with monthly/quarterly governance operations. This finding is a **candidate list**, explicitly not a drop list.

### F-006 — TOAST accounts for 193.5 MB — 32% of the database — concentrated in 7 tables

**FACT.** Large payload columns dominate storage, with several tables holding most of their bytes outside the heap.

**MEASUREMENT.**
- total TOAST across all relations: 193.5 MB of 604 MB
- top: `news_articles` 55.7 MB (69% of table), `strategy_registry` 28.2 MB (76% of table), `news_analysis` 25.6 MB (57% of table), `research_gates` 21.4 MB (42% of table), `audit_experiences` 21.1 MB (72% of table)
- `strategy_registry` and `factory_events` exceed 70% TOAST share on small row counts, indicating wide per-row payloads rather than row volume

**REPRODUCTION.** `footprint` probe (`pg_table_size - pg_relation_size` vs `reltoastrelid`).

**ROOT CAUSE.** Raw/verbose payloads are persisted in-line with the row instead of being derived or moved to cold storage. Sec.33/§34 treat this as a column-level storage question.

**CHANGE.** None (Phase 1 is read-only).

**BEFORE.** 193.5 MB TOAST.

**AFTER.** PENDING.

**TRADEOFF.** Compression or relocation can change read amplification on the hot path if a frequently-read column gets pushed out-of-line.

**TEST.** Sec.34 per-column audit: storage contribution, read frequency, consumer count, serialization frequency; then a bounded before/after on response bytes.

**LIMITATION.** Payload *content* was not sampled in this pass, so 'verbose' is an inference from TOAST share, not a measured redundancy.

### F-007 — 81 of 126 tables have never been vacuumed or analyzed by any path

**FACT.** Most relations have no maintenance history at all, and the ones that do are maintained by the default autovacuum settings with no per-table tuning.

**MEASUREMENT.**
- 81 of 126 tables show both `last_vacuum` and `last_autovacuum` as NULL
- highest dead-tuple counts: `news_analysis_runs` live=25,604 dead=1,795, `news_analysis` live=20,374 dead=565, `research_gates` live=27,984 dead=326, `factory_candidates` live=4,108 dead=295
- all autovacuum settings are `source=default`: `autovacuum_vacuum_scale_factor=0.2`, `autovacuum_analyze_scale_factor=0.1`, `autovacuum_max_workers=3`, `autovacuum_naptime=60`

**REPRODUCTION.** `table_stats` probe + `settings` probe for the autovacuum parameters.

**ROOT CAUSE.** No per-table autovacuum tuning exists for the append-heavy, high-churn tables in this workload. A 0.2 scale factor means a table is not vacuumed until 20% of its rows are dead — for the churning tables here that threshold is rarely reached, and for the append-only tables it is never reached.

**CHANGE.** None (Phase 1 is read-only).

**BEFORE.** 81 tables with no maintenance history.

**AFTER.** PENDING.

**TRADEOFF.** Aggressive autovacuum costs background I/O; on this box the observed pending cost is a 5,753 s cumulative checkpoint write time, so any tuning must be measured against that, not assumed.

**TEST.** Sec.24/§28: measure bloat and query latency at 10K/100K/1M rows before and after any per-table setting.

**LIMITATION.** `n_dead_tup` is an estimate; page-level bloat was not measured (`pgstattuple` is not installed and installing it is out of scope for a read-only pass).

### F-008 — No retention or purge path is verifiable from the baseline

**FACT.** The program's §9/§22/§90 require an explicit retention policy and purge subsystem; the baseline cannot confirm that any exists, and several tables grow monotonically with no deletion history.

**MEASUREMENT.**
- tables with `n_tup_del = 0` and no lifecycle owner identified: `research_events` (ins=87,951), `news_entities` (ins=61,375), `model_governance_events` (ins=59,180), `shadow_decisions` (ins=55,342), `news_topics` (ins=42,403), `research_gates` (ins=27,984)
- `audit_signals` carries a 7-day retention window in its query predicates yet shows `n_tup_del` = 4,581 against `n_tup_ins` = 13,372

**REPRODUCTION.** `table_stats` probe: `n_tup_del` is 0 for almost every high-volume table.

**ROOT CAUSE.** Unknown ownership. Sec.27 forbids purging on age alone and sec.93 makes 'unknown retention requirement' a hard stop.

**CHANGE.** None.

**BEFORE.** No purge measurement exists.

**AFTER.** PENDING — blocked. This finding cannot be resolved by measurement alone.

**TRADEOFF.** Sec.22 requires dry-run candidate counts and protected-row exclusion before any purge is enabled.

**TEST.** Sec.93 hard stop: **unknown retention requirement / unknown data ownership**. The Phase 2 synthesis must produce a per-dataset lifecycle owner before any purge work is authorised.

**LIMITATION.** Not measurable from catalog statistics: the presence of a retention window in one query's predicate does not prove a purge job exists.

### F-009 — shadow_decisions is the largest physical disk reader despite being an observability table

**FACT.** The table with the highest `heap_blks_read` (110,853) is a shadow/observability table, and it is also the single largest relation in the database.

**MEASUREMENT.**
- `shadow_decisions` size 94 MB, `n_live_tup` = 55,342
- `heap_blks_read` = 110,853 (highest of all relations), `heap_blks_hit` = 144,167
- `last_autovacuum` = 2026-09-27T09:56:26, `autovacuum_count` = 1
- `idx_scan` = 110,878 against 55,342 rows

**REPRODUCTION.** `statio` + `footprint` + `table_stats` probes filtered to `shadow_decisions`.

**ROOT CAUSE.** Not yet established. Shadow/challenger records are persisted at a volume and row width that makes them the dominant storage and read consumer, but no consumer of that history has been identified.

**CHANGE.** None.

**BEFORE.** 94 MB / 110,853 blocks read.

**AFTER.** PENDING.

**TRADEOFF.** Shadow data feeds model-governance and promotion evidence; sec.74 forbids reducing critical evidence. Any change must prove governance consumers survive.

**TEST.** Sec.37 decision-storage contract test applied to shadow rows: can the record answer what/why/when/outcome, and does any consumer read it?

**LIMITATION.** This is a HIGH-VALUE suspicion, not a proven defect. 'Largest table' is not 'unnecessary table' — sec.34 requires the consumer count first.

### F-010 — A disabled subsystem still owns ~25 MB of persisted events

**FACT.** `factory.enabled = false` in application settings, yet `factory_events` holds 25 MB (17.8 MB of it TOAST) and `factory_*` tables are present.

**MEASUREMENT.**
- `factory.enabled` = `false` (source `USER_SETTINGS`, value_type str)
- `factory_events` 25 MB total, 17.8 MB TOAST, 4,745 rows
- `factory_candidates` 7704 kB, `factory_failures` 3504 kB
- `factory_events.n_tup_ins` = 4,745 (all rows inserted before the subsystem was disabled)

**REPRODUCTION.** `footprint`/`table_stats` probes cross-referenced with the `application_settings` table in `app_settings.db`.

**ROOT CAUSE.** Data from an experimental/feature-flagged subsystem was never given a lifecycle once the flag was turned off.

**CHANGE.** None.

**BEFORE.** 25 MB + 7704 kB + 3504 kB of factory data.

**AFTER.** PENDING.

**TRADEOFF.** The subsystem can be re-enabled (`factory.enabled` is `HOT_RESTRICTED`, not removed), in which case the history has value again.

**TEST.** Sec.34/§48: identify consumers, then a dry-run candidate count per this finding's tables before any action.

**LIMITATION.** A disabled flag is not proof the data is unread — batch research jobs may still query it. Consumer proof is required first.

### F-011 — PostgreSQL is the sole runtime provider, but SQLite is NOT merely settings storage

**FACT.** The provider was chosen deliberately by the operator, so there is no silent provider fallback in play. **However, an earlier revision of this finding wrongly claimed SQLite was confined to settings storage - see C-1, which measures a multi-GB SQLite footprint.**

**MEASUREMENT.**
- `database.provider` = `postgresql` (source **USER_SETTINGS**, not a wave)
- `database.provider_transition_state` = active `postgresql`, target `postgresql`
- `ai_provider_decisions.db` 24,576 bytes with **0 rows** (orphan candidate)
- the settings store `app_settings.db` is 155,648 bytes / 5 tables, `quick_check = ok`
- **correction (C-1):** `artifacts/audit.db` 416.4 MB is the engine's audit write target, `artifacts/news.db` 230.3 MB, plus a 1.25 GB `audit_backup_*.db` set

**REPRODUCTION.** `baseline_collect_sqlite.py` over the actual runtime store paths, plus the `application_settings` table read.

**ROOT CAUSE.** N/A for the provider choice. The SQLite volume is a separate lifecycle question: no owner, no retention, and three near-identical backups on a live path.

**CHANGE.** None.

**BEFORE.** SQLite footprint: **~2.6 GB** across the repo's `artifacts/` stores and backups (see C-1), not the ~213 KB an earlier revision reported from a too-shallow sweep.

**AFTER.** PENDING.

**TRADEOFF.** None.

**TEST.** Sec.44 SQLite WAL/restart tests and sec.72 SQLite acceptance now apply in full, and the 1.25 GB backup set needs an owner under sec.49/§90.

**LIMITATION.** The settings store is definitively located; the domain stores' runtime homes are established for `artifacts/` but the split between the two checkouts' copies is not yet disambiguated.

---

## 5. Data value scorecard (sec.10)

Explicit categories, no arbitrary numeric score. `UNKNOWN` is a finding, not a
placeholder.

Table                      | Size    | Live    | Class                 | Evidence / note
---------------------------|---------|---------|-----------------------|----------------
shadow_decisions           | 94 MB   |  55,342 | MODEL-CRITICAL?       | governance evidence; consumer UNKNOWN
news_articles              | 80 MB   |  25,144 | OPERATIONAL           | raw ingest, 70% TOAST; news.enabled=1
research_gates             | 51 MB   |  27,984 | MODEL-CRITICAL        | promotion gates; 4,917 updates
news_analysis              | 45 MB   |  20,374 | DERIVED               | 2,825 deletions already; derived from articles
research_evidence          | 44 MB   |  19,228 | MODEL-CRITICAL        | append-only evidence, 0 deletions
model_governance_events    | 40 MB   |  59,180 | AUDIT-CRITICAL        | 59,180 events; 13 MB of indexes
strategy_registry          | 37 MB   |   4,113 | DECISION-CRITICAL     | 76% TOAST; 997 updates
audit_experiences          | 30 MB   |   9,940 | AUDIT-CRITICAL        | 71% TOAST
factory_events             | 25 MB   |   4,745 | DISPOSABLE-CANDIDATE? | owner subsystem disabled
research_events            | 25 MB   |  87,951 | AUDIT-CRITICAL        | 87,951 rows, 9.4 MB indexes
audit_signals              | 12 MB   |   7,453 | AUDIT-CRITICAL        | F-001 subject; 4,581 deletions
audit_orders               | 2808 kB |   2,457 | AUDIT-CRITICAL        | F-003 subject
audit_experience_outcomes  | 6448 kB |   3,471 | DERIVED               | F-004 subject

Every `UNKNOWN` / `?` entry above is a sec.93 gate: it must be resolved in
Phase 2 before any retention or purge action on that table.

---

## 6. What Phase 1 did NOT establish

Stated plainly so Phase 2 does not inherit a false sense of coverage:

- **No query inventory (sec.8).** No `Q-<DOMAIN>-NNN` IDs exist yet. F-003 and
  F-004 are counter-level, not query-level.
- **No EXPLAIN plans (sec.13).** No plan was captured, because the engine was
  down and the real access paths were not yet known. Plan capture belongs to the
  Phase 2 query map, where each query has a named code location and caller.
- **No workloads W1–W10 (sec.11) and no fixtures (sec.12).** No synthetic
  dataset was built and no benchmark was run.
- **No p50/p95/p99 (sec.15).** Blocked by the instrumentation gap in section 2.
- **No purge dry-run (sec.22).** Blocked by F-008's ownership gap.
- **No growth rate (sec.9).** A single snapshot is not a rate; two samples over a
  documented interval are required.
- **No latency-by-layer (sec.56) and no UI/SSE request budget (sec.30/§31).**
  The engine was not running.

---

## 7. Phase 2 entry conditions

1. Decide the instrumentation question (section 2). Either enable
   `pg_stat_statements` via `shared_preload_libraries` + restart as a controlled
   change with its own rollback, or add the driver-boundary query aggregator in
   `src/nexus_scalp/database/query_logging.py` and enter it from both drivers.
   Without one of these, sec.15, sec.53 latency rows and sec.56 stay
   NOT MEASURABLE and the program cannot satisfy its own definition of done.
2. Start the engine and capture the first post-#561 window (F-002 / Phase 12),
   since a fix with no runtime observation is not verified.
3. Resolve the sec.93 ownership stops before any retention work: `shadow_decisions`
   (F-009), `factory_*` (F-010), and every table with `n_tup_del = 0` (F-008).
4. Build the sec.8 query inventory for the F-003 / F-004 counters so those
   findings become query-level rather than table-level.

## 8. Hard stops declared by this baseline (sec.93)

- unknown retention requirement — no purge may be authorised yet (F-008)
- unknown consumer — `factory_*` and `shadow_decisions` lifecycle unresolved (F-009, F-010)
- performance claim without measurement — PR #561 benefit is unverified in
  production (F-002)
- silent provider fallback — none found; provider is an explicit USER_SETTINGS
  choice (F-011)
- benchmark disconnected from production path — n/a; no benchmark attempted

## 9. Corrections, supersession, and cross-reference to prior audits

This section is appended, not back-edited: the errors it records were in an
earlier revision of this document and are corrected here with their evidence, so
the audit trail shows what changed and why.

### C-1 — CORRECTION: the SQLite footprint was understated by ~12,000x

The first SQLite sweep used `-maxdepth 4` from `$HOME`. That matched the repo's
`data/*.db` **0-byte placeholders** and missed the real stores under
`artifacts/`, which sit deeper. The initial claim of ~213 KB total was wrong.

Measured footprint of the SQLite stores that matter:

Path                                                           | Size
---------------------------------------------------------------|--------
`NexusTradingForexBot/artifacts/audit.db`                      | 416.4 MB (live write target)
`NexusTradingForexBot/artifacts/backups/audit_backup_*.db` (x3)| 1.25 GB (three near-identical backups)
`NexusTradingForexBot/artifacts/news.db`                       | 230.3 MB
`NexusTradingForexBot/artifacts/strategies.db`                 | 29.1 MB
`NexusTradingForexBot/artifacts/candle_intel.db`               | 10.5 MB
`nse-review-main/artifacts/audit.db`                           | 415.4 MB (second checkout)
`nse-review-main/artifacts/news.db`                            | 230.3 MB

**Consequence for F-011.** The earlier statement that SQLite is confined to
settings storage with almost no runtime surface left is **withdrawn**. SQLite
carries the engine's audit write target plus a 1.25 GB backup set. Sec.72 SQLite
acceptance and the sec.44/§49 lifecycle questions apply in full, and the backup set
is an unowned growth source in its own right.

### C-2 — SUPERSESSION: the prior audit's P0 provider split is STALE at main

The 2026-09-28 forensic audit's headline P0 ('R2') states that
`AuditRepository.log_signal()` begins with `if not self._is_sqlite: return`, so the
engine writes audit data to SQLite while the UI polls PostgreSQL. **At the current
`origin/main` that is no longer true.**

Verified at `adbf05af` (`src/nexus_scalp/adapters/database/audit_repository.py`):

- `log_signal` (line 3922) has **no** component/config early return; it proceeds
  straight to building the record and writing it.
- `self._write_plane = self._build_write_plane()` (line 442), started at line 537
  and flushed at lines 3263-3267 -> an `AuditWritePlane`
  (`adapters/database/audit_write_plane.py`) is the non-SQLite write path.
- CHG-0067 converted the non-SQLite **read** gates from fail-silent defaults to
  fabric read-plane routing, with `provider_read_degraded_total` plus a
  rate-limited structured warning when no plane is registered.
- `_is_sqlite` still appears 54 times, but the remaining guards are routed or
  explicitly observable rather than silent.

**Consequence.** R2 must not be re-opened as an open P0, and the earlier F-011
phrasing implying a live write/read split is withdrawn. What *remains* true and
measurable is narrower: PG `audit_signals` holds 7,453 live rows against an id
sequence at 1.95M, while SQLite `audit.db` holds 11,266 rows whose newest entry was
89.3 h old at the prior audit - two stores with different freshness, which is a
reconciliation question, not a provider-split blocker.

### C-3 — REFINEMENT of F-003: audit_orders is the churn + index-scan hotspot

`audit_orders` is not merely index-scan-heavy; it is the **most rewritten table in
the database**:

- `n_tup_ins` = 8,799 but `n_tup_del` = 44,394 against only
  2,457 live rows -> the table is being rewritten wholesale
- `idx_scan` = 3,229,612 and `idx_tup_fetch` = 61,661,025
  (25,096 fetches per live row)
- every other table's insert:delete ratio is under 3x; this one is ~5x with a
  live set smaller than either counter

This makes F-003 the strongest sec.24/§28 lifecycle candidate in the baseline and
raises its priority well above what its row count suggests.

### C-4 — REFINEMENT of F-005: the duplicate index pairs ARE present

The first F-005 revision reported '0 duplicate groups' because it compared full
index **definitions**, which differ by the `UNIQUE`/`PRIMARY` keyword. Comparing
**key columns** per table finds 4 pairs, matching the prior audits:

table                      | key columns       | reclaimable plain copy          | bytes
---------------------------|-------------------|---------------------------------|--------
`news_analyzed_hashes`     | (article_hash)    | `idx_news_analyzed_hashes_hash` | 2,940,928
`news_junk_hashes`         | (article_hash)    | `idx_news_junk_hashes_hash`     | 1,187,840
`audit_experience_outcomes`| (idempotency_key) | `idx_exp_outcome_key`           | 303,104
`release_metadata`         | (key)             | `idx_release_metadata_key`      | 8,192
**total reclaimable**      |                   |                                 | **4,440,064 (4.23 MB)**

Each plain copy is key-identical to a UNIQUE/constraint index on the same table, so
dropping it is lossless for uniqueness enforcement. Sec.49 still requires the DDL /
constraint / migration-reference / foreign-tooling gate before any drop.

Note the scan asymmetry that also argues for these: on
`audit_experience_outcomes` the *plain* copy carries 2,801,729 scans while the
unique constraint index carries 3,791 - i.e. the redundant index is absorbing
work the constraint index could serve.

### C-5 — REFINEMENT of F-008: deletion is rare, and concentrated in the small tables

Only **11 of 126 tables** record any deletion at all, and the tables with
zero deletions include the largest ones:

| table | inserts | live | total |
|---|---|---|---|
| `research_events` | 87,951 | 87,951 | 25 MB |
| `model_governance_events` | 59,180 | 59,180 | 40 MB |
| `shadow_decisions` | 55,342 | 55,342 | 94 MB |
| `research_gates` | 27,984 | 27,984 | 51 MB |
| `news_articles` | 25,144 | 25,144 | 80 MB |
| `research_evidence` | 19,228 | 19,228 | 44 MB |
| `candle_patterns` | 10,758 | 10,758 | 3120 kB |
| `audit_broker_orders` | 10,743 | 10,743 | 2552 kB |

This strengthens F-008: the storage that dominates the database sits on a pure
append path with no observed reclamation, while every one of the 11 tables that
*does* delete is under 15 MB. Lifecycle work should target the append tables, not
the churning ones.

### C-6 — CORROBORATION: three independent audits agree on the instrumentation gap

`postgresql-deep-performance-storage-audit-2026-09-27.md`,
`postgresql-master-performance-audit-2026-09-27.md` and
`postgresql-forensic-audit-2026-09-28.md` all independently report
`pg_stat_statements` absent with an empty `shared_preload_libraries`, and all three
mark their top-query table `N/A` rather than fabricating one. Section 2's
NOT MEASURABLE finding is therefore corroborated by three prior passes, not inferred
from a single probe.

Known drift between those three documents (unreconciled, and NOT inherited here):
database size 501 MB (both 09-27) vs 588 MB (09-28); buffer-hit ratio 98.7% /
98.21% / 99.9% across three different windows; index count 445 (user indexes) vs
281 (public-schema indexes) - a definitional difference, not a contradiction.

---

