# PHASE 2 — RANKED REMEDIATION MATRIX AND SYNTHESIS

**Program:** NSE Database & Data-Lifecycle Remediation (Part 4 execution contract)
**Lanes:** P2-01 .. P2-08 (8 read-only lanes) + P2-00 instrumentation reconciliation
**Baseline:** Phase 1 @ `7d07a9b5`; this lane @ `9d035f3a`
**Stats window:** 2026-09-24 16:07:19 +03:30 → capture (4d 14h 39m). Counters are
cumulative and were never reset; `pg_stat_database.stats_reset` is NULL but
`pg_stat_wal`/`bgwriter`/`checkpointer`/`io`/`archiver` all share 2026-09-24
16:07:19, which is the authoritative window anchor. Postmaster uptime is NOT the
observation period.

---

## 0. THREE PHASE-1 CLAIMS ARE OVERTURNED — READ THIS FIRST

The read-only phase did its job. Three claims that shaped the program's priorities
are refuted or corrected by measured evidence. They are recorded here rather than
silently dropped.

### O-1 — `audit_orders` is NOT a write-churn hotspot (refutes Phase-1 C-3)

Phase-1 C-3 reported 8,799 inserts vs 44,394 deletes vs 2,457 live rows and called
it "the strongest §28 growth/lifecycle candidate". **That was a counter artifact.**

- `n_tup_upd = 0`, `n_tup_hot_upd = 0`. There is no UPDATE, no `INSERT OR REPLACE`,
  no delete-then-insert anywhere in the write path. The sole INSERT is
  `INSERT ... ON CONFLICT(execution_id) ... DO NOTHING` (audit_repository.py:4123) —
  a conflict-suppressed append, never an upsert.
- The 44,394 decompose as **6,342 attributable + 38,052 counter carry-over**.
  The 6,342 are the one-shot R1B collapse on 2026-09-28 04:33:42, recorded verbatim
  in `db_operation_logs` (before 8,721 → after 2,379).
- The remaining 38,052 **cannot have been rows in this table**: `audit_orders_id_seq.last_value`
  is 8,799 and a sequence is never rewound by deletes. They are carry-over from the
  pre-migration SQLite ledger across the SQLite→PG re-basing.
- The table is declared `never_delete` / `TIER_1_CANONICAL_AUDIT` / `source_of_truth=True`
  and listed in `IMMUTABLE_TABLES`.

**One row = one order-LIFECYCLE EVENT, not one order.** 2,457 live rows come from
644 distinct `order_id` values (~3.8 events/order). It is append-only event history,
and that design is correct.

The real defect on this table is READ amplification (see R-6): 99.75% of its
61.66M `idx_tup_fetch` come from the primary key because **there is no index on
`order_id`**.

**Consequence for the program:** the "write amplification first-class hotspot"
framing is withdrawn. The §22/§23 state-vs-event analysis is closed: it is history.

### O-2 — `shadow_decisions`' 3.15 GB is NOT a read (corrects F-009's mechanism)

F-009 was right that `shadow_decisions` is the largest physical disk reader
(393,757 heap blocks = 3.15 GB, 60.4% of all heap disk reads, worst cache hit at
27.5%). The mechanism was wrong.

- A 130-second idle isolation window produced a heap_blks_read delta of **exactly 0**.
- `pkey idx_scan 57,003 ≈ n_tup_ins 55,342` — the scans are INSERT uniqueness probes.
- `pg_statio_user_tables` counts blocks touched by inserts, autovacuum and analyze
  as "read". With 128 MB `shared_buffers` against an 86 MB relation competing with
  125 others, every insert-path block is uncached.

It is an **insert-dominated, never-read relation**, not a table being hammered by
queries. This does not make it harmless — it means the fix is payload reduction and
retention, not indexing.

### O-3 — F-008's §93 hard stop is PARTIALLY resolvable

Phase-1 F-008 said there was no verifiable retention path. Production logs prove
otherwise: `Audit retention purge complete (pooled provider)` with per-table counts
appears 5 times on 2026-09-28 (e.g. `logs/info/2026/09/2026-09-28.log:2140`,
audit_signals=2520, position_moving=1024, 219 ms). The BUG-054 path is verifiable
and invoked on PostgreSQL.

The hard stop now stands only for the classes no code path owns: research, news,
factory, shadow, governance.

Also: the Phase-1 citation `audit_repository.py:3947` for a retention DELETE is
**stale** — line 3947 is inside the decision-recording payload block. The canonical
path is `purge_old_audit_data` at :5531, DELETEs at :5589/:5594/:5601.

### And two corrections to numbers I published

- The "1.25 GB of unmanaged backups" understated the set by ~2x. The real tree is
  **2,551,906,513 B (2.40 GiB)** across four producer families: `audit_backup_*.db`
  (1.31 GB), `audit_v7_*.bak` (434 MB), `news_v0_*.bak` (15 MB), and
  `hygiene-20260928/` (793 MB, including a 73 MB nexusdb dump).
- The 30.5B/4.9B `audit_signals` discrepancy (contract §2 vs Phase 1) is the SAME
  cumulative counter sampled at different times — ratio agreement in two dimensions
  (6.20 and 6.04), `stats_reset` NULL, PG15+ persists stats across restarts.
  Neither reading is post-PR-#561; that effect remains
  NOT MEASURABLE WITH CURRENT INSTRUMENTATION.

---

## 1. RANKED REMEDIATION MATRIX

Classification per the execution contract §64/§8: **NOW** / **NEXT** / **LATER** /
**DO NOT CHANGE**, on measured impact.

### NOW — Tier A, measured, low complexity, low risk

| # | Finding | Domain | Table(s) | Root cause | Measured impact | Decision | Cpx | Risk | Deps |
|---|---|---|---|---|---|---|---|---|---|
| R-1 | SSE health probe on the hot path | SSE/health | catalog (7 round trips) | `check_domain("audit")` runs unconditionally at 5 Hz per client with no memoization | **151.59 ms per call** = 76% of every 200 ms tick; ~35 round trips/s per client. This is the driver of the 30.5B | **REWRITE** (memoize on the existing `ledger_high_water_mark()` or TTL) | LOW | LOW | none |
| R-2 | `fetch_rows_bounded` does not bound on PostgreSQL | API v1 | `audit_signals` | LIMIT injected only on the SQLite branch; PG path does `fetchall()[:n]` | `/api/v1/signals/latest` (returns 1 row) **seq-scans all 8,791 rows**. Identical shape WITH a SQL LIMIT = Index Scan, 40 rows | **REWRITE** (inject LIMIT in SQL on both providers) | LOW | LOW | none |
| R-3 | PR #561's bounded tail does not bind | API/audit | `audit_signals` | `ORDER BY id DESC LIMIT 2000` sits inside a subquery whose `WHERE UPPER(action)='NO_TRADE'` selects ~92% of rows | Measured Seq Scan, 8,313 rows. The LIMIT is unreachable. Sibling `get_decision_stats` places its filter OUTSIDE the slice and is measured bounded | **REWRITE** (move the filter outside the bounded slice) | LOW | MED | none |
| R-4 | News dedup keyed on an unstable hash | news | `news_articles` | `article_hash` folds in a 60s bucket of `published_at` + first 2000 chars; stable key `(source_id, canonical_url)` has NO unique constraint | **21,841 of 25,144 rows (86.8%) are repeats of 3,303 distinct URLs**; 35.9 MB duplicated body; worst group 231 copies | **REJECT BEFORE INSERT** (key the guard on content identity; add the unique constraint) | MED | LOW | R-11 |
| R-5 | `body` column duplicates `summary` | news | `news_articles` | RSS adapter: `if not body.strip(): body = summary` (sources/base.py:213-217) | Byte-identical in **25,129/25,144 rows (99.94%)**; 49.5 MB TOAST on PG, ~48 MB in SQLite, read by **no** web route | **REDUCE** (stop duplicating; keep full text in `news_article_versions`) | LOW | LOW | none |
| R-6 | No index on `audit_orders.order_id` | audit | `audit_orders` | Only secondary composite index is `(ticket, order_id)`; 957 of 2,457 rows hold `ticket=0` | **99.75% of 61.66M `idx_tup_fetch` come from the PRIMARY KEY**; four `order_id`-led readers degrade to Seq Scan | **INDEX** (see §48 gate below) | MED | LOW | none |
| R-7 | `request_id` has no index | decision evidence | `audit_signals` | Equality probe on a unique key (`avg_width 36`, `n_distinct -1.0`) with no serving index, inside an N+1 loop over up to 2,000–10,000 records | **8,791 rows examined per call, per iteration.** PR #561's own artifact confirms: `tup/scan=9108.0` | **INDEX** (or resolve from the in-memory ledger) | LOW | MED | migration registry |
| R-8 | Four duplicate index pairs | schema | 4 tables | Dual SQLite/PG schema authoring with no dedup check; `IF NOT EXISTS` guards on name only | 4,440,064 B reclaimable; **the redundant side carries more scans in 3 of 4 pairs** (e.g. `idx_exp_outcome_key` 2,801,729 scans vs the UNIQUE index's 3,791). Every insert writes 2 identical b-trees | **REDUCE** (drop the 4 redundant sides) | LOW | LOW | none |
| R-9 | SQLite backups: 2.40 GiB, no owner | storage | `artifacts/backups/` | Two producers (CLI `db_commands.py:363` and HTTP `diagnostics_state_routes.py:2057`), **zero pruners, zero restore path, zero validation**. `artifacts/` is gitignored so git cleanup cannot see it | 2.40 GiB; measured **gzip 9.85x → 1.10 GiB recoverable losslessly** from three files alone. Two of the three `audit_backup_*` are the same snapshot 2 s apart | **ARCHIVE** (name an owner, retention cap on distinct snapshots, gzip) | LOW | LOW | none |
| R-10 | `shadow_decisions.payload` column | shadow | `shadow_decisions` | `save_decision` serializes the entire record into `payload` (store.py:589), duplicating all 45 relational columns | **55.25 MiB = 64% of the 86 MB heap**; avg 1,047 B/row on 55,342/55,342 rows. Its only reader is one endpoint that reads exactly ONE key and has no frontend caller | **REDUCE** (keep only the 5 fields not present relationally, ~200 B) | LOW | LOW | O-2 |

### NEXT — Tier A/B, larger blast radius or needs a decision

| # | Finding | Domain | Table(s) | Root cause | Measured impact | Decision | Cpx | Risk | Deps |
|---|---|---|---|---|---|---|---|---|---|
| R-11 | News is stored in BOTH PG and SQLite | news | `news_articles` + 17 | PG migration left `artifacts/news.db` (241 MB, 24,919 articles) as a live-looking artifact; hygiene worker and one console route still open it | 241 MB stale duplicate + duplicated connection surface. Last SQLite row 2026-09-25, PG continues to 09-28 — the two disagree | **MIGRATE/RETIRE** (archive once, repoint `MANAGED_DATABASES['news']` and `NewsConfig.db_path`) | LOW | LOW | R-4 |
| R-12 | `shadow_decisions` never resolves | shadow | `shadow_decisions` | `resolve_pending_outcomes` early-returns unless BOTH `active_run_id` AND `active_challenger` are set in the current process; runs belong to dead processes | **All 55,342 rows are `outcome_status='PENDING'`; `n_tup_upd=0`.** 15 outcome columns plus the entire §37 "what was the outcome?" question are unanswerable | **REWRITE** (relax the guard; add a startup pass resolving rows whose 120-min horizon closed) | MED | MED | R-10 |
| R-13 | 12 append-only tables, ~450 MB, no delete path | lifecycle | see below | The purge whitelist was written for the audit-telemetry tier only and never extended | `shadow_decisions` 98 MB, `news_articles` 86 MB (57.5 MB TOAST), `research_gates` 53 MB, `research_evidence` 46 MB, `model_governance_events` 42 MB, `strategy_registry` 39 MB, `factory_events` 26 MB, `research_events` 26 MB, + 4 more. All `n_tup_del = 0` | **ARCHIVE/PURGE** per §27 verdicts below | MED | MED | R-20 |
| R-14 | First safe purge target | lifecycle | `audit_guard_telemetry` | — | **7 rows of 596** past 13 days (~2 kB). Three independent policy declarations agree it is Tier-7 temporary state, `rebuildable=True`, not in `IMMUTABLE_TABLES`, already exercised in production | **PURGE** (with exact before/after count verification) | LOW | LOW | R-20 |
| R-15 | `operator/decisions` 500s on PostgreSQL | operator | `audit_signals` | SQLite's `datetime('now', ?)` hardcoded in a provider-agnostic route (operator_routes.py:401); `translate_sql` does not rewrite it | `UndefinedFunction: text >= timestamp` — the frontend ALWAYS passes `hours`, so the failure is the default path | **REWRITE** (provider-correct SQL via `translate_sql`) | LOW | LOW | none |
| R-16 | Operator surface reads SQLite, dashboard reads PG | operator | `audit_signals` | `_audit_db_path()` hardcodes the SQLite default (operator_routes.py:131) while v1 resolves through `DatabaseConfig` | Two stores with different freshness (SQLite 11,266 rows vs PG 8,791) shown to the operator as the same data | **MIGRATE** (anchor to the same read plane as v1) | MED | MED | R-11 |
| R-17 | `news_analysis_runs` retention has no index | lifecycle | `news_analysis_runs` | Retention rule written against a column that was never indexed | Purge predicate is a **Seq Scan, cost 841, est. 8,454 rows**. Same for `research_events` (cost 3,090) | **INDEX** (`started_at`) | LOW | LOW | R-13 |
| R-18 | 91 of 281 indexes never scanned; 32 on non-empty tables (10.76 MB) | schema | many | Speculative schema for queries the code never runs; `idx_gov_events_model` even declared in TWO files with different columns | `idx_gov_events_model` 5.65 MB (6.5% of the whole index portfolio) on 59,180 rows, 0 scans, drift +0 since Phase 1. Live reads use `event_id` (59,182 scans) | **REDUCE** (drop only where source grep proves the predicate absent) | LOW | MED | R-8 |

### LATER — Tier B/C, or blocked on instrumentation

| # | Finding | Decision | Why later |
|---|---|---|---|
| R-19 | `strategies.db` is the one SQLite file still receiving writes (newest row 41 h old) and sits OUTSIDE every maintenance allowlist | **KEEP** (do not purge) | Writer identity NOT ESTABLISHED — could be a provider-switch fallback bug. Must be named before any change |
| R-20 | Purge scheduler safety: in-memory throttle stamp, no run-lock, TEXT cutoffs | **REWRITE** | Design work; the same 7-day window returns 2,480 / 3,397 / 2,501 rows depending on how the cutoff is built. Persist run state into `hygiene_run_history` (columns already exist) |
| R-21 | 81 of 126 relations never vacuumed; all autovacuum settings at default | **DESIGN ONLY** | Correction to F-007: only **2 of 126** have never been analyzed and all >5 MB tables carry fresh analyze dates. The debt is VACUUMING, not ANALYZING. Highest dead-tuple ratio is 7.0% |
| R-22 | `db_operation_logs` is not an audit trail: 7 rows total, only the hygiene script records itself | **REWRITE** | 80% of the cluster's delete churn is unattributable after the fact. Resolved by the driver-boundary aggregator (already implemented) |
| R-23 | WAL: 9.03 GiB, checkpoint write 96 min, FPI 9.36%, `wal_compression=off` | **MEASURE FIRST** | Not one byte is attributable. Requires `pg_stat_statements` + `track_wal_io_timing` |
| R-24 | 4,575 SQLite-only `audit_signals` keys (40.6%) exist in neither live PG nor the migration; SQLite retains 3 days PG already purged | **ARCHIVE** | Restoring them re-introduces history the operator's own retention pruned. Needs a product decision |
| R-25 | The safest archiver in the repo (`research/archive.py`, verify-before-delete in one transaction) has never moved a single row | **PROVE FIRST** | The 365-day horizon exceeds all observed data age (oldest is 38.8 days). Exercise once at a short horizon on a copy before extending it |

### DO NOT CHANGE — rejected at the gate, with evidence

| Proposal | Verdict | Evidence |
|---|---|---|
| Composite index on `audit_signals (generated_at, action)` | **REJECTED as cargo-cult** | `generated_at >= now()-7d` selects 82.4%; `action='NO_TRADE'` selects 94.6%; both 77.9%. Only 6 distinct action values. **Forcing the index path with `enable_seqscan=off` measured SLOWER.** The genuine fix already shipped (bounded `ORDER BY id DESC LIMIT n` served by the pkey). The code comments record the reasoning verbatim |
| Partition any table | **REJECTED for want of a measured lifecycle argument** | 0 partitioned tables exist. Every measured purge volume is trivial (3,964 rows combined across the whole purge set) and every purge plan is already index-served and sub-5 ms. Partitioning would also make `audit_signals_pkey`, `idx_audit_signals_dedup` and `news_*_pkey` global indexes, destroying the detach-drop benefit |
| VACUUM the SQLite files | **REJECTED** | Reclaimable is 3.01 MB of 416.4 MB (0.69%) and 8.08 MB of 230.3 MB (3.35%). Not worth a full-file rewrite with 2x transient disk and an exclusive lock |
| `audit_orders` churn / retention work | **WITHDRAWN** | Premise refuted (O-1). It is immutable append-only history with `n_tup_upd=0` |
| Raise `shared_buffers` for `shadow_decisions` | **REJECTED** | Contract §14 forbids changing memory settings to hide application-level problems; the mechanism is insert-dominated (O-2) |
| Add FKs indiscriminately | **HOLD** | 0 FK constraints is a question, not proof of absence. Each relationship needs exists/required/safe/perf/migration analysis first |

---

## 2. §48 INDEX GATES — the two indexes actually justified

The contract requires a full gate for every proposed index. Only two pass.

### Gate for R-6: `idx_orders_order_id ON audit_orders (order_id)`

```text
QUERY-ID            Q-AUD-ORDER-001
Current plan        Seq Scan on audit_orders (4 reader shapes); Index Scan using
                    audit_orders_pkey + Filter for ticket/order_id OR shapes
Current cost        ~130 heap fetches per whole-table read (measured: 260 fetches / 2 scans)
Selectivity         order_id is near-unique: 644 distinct values / 2,457 rows (~3.8 rows/key)
Call frequency      15,816 pkey scans in the window; 99.75% of 61.66M idx_tup_fetch
Rows returned       1-4 per call (point lookups)
Alternative         None — no index serves order_id-only predicates. idx_orders_ticket
                    is led by ticket, which 957/2,457 rows hold as 0
Proposed index      CREATE INDEX idx_orders_order_id ON audit_orders (order_id)
Storage cost        ~150 KB estimated (scaling the measured idx_orders_ticket at 3,213,501
                    scans / 17,019 fetches on 2,457 rows)
Write cost          Negligible — 8,799 inserts over 4.6 days on an IMMUTABLE table
Expected benefit    order_id-led lookups ~1-3 fetches per call; seq_scan eliminated
                    for 4 reader shapes
Measured benefit    PENDING (read-only lane) — re-EXPLAIN the five shapes after creation
```

### Gate for R-7: `idx_audit_signals_request_id ON audit_signals (request_id)`

```text
QUERY-ID            Q-AUD-REQ-001  (experience/decision_evidence.py:128-135)
Current plan        Seq Scan on audit_signals, Rows Removed by Filter 8,791
Current cost        1,299 shared hits per call, per N+1 iteration
Selectivity         n_distinct = -1.0 (UNIQUE), null_frac 0, avg_width 36
Call frequency      N+1 over max_decisions=2000 orphans at startup; also 10,000-record
                    research/dataset.py loops
Rows returned       1 (ORDER BY id LIMIT 1)
Alternative         Resolve request_id from the write path's in-memory ledger —
                    PREFERRED, avoids the index entirely
Proposed index      CREATE INDEX idx_audit_signals_request_id ON audit_signals(request_id)
Storage cost        ~390 KB estimated (36 B key + 4 B tid, 8,791 rows)
Write cost          Negligible for a ~1 Hz append-only table
Expected benefit    1 row per probe vs 8,791 — ~8,791x per iteration
Measured benefit    PENDING — PR #561's own p1_after.txt confirms the unfixed cost
                    (tup/scan=9108.0) even after its "sargable" rewrite
Migration           Must go through the registry (AUDIT_SCHEMA_VERSION bump, manifest.py),
                    NOT hand DDL
```

---

## 3. §27 RETENTION SAFETY VERDICTS

| Class | Verdict | Basis |
|---|---|---|
| TIER-0/TIER-1 (audit_ledger, audit_orders, audit_executions, audit_broker_*, audit_account_snapshots, trade_decisions, risk_evaluations, audit_experiences, audit_experience_outcomes) | **NEVER DELETE** | `IMMUTABLE_TABLES` + `never_delete` + financial_aggregates invariant + incident tracer replay |
| `position_lifecycle_events` canonical classes (CREATED/OPENED/EXITED/EXPECTATION_CONFIRMED) | **NEVER DELETE** | incident tracer timeline consumer |
| `audit_signals` >7d | **SAFE (conditional)** | 2,480 of 8,791 rows; conditional on the §53/§54 boundary tests |
| `audit_guard_telemetry` >13d | **SAFE** | first purge target (R-14) |
| position churn classes >3d | **SAFE** | 1,657 rows; NOTE the durable filter targets POSITION_MOVING, which production never emits — a guaranteed no-op (BUG-295) |
| `model_runtime_health` >14d | **SAFE** | keep latest row; 1,255 of 2,015 |
| research_events, research_evidence, research_gates, news_articles + news_*, factory_*, shadow_decisions, model_governance_events | **UNSAFE / ARCHIVE-ONLY** | all have live model/research/replay/audit consumers |

---

## 4. THE SINGLE LARGEST MEASURED SOURCE OF UNNECESSARY WORK

Per the contract's optimization priority (§62: runaway reads rank #2, and #1 — runaway
writes — is refuted by O-1):

**`audit_signals` — 30,548,787,365 sequential tuples read from a table holding 8,791
rows, absorbed as 4,187,221,635 buffer hits (~34 TB of buffer traffic), 99.86% of the
cluster's `seq_tup_read`.**

The mechanism is now fully traced, and it is **two independent defects, not one**:

1. The SSE loop calls `get_system_state()` every 200 ms per client and every call
   unconditionally performs a 7-round-trip catalog health probe costing 151.59 ms
   (R-1). Change detection is rebuild-and-diff polling, not an event (R-1b).
2. `fetch_rows_bounded` does not bound on PostgreSQL (R-2), and PR #561's bounded
   tail does not bind because its filter selects 92% of rows (R-3).

Each scan reads the whole table: 30,548,840,111 / 3,220,584 = **9,483 rows per scan
on 8,791 rows.**

The highest-value change in the program is R-1 + R-2 + R-3, which together attack the
read path at the source. Per §96, this is "DO NOT CREATE THE WORK" rather than
"MAKE THE WORK FASTER" — no index is involved.

---

## 5. INSTRUMENTATION STATUS (what §97 items 7 and 17 still need)

| Instrument | Status | Value |
|---|---|---|
| `pg_stat_statements` | **ABSENT** (available in build, v1.11, not installed) | `shared_preload_libraries=''` (postmaster context) |
| `track_io_timing` | **ABSENT** | `off` (superuser, no restart) |
| `track_wal_io_timing` | **ABSENT** | `off` (superuser, no restart) |
| `log_min_duration_statement` | **ABSENT** | `-1` (superuser, no restart) |
| `track_functions` | **ABSENT** | `none` |
| `auto_explain` | **ABSENT** | not in this build |
| `compute_query_id` | present | `auto` |
| `track_counts` | present | `on` |

**Delivered this phase:** the driver-boundary aggregator in
`src/nexus_scalp/database/query_logging.py` now supplies p50/p95/p99 (bounded
reservoir), operation/repository attribution (§51), measured overhead
(+1.74 µs/record enabled, 0.18 µs disabled, 135 KB vs 16.8 MB bounded worst case),
and a §49 no-persistence assertion. 12 new tests, 46 total passing.

**§75 NO SILENT FALLBACK: not triggered.** Every provider transition site was
inspected; all raise or log (`config.py:499-513`, `provider.py:55-62`,
`provider_lifecycle.py:339-358`). The one silent path (`config.py:476-481`) is the
documented first-run unset case. The parallel SQLite stores are a split-write
finding, not a fallback.

---

## 6. DECISIONS BY LANE (contract §12 — every finding ends actionable)

| Lane | Decision | Basis |
|---|---|---|
| P2-01 audit_orders | **INDEX** | churn refuted; read amplification is the live defect |
| P2-02 SQLite surface | **ARCHIVE** | 2.40 GiB backups unowned; `shadow_decisions` 56.4% of audit.db and 100% PG-duplicated |
| P2-03 news ingestion | **REJECT BEFORE INSERT** | 86.8% duplicates; gate exists but is under-used |
| P2-04 decisions | **REDUCE** | payload is 64% of the heap and read by nobody; §60 proven release-safe |
| P2-05 hot reads | **REWRITE** | SSE probe + unbounded PG path + non-binding LIMIT |
| P2-06 index/lifecycle | **REDUCE** | 4 duplicate pairs + 32 zero-scan indexes; composite REJECTED |
| P2-07 retention | **PURGE** | first target named with a six-point safety argument |
| P2-08 write amplification | **REDUCE** | largest waste is the read path; §75 clear |

---

## 7. WHAT REMAINS NOT MEASURABLE

Stated per §70 rather than fabricated:

- **PR #561's runtime effect** — postmaster started before the merge, engine never ran
  since. Requires a live post-fix window.
- **Per-statement attribution of anything** — `pg_stat_statements` absent. The
  driver-boundary aggregator supplies operation-level attribution but not
  buffer/temp/WAL attribution.
- **38,052 of 44,394 `audit_orders` deletes** — historical, gone from every recording
  instrument. `db_operation_logs` holds 7 rows total.
- **Per-page SQLite fill factor** — needs `sqlite3_analyzer`/`dbstat` payload
  sampling, absent on this host.
- **Physical block-level dedup among the six ~436 MB audit objects** — needs a
  CoW-aware tool. If the volume supports reflink the real unique footprint is far
  below 2.6 GB.
- **p50/p95/p99 and page-level bloat** — until Stage 1 + Stage 2 of the
  instrumentation plan land (`PG-INSTRUMENTATION-MAINTENANCE-PLAN.md`).

---

## 8. RECOMMENDED WAVE ORDER (contract §64 multi-agent integration)

```text
Wave A (now, zero-risk, no schema change)
  R-1  SSE health memoization            <- highest value in the program
  R-2  fetch_rows_bounded LIMIT on PG
  R-3  move the NO_TRADE filter outside the bounded slice
  R-15 operator/decisions dialect fix    (correctness; 500s on PG)
  R-9  backup retention cap + gzip       (storage, 1.10 GiB lossless)

Wave B (schema, additive only)
  R-6  idx_orders_order_id               (§48 gate above)
  R-7  idx_audit_signals_request_id      (via the migration registry)
  R-8  drop 4 duplicate indexes
  R-17 idx on news_analysis_runs.started_at

Wave C (storage reduction)
  R-5  stop duplicating summary into body      (-49.5 MB TOAST)
  R-10 shadow_decisions.payload reduction      (-55 MB heap)
  R-4  news dedup stable key + unique constraint
  R-11 retire artifacts/news.db               (-241 MB)

Wave D (lifecycle)
  R-14 first purge: audit_guard_telemetry >13d (7 rows)
  R-20 purge scheduler safety (persisted state, run-lock, timestamptz cutoffs)
  R-13 retention for the 12 append-only tables, per §27 verdicts
  R-24 SQLite-only audit_signals archival

Wave E (instrumentation, enables the rest)
  Stage 1: track_io_timing + track_wal_io_timing + log_min_duration_statement
           (superuser context, NO restart, reversible in seconds)
  Stage 2: shared_preload_libraries + CREATE EXTENSION pg_stat_statements
           (one restart in a maintenance window — authorization requested)

Wave F (integration / E2E / CI / merge / post-merge verification)
```

Waves A and E-Stage-1 can run immediately with no authorization. Wave E-Stage-2
needs the restart decision. Wave B needs the migration registry.

---

## 9. OPEN QUESTIONS FOR THE OPERATOR

1. **Restart authorization** for `pg_stat_statements` (Stage 2). I am not an
   administrator, but `postgresql.conf` is writable and `pg_ctl.exe` is present.
   Or: run the elevated service command yourself.
2. **`strategies.db` writer identity** — it is the only SQLite file still receiving
   writes (41 h ago) and it is in no maintenance allowlist. Is the post-cut-over
   writer a provider-switch fallback bug or a deliberate SQLite-only subsystem?
   Nothing may be purged until this is answered.
3. **Retention windows** for the 12 append-only tables are proposals, not
   measurements. The consumer horizon is proven short (news readers use ≤7 days;
   shadow_decisions has no reader at all), but the windows are a product decision.
4. **The 4,575 SQLite-only `audit_signals` keys** (3 days of history PG already
   purged) — restore-and-let-it-reprune, or accept the loss?

---

## 10. SIGN-OFF (contract §91)

```text
OWNER:        Hermes remediation-instrumentation lane (Phase 2 synthesis)
SCOPE:        8 read-only discovery lanes + instrumentation reconciliation,
              against the Phase-1 baseline at 7d07a9b5
ROOT CAUSE:   audit_signals read amplification = SSE health probe at 5 Hz +
              unbounded PG fetch path + non-binding LIMIT; NOT an index problem.
              audit_orders churn = counter carry-over, refuted.
CHANGES:      driver-boundary aggregator (percentiles, §51 attribution, §49
              no-persistence); PG instrumentation maintenance plan. NO schema,
              index, purge or data change was made by any Phase-2 lane.
MEASURED:     151.59 ms/probe; 8,791 rows/scan; 86.8% news duplicates;
              55.25 MB payload; 2.40 GiB backups; 4,440,064 B duplicate indexes;
              aggregator +1.74 us/record, 135 KB memory.
REGRESSIONS:  46 tests pass in tests/unit/test_pg_query_logging.py (12 new).
KNOWN LIMITS: PR #561 effect unmeasurable; per-statement attribution absent;
              38,052 historical deletes unattributable; p50/p95/p99 pending
              instrumentation Stage 1+2.
FOLLOW-UP:    Waves A-F above; the four open questions in §9.
STATUS:       READY FOR INTEGRATION
```
