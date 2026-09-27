# NSE PostgreSQL Deep Performance, Storage & Backend Audit

Date: 2026-09-27 (Iran local time shown by PostgreSQL)
Scope: read-only audit of the live `nexusdb` PostgreSQL provider plus source inspection.
Provider routing: `load_database_config("audit")` resolved PostgreSQL at localhost:5432, database `nexusdb`, user `postgres`. No provider, schema, data, index, vacuum, or application setting was changed.

## Executive summary

1. The live database is 525,211,315 bytes (501 MB). It contains 125 user tables, 445 indexes, 62 sequences, no views/materialized views, and only the `plpgsql` extension.
2. Storage is concentrated in append-like operational/research/news data, not obvious heap bloat. The largest table is `shadow_decisions`: 86 MB heap, 7.1 MB indexes, 94 MB total, 55,342 rows. Its average sampled tuple is about 1,560 bytes and its payload column accounts for 108 MB of logical text across rows; this is legitimate wide shadow-decision evidence, but it is a strong lifecycle/retention candidate.
3. The next largest data sets are `news_articles` (79 MB total), `news_analysis` (45 MB), `model_governance_events` (40 MB), `research_gates` (29 MB), `research_evidence` (24 MB), and `research_events` (23 MB). The largest rows are driven by text/JSON-like serialized payload columns, not dead tuples.
4. Autovacuum and autoanalyze are current for the heavy tables. Dead tuples are effectively zero in the captured snapshot: `shadow_decisions` 0, `news_articles` 19/24,939, and all other top tables 0. This is not evidence for `VACUUM FULL`, CLUSTER, or blanket REINDEX.
5. Four duplicate exact-key indexes are confirmed by catalog definitions, costing about 8.0 MB combined: two redundant indexes on `news_analyzed_hashes`, two on `news_junk_hashes`, plus duplicate pairs on `audit_experience_outcomes` and `release_metadata`. These are review candidates, not automatic drop candidates.
6. Query attribution is materially limited because `pg_stat_statements` is not installed/enabled and PostgreSQL statement logging is not available from this audit. Query-level performance conclusions must therefore remain source- and plan-based until a measurement window enables query statistics.
7. Source inspection found concrete backend risks: `SELECT *` and OFFSET pagination in `audit_repository.py`, `SELECT *` in `accounting/core.py` and `reporting/queries.py`, and a bounded batch writer (`executemany`, up to 500 rows) in `audit_repository.py`. The batch writer is a positive pattern; the read paths need targeted measurement before rewriting.
8. The highest-value next step is observability plus a controlled retention/index review, not destructive cleanup or broad schema redesign.

## Evidence and method

Read-only probe scripts were run from `C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/`:

- `nse_pg_audit_probe.py` / `nse_pg_audit.json`: version, size, catalogs, relation statistics, activity, locks, settings, database I/O, and `pg_stat_statements` availability.
- `nse_pg_deep_probe.py` / `nse_pg_deep.json`: columns, constraints, indexes, relation settings, I/O, sampled tuple widths, activity, and vacuum progress.
- `nse_pg_focus.py` / `nse_pg_focus.json`: data composition, payload sizes, date ranges, duplicate counts, and one safe `EXPLAIN (FORMAT JSON)`.

The probes used catalog/statistics views and bounded aggregate/sample queries. No `EXPLAIN ANALYZE`, DDL, DML, VACUUM, REINDEX, setting change, or provider change was executed.

## Inventory baseline

| Metric | Observed value |
| --- | ---: |
| PostgreSQL | 17.10, x86_64 Windows 64-bit |
| Database size | 525,211,315 bytes / 501 MB |
| Schemas | `public` plus system `pg_toast`; no application schema beyond public |
| Tables | 125 user tables |
| Indexes | 445 user indexes/catalog count |
| Sequences | 62 |
| Views/materialized views | 0 / 0 |
| Extensions | `plpgsql` 1.0 only |
| max_connections | 100 |
| Sessions | 1 active client session during probe; 5 server background processes had no client state |
| Idle / idle-in-transaction | No idle client sessions and no idle-in-transaction session observed |
| Locks | No ungranted locks observed |
| Deadlocks | 0 in `pg_stat_database` |
| DB commits/rollbacks | 182,882 / 1,398 since stats reset (reset timestamp was null) |
| Buffer hit ratio | 9,844,598 hits / (9,844,598 + 125,786 reads) = approximately 98.7% |
| Temporary files | 2, 524,288 bytes |
| pg_stat_statements | Not available; extension absent and relation not found |
| track_io_timing | off |
| shared_buffers | 128 MB (`16384` 8-kB pages) |
| effective_cache_size | 4 GB (`524288` 8-kB pages) |
| work_mem | 4 MB |
| autovacuum / track_counts | on / on |

The connection and database user details above are operational facts from the active configuration; the password was resolved through the repository secret store and was not printed or persisted by the audit.

## Significant table report

Sizes are `pg_relation_size` heap, `pg_indexes_size`, and `pg_total_relation_size`; row counts are PostgreSQL statistics estimates (`n_live_tup`) unless otherwise stated.

| Table | Rows | Heap | Indexes | Total | Dead tuples | Main cause | Classification / recommendation | Risk |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| `shadow_decisions` | 55,342 | 86 MB | 7.1 MB | 94 MB | 0 | Wide decision records; sampled tuple ~1,560 B; payload text totals 108 MB | HOT/WARM evidence. Establish retention by run/model and archive policy before any purge. | Approval required for deletion |
| `news_articles` | 24,939 | 17 MB | 6.7 MB | 79 MB | 19 | Article title/summary/body and serialized metadata; sampled tuple ~1,950 B; total body text ~48.7 MB | WARM/COLD news history. Only 3 duplicate flags; 21,326 active. Do not equate inactive/old with purgeable. | Medium |
| `news_analysis` | 20,939 | 16 MB | 3.1 MB | 45 MB | 0 | Serialized analysis fields; sampled tuple ~1,874 B; analysis text/arrays ~51 MB; 474 rows share an `article_id` beyond one row | WARM analytical history. Investigate whether repeated analyses are intended; retain audit-relevant results. | Medium |
| `model_governance_events` | 59,166 | 27 MB | 13 MB | 40 MB | 0 | Append-only governance events plus three large indexes; payload avg ~162 B | IMMUTABLE/RETAIN. Index review is higher value than data deletion. | Low/Medium |
| `research_gates` | 26,140 | 22 MB | 5.4 MB | 29 MB | 0 | Result/content text, avg sampled row ~1,000 B; some result payloads up to 192,714 chars | COLD/RETAIN by research lineage. Large payload outliers need product-level review. | Medium |
| `research_evidence` | 18,316 | 18 MB | 4.6 MB | 24 MB | 0 | `content` avg ~938 B, maximum 192,714 chars; evidence is historical by design | COLD/RETAIN. Archive large evidence only with lineage and retrieval verification. | High |
| `research_events` | 82,726 | 15 MB | 8.4 MB | 23 MB | 0 | Append-only event records; two large indexes | IMMUTABLE/RETAIN or archive by completed run. One index has 0 scans in this snapshot. | Medium |
| `strategy_registry` | 4,109 | 8.0 MB | 680 kB | 17 MB | 0 | Very wide serialized strategy definitions; sampled tuple ~3,616 B; max sampled row 61,540 B | COLD/RETAIN. Review superseded strategy payload retention; no datatype change without compatibility proof. | Medium |
| `audit_experiences` | 5,239 | 2.4 MB | 2.1 MB | 15 MB | 0 | Wide immutable decision payload; sampled tuple ~2,344 B; payload avg ~2,982 chars | IMMUTABLE/RETAIN. Index-to-heap ratio is high; review unused index only after workload window. | High |
| `audit_signals` | 11,302 | 10 MB | 1.5 MB | 12 MB | 0 | Wide signal rows plus payload; append-like telemetry | HOT/WARM operational evidence. Retention may be appropriate, but not without audit policy. | Medium |
| `news_analyzed_hashes` | 23,497 | 5.1 MB | 5.6 MB | 11 MB | 0 | One unique and one exact duplicate hash index, each ~2.8 MB | Retain uniqueness constraint; review/drop only the redundant non-unique index in a controlled migration. | Low/Medium |
| `factory_events` | 4,449 | 6.2 MB | 776 kB | 8.0 MB | 0 | Wide event payloads; sampled max was 51,200 B | COLD/RETAIN by factory run; archive if operationally no longer queried. | Medium |
| `factory_candidates` | 4,108 | 6.5 MB | 1.0 MB | 7.6 MB | 0 | Wide candidate definitions; sampled tuple ~1,404 B | COLD/RETAIN by generation; no purge without lineage check. | Medium |
| `news_entities` | 61,374 | 5.7 MB | 1.3 MB | 7.2 MB | 0 | Normalized child rows, ~92 B sampled tuple | WARM; investigate FK/index coverage before growth accelerates. | Low |
| `news_ai_analysis` | 2,767 | 4.6 MB | 456 kB | 5.8 MB | 0 | Wide serialized AI analysis; sampled tuple ~1,589 B | WARM/COLD; retain if used for reproducibility. | Medium |
| `position_lifecycle_events` | 3,888 | 5.2 MB | 568 kB | 5.8 MB | 0 | Wide lifecycle event payloads, ~1,305 B sampled tuple | IMMUTABLE/RETAIN for trading correctness. | High |

The top 20 tables account for approximately 456 MB of relation total size by summing the catalog values; this is the primary storage concentration. The database is not large because of dead tuples: the captured top-table dead/live ratios were 0.0000 except `news_articles` at 0.0008 and `audit_account_snapshots` at 0.0002.

## Data lifecycle and composition findings

- `shadow_decisions` spans 2026-09-21 through 2026-09-22 and contains 55,342 records. Its large heap is legitimate shadow-evaluation history, but its narrow time range and high volume justify a product decision on completed-run retention/archive. No deletion is authorized by this audit.
- `news_articles` spans 2026-08-21 through 2026-09-27. It has 24,939 rows, 21,326 active rows, 3 rows marked duplicate, average body length 1,952.5 characters, and maximum body length 56,692. The 79 MB total is consistent with wide article text plus TOAST/index overhead, not bloat.
- `news_analysis` spans 2026-08-21 through 2026-09-24. There are 474 additional rows beyond one row per distinct `article_id`; this may be legitimate multi-run/provider history or duplicate analysis. It is a data-model question requiring lineage checks, not a deletion instruction.
- `research_evidence` and `research_gates` each have payload values up to 192,714 characters. These outliers are the strongest TOAST/wide-row candidates. Measure `pg_column_size(content/result)` distribution and access frequency before normalizing or externalizing.
- `shadow_decisions`, `model_governance_events`, `research_events`, and `position_lifecycle_events` are append-like and should default to IMMUTABLE/RETAIN unless an approved archive policy exists.
- No evidence supports calling any top table junk. The database has named archive/quarantine tables, but the audit did not infer that active records are purgeable from age alone.

## Index audit

The largest index observations were:

| Index | Table | Size | Scans | Assessment |
| --- | --- | ---: | ---: | --- |
| `idx_gov_events_model` | `model_governance_events` | 5.4 MB | 0 | Candidate for workload review; `(model_id, timestamp)` may be needed by rare governance queries. Do not drop from one snapshot. |
| `idx_gov_events_ts` | `model_governance_events` | 3.3 MB | 0 | Same caveat; timestamp-only access must be confirmed from source/workload. |
| `research_events_event_id_key` | `research_events` | 3.2 MB | 82,726 | Unique identity constraint; keep. |
| `idx_events_strategy` | `research_events` | 3.2 MB | 0 | Candidate for review after query-statistics window. |
| `news_articles_article_hash_key` | `news_articles` | 3.1 MB | 25,803 | Unique deduplication constraint; keep. |
| `model_governance_events_event_id_key` | `model_governance_events` | 2.9 MB | 59,166 | Unique identity constraint; keep. |
| `news_analyzed_hashes_pkey` | `news_analyzed_hashes` | 2.8 MB | 23,497 | Required uniqueness/PK; keep. |
| `idx_news_analyzed_hashes_hash` | `news_analyzed_hashes` | 2.8 MB | 725 | Exact duplicate of PK key `(article_hash)`; controlled drop candidate. |
| `shadow_decisions_shadow_decision_id_key` | `shadow_decisions` | 2.6 MB | 55,342 | Unique identity constraint; keep. |
| `idx_shadow_decisions_run` | `shadow_decisions` | 2.2 MB | 0 | Review with actual run-filter queries. |
| `idx_evidence_strategy_created_id` | `research_evidence` | 2.0 MB | 0 | It extends `(strategy_id, created_at)` with `evidence_id`; compare to the shorter index before any change. |
| `idx_gates_strategy_run` | `research_gates` | 2.0 MB | 0 | Review against actual research-run reads. |

Confirmed exact duplicate pairs from `pg_index`/`pg_get_indexdef`:

- `news_analyzed_hashes_pkey (article_hash)` and `idx_news_analyzed_hashes_hash (article_hash)`, 2.8 MB each.
- `news_junk_hashes_pkey (article_hash)` and `idx_news_junk_hashes_hash (article_hash)`, 1.16 MB each.
- `audit_experience_outcomes_idempotency_key_key (idempotency_key)` and `idx_exp_outcome_key (idempotency_key)`, 160 kB each.
- `release_metadata_pkey (key)` and `idx_release_metadata_key (key)`, 8 kB each.

Zero scans do not prove an index is useless: statistics coverage starts at `stats_reset = NULL` and workload/uptime history is not established. Recommendation: enable a bounded observation window, record index scans, and compare each candidate with query plans and constraint requirements. Do not drop primary/unique indexes.

No evidence-based missing-index recommendation is made. Foreign-key catalog inspection returned no declared foreign keys in `public`; this should be confirmed against the migration source before concluding the schema intentionally has none.

## Vacuum, bloat, planner, and I/O

- `autovacuum=on`, `track_counts=on`; the top tables had autoanalyze and autovacuum timestamps within the same morning as the probe. `n_mod_since_analyze` was 0 for the largest tables except 4 governance-event modifications.
- No vacuum was running at probe time, and no long-running client transaction or ungranted lock was observed.
- Heap bloat is not demonstrated. `pg_stat_user_tables.n_dead_tup` is near zero, and table size is explained by live wide rows. A precise bloat estimate would require a vetted bloat query/extension or page inspection; none was installed or executed.
- Cache behavior is healthy in this snapshot: approximately 98.7% database block-hit ratio. This does not prove every query is fast; it only indicates the captured workload was mostly cache-served.
- `shadow_decisions` and `model_governance_events` have the highest observed heap reads in `pg_statio_user_tables`, followed by `news_analysis`, `research_gates`, `research_evidence`, `news_articles`, and `research_events`. This aligns with their size and activity; it is not by itself a defect.
- Two temporary files totaling 512 kB are recorded. With `log_temp_files=-1`, query-level attribution is unavailable.
- `track_io_timing=off`; enabling it is a controlled observability change, not part of this read-only audit.

## Query and execution-plan audit

`pg_stat_statements` is absent, so no honest ranking by total execution time, mean latency, calls, rows, shared reads, temp blocks, or WAL can be produced. The required query report is therefore explicitly unavailable rather than fabricated.

The safe plan probe for `SELECT * FROM audit_ledger ORDER BY ticket DESC LIMIT 50` used a backward index scan on `audit_ledger_pkey`, estimated 435 rows and a 50-row limit. This specific pattern is not missing an ordering index. It was not run with `ANALYZE`, so no actual latency/buffer claim is made.

Concrete source-level query anti-patterns found:

| Source | Evidence | Impact / status |
| --- | --- | --- |
| `src/nexus_scalp/adapters/database/audit_repository.py:1376-1382` | `SELECT * FROM audit_broker_trades ... LIMIT ? OFFSET ?` | Full-row transfer and deep OFFSET degradation are possible; measure endpoint offsets and row width before changing the repository contract. |
| `src/nexus_scalp/adapters/database/audit_repository.py:3708-3716` | `SELECT * FROM audit_ledger ... LIMIT ? OFFSET ?` | Same; keyset pagination is a candidate only if callers navigate deep pages. |
| `src/nexus_scalp/adapters/database/audit_repository.py:3772-3776` | `SELECT * ... WHERE ticket = ?` | Point lookup; `SELECT *` may be acceptable if the API requires the full record, otherwise project columns. |
| `src/nexus_scalp/accounting/core.py:314-317, 787-790` | `SELECT *` on `audit_ledger` | Projection review candidate. |
| `src/nexus_scalp/reporting/queries.py:103-105, 137-140` | `SELECT *` across a join | Potential row/column overfetch; inspect report consumers and join cardinality. |
| `src/nexus_scalp/adapters/database/audit_repository.py:2301-2340` | Queue writer drains up to 500 rows and uses `executemany` grouped by query | Positive batching pattern; retain and benchmark under load. |
| `src/nexus_scalp/adapters/database/dead_letter_store.py:293-301` | `COUNT(*)` plus `ORDER BY id DESC LIMIT 1 OFFSET ?` | Bounded maintenance path, but OFFSET grows with configured retention; low priority unless dead-letter volume increases. |

No N+1 pattern, unbounded result path, accidental Cartesian join, or repeated identical request-level query was proven from source alone. Those require request traces or `pg_stat_statements` plus correlation IDs.

## Backend and repository assessment

The database driver architecture preserves provider separation: `DatabaseConfig` selects SQLite or PostgreSQL, and `PostgreSQLDriver` lazily imports psycopg, translates qmark placeholders, and uses the secret store. PostgreSQL-specific optimizations must remain in provider/dialect code or provider-specific migrations; shared repository SQL must continue to work on SQLite.

Positive findings:

- `audit_repository.py` batches queued writes and calls `executemany` rather than issuing every queued row as an independent transaction.
- PostgreSQL connections are configured through the provider driver and secret-store URL construction; this audit did not observe per-query connection creation in the live session.
- Current data writes are largely append-like and metadata timestamps/analyze maintenance are current.

Review candidates:

- Replace only demonstrated `SELECT *` paths with explicit projections. This needs SQLite/PostgreSQL contract tests and consumer shape checks.
- Add keyset pagination only to callers proven to request large offsets; preserve current API compatibility.
- Instrument repository operation name, duration, rows, provider, transaction/correlation ID without logging credentials or SQL parameters containing secrets.
- Capture worker/job/request correlation to attribute `shadow_decisions`, research, news, and governance writes.

## Root-cause matrix

| Problem | Root cause category | Evidence | Priority |
| --- | --- | --- | --- |
| `shadow_decisions` dominates heap | Data lifecycle / architecture | 94 MB total, 55,342 wide records, payload text total 108 MB | P1 retention/archival design |
| News and analysis occupy 124 MB combined | Data model / ingestion | `news_articles` 79 MB and `news_analysis` 45 MB; wide text/serialized fields | P1 lifecycle and duplicate-analysis review |
| Duplicate hash indexes | Database index | Exact catalog definitions and equal sizes | P1 controlled migration review |
| Zero dead tuples / healthy maintenance | Database maintenance | `n_dead_tup`, autovacuum/autoanalyze timestamps | No defect |
| Query cost cannot be ranked | Observability | `pg_stat_statements` absent; logging/temp attribution unavailable | P0 measurement gap |
| Possible read overfetch/deep pagination | Repository/query | Exact source locations above | P2 after workload evidence |
| Wide research payload outliers | Schema/data lifecycle | `content`/`result` maximum 192,714 chars | P2 design review |

## Safe and controlled recommendations

### P0 — critical measurement gap (LOW risk)

Problem: no `pg_stat_statements`, no `track_io_timing`, and no query correlation surface.

Evidence: extension absent; `pg_stat_statements` query failed; `track_io_timing=off`; no query logs supplied.

Solution: schedule a controlled observability change: enable `pg_stat_statements` through the approved PostgreSQL configuration/restart process, enable `track_io_timing` only if acceptable, and instrument repository operation names and correlation IDs. Capture a representative workload window before tuning.

Benefit: makes total-cost, latency, call-frequency, I/O, and backend attribution measurable. Storage impact is negligible; write impact is small and must be measured. PostgreSQL-specific; SQLite remains unchanged. Rollback: remove the instrumentation/configuration through the normal controlled change process.

### P1 — retention/archive design (MEDIUM/HIGH; explicit approval for deletion)

Problem: append-like shadow, research, news, governance, and telemetry data will grow predictably, but no historical growth series or approved retention policy was available.

Solution: define per-table HOT/WARM/COLD/IMMUTABLE/RETAIN policy; first implement preview/count/reporting and archive verification, then consider bounded purge only for approved non-audit data. Use dependency-aware batches of approximately 5,000 rows/transaction and verify counts/checksums/lineage. Never purge trading, risk, financial, governance, or audit history without explicit approval.

Expected benefit: controls future size and cache pressure; no immediate reduction should be claimed.

### P1 — duplicate index review (LOW/MEDIUM; controlled migration)

Problem: four exact duplicate pairs waste approximately 8 MB and add write/maintenance cost.

Solution: confirm each non-constraint index has no migration/tooling dependency and is redundant under the active workload; drop only the non-unique duplicate in a transactional migration with a rollback/recreate script. Keep primary/unique constraint indexes.

SQLite compatibility: leave SQLite schema untouched or express equivalent cleanup in its own provider migration only if separately justified. PostgreSQL-specific candidate.

### P2 — projection and pagination review (LOW/MEDIUM)

Problem: source contains `SELECT *` and OFFSET pagination.

Solution: trace response consumers, capture actual query plans and result widths, then introduce explicit projections and keyset pagination behind provider-neutral repository contracts. Add both PostgreSQL integration and SQLite contract tests.

Expected benefit: lower network/heap I/O and stable deep-page latency if the workload actually reaches large offsets. Do not change based on the source pattern alone.

### P2 — wide payload review (MEDIUM/HIGH)

Problem: research payloads reach 192,714 characters and several tables have multi-kilobyte average rows.

Solution: measure access frequency and TOAST/page behavior; consider immutable compressed/object archival or normalized/generated searchable fields only where query evidence justifies it. Do not change TEXT/JSON representation solely because it is large.

### P3 — schema/index lifecycle hygiene (LOW)

Keep an index inventory and review zero-scan indexes after a full workload period. Add indexes only from real predicates/join/order patterns and plans. Do not partition: the current database is 501 MB, no growth-rate history is available, and time-pruning/retention requirements have not been demonstrated.

## Growth model

A defensible daily/weekly/monthly growth rate cannot be calculated: PostgreSQL statistics have no historical size snapshots, `pg_stat_statements` is absent, and the observed timestamp ranges describe data age, not storage deltas. Estimates for 30/90/180/365 days are therefore intentionally not fabricated. Capture daily `pg_database_size`, `pg_total_relation_size` by relation, row counts, and index sizes for at least 14 days before projecting growth.

## Final decisions

Can be done safely after normal change review:

- Capture database/relation-size baselines daily.
- Enable approved query observability and repository timing/correlation instrumentation.
- Run `ANALYZE` on a targeted table only if a plan/statistics investigation shows stale statistics; no targeted need was found in this snapshot.
- Add or adjust projections/batching only with measured plan and contract evidence.

Requires controlled migration:

- Remove confirmed redundant non-unique indexes.
- Change pagination contracts or add provider-specific indexes.
- Change payload schema, archive layout, partitioning, materialized views, or autovacuum table settings.

Requires explicit user/business approval:

- Delete or purge historical news, research, shadow, telemetry, governance, financial, audit, or trading data.
- Change retention semantics or archive audit/financial records.
- Drop constraint-backed indexes, tables, or schemas.
- Change the active provider or provider routing.

## Verification status

VERIFIED by live read-only PostgreSQL execution: version, database size, inventory, relation sizes, relation/index statistics, autovacuum/analyze timestamps, connection/lock snapshot, cache/I/O counters, payload distributions, duplicate-index catalog definitions, duplicate counts, and the safe `audit_ledger` plan.

NOT VERIFIED / unavailable: query latency ranking, expensive-query attribution, `EXPLAIN ANALYZE` for production workload, historical growth rate, exact bloat percentage, application pool utilization over time, N+1 behavior under requests, and deadlock history beyond the current `pg_stat_database` counter. These require an approved observation window or application traces.
