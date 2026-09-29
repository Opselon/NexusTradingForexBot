# P2-00 — Instrumentation reconciliation and the two-shape read pathology

**Lane:** `agent/hermes/remediation-instrumentation`
**Base:** `9d035f3a`
**Evidence:** `baseline_pg_phase1b-*.json` (lane `rem-pg-base` @ `7d07a9b5`),
`audit_signals_reconcile.json`, `physical_readers.json` (this lane)
**Refs:** Part 4 §5, §6, §17, §39; PostgreSQL contract §2, §3, §8, §9, §11, §16

## FACT

### 1. The contract's `audit_signals` numbers and the Phase-1 baseline numbers are the SAME cumulative counter

| Source | seq_scan | seq_tup_read | ratio |
|---|---|---|---|
| PostgreSQL remediation contract §2 | ~533,577 | ~4.927e9 | 9,234 |
| Phase-1 baseline (measured `20:32:57Z`) | 3,220,584 | 3.0549e10 | 9,483 |

`pg_stat_database.stats_reset` is **NULL** on this cluster: statistics have never
been reset. On PostgreSQL 15+ the cumulative statistics collector persists across
restarts, so both readings describe the **same counter, accumulated over the same
period, sampled at two different moments**:

```text
30,548,840,111 / 4,927,000,000  = 6.20
     3,220,584 /       533,577  = 6.04
```

Two independent dimensions agreeing on ~6x is the signature of elapsed time, not
of changed behaviour. The contract's §2 reading is **older**; the Phase-1 reading
is **newer**. Neither is a post-fix measurement.

### 2. The counter cannot be attributed to fixed or unfixed code

`PR #561` merged `2026-09-29 02:00:20`. `pg_postmaster_start_time()` =
`2026-09-29 01:19:26`, i.e. the postmaster started **before** the merge. The
engine is not currently running on this host. Therefore **no part of either
counter window is known to contain post-#561 execution**, and no reading of them
can say whether the polling fix works:

```text
NOT MEASURABLE WITH CURRENT INSTRUMENTATION
  -> requires: engine running on a fixed build, with pg_stat_statements
     activated (or a stats reset with a recorded timestamp) so that a bounded
     post-fix window can be separated from the cumulative total.
```

### 3. The real defect shape is "every scan reads the whole table"

```text
30,548,840,111 seq_tup_read / 3,220,584 seq_scan = 9,483 rows per scan
table size at measurement                          = 8,791 rows (exact count)
```

Each sequential scan reads **the entire table** — on a 12 MB / ~8.8K-row table.
This is not "some reads are inefficient"; it is a query with no usable limiting
predicate being executed millions of times. 618,286 index scans exist alongside
it, so the table is read both ways.

### 4. The largest CPU/buffer consumer and the largest DISK consumer are different tables

`pg_stat_user_tables.seq_tup_read` (tuples) and `pg_statio_user_tables.heap_blks_read`
(8 KB disk blocks) rank **differently**, and the Phase-1 finding F-001 (by tuples)
and F-009 (by disk) are both correct about different things.

Top physical disk readers (`heap_blks_read x 8 KB`):

| table | heap_blks_read | disk read | heap_blks_hit | cache hit % |
|---|---|---|---|---|
| `shadow_decisions` | 393,757 | **3.15 GB** | 149,093 | **27.5%** |
| `news_analysis` | 33,425 | 261 MB | 1,097,727 | 97.0% |
| `research_gates` | 30,803 | 241 MB | 354,382 | 92.0% |
| `news_articles` | 29,818 | 233 MB | 392,150 | 92.9% |
| `audit_signals` | 14,087 | 110 MB | **4,187,221,635** | ~100% |

Total heap read from disk across the whole database: **5.22 GB**. Of that,
`shadow_decisions` alone is **3.15 GB = 60.4%**.

`audit_signals` is the opposite case: it reads only 110 MB from disk because it
is effectively fully cached, but it incurs **4.19 billion buffer hits**
(~34 TB of buffer traffic) and ~30.5 billion tuples. It burns **memory bandwidth
and CPU**, not disk.

## MEASUREMENT

```text
stats_reset                        NULL (never reset)
postmaster_start                   2026-09-29 01:19:26 +03:30
PR #561 merge                      2026-09-29 02:00:20
audit_signals live rows (COUNT *)  8,791
audit_signals n_live_tup           7,453   (stale estimate; n_dead_tup 128)
audit_signals n_tup_ins / del      13,372 / 4,581
audit_signals seq_scan             3,220,584
audit_signals seq_tup_read         30,548,840,111
audit_signals heap_blks_hit        4,187,221,635
audit_signals heap_blks_read       14,087
audit_signals idx_scan             618,286
audit_signals idx_tup_fetch        20,609,767
audit_signals data range           2026-09-21 .. 2026-09-28 (8 days)
shadow_decisions heap_blks_read    393,757  (3.15 GB)  worst cache hit 27.5%
all tables heap disk read          5.22 GB
```

All values read live from `pg_stat_user_tables`, `pg_statio_user_tables`,
`pg_stat_database` and `SELECT count(*)` on the table itself. No values are
inferred.

## REPRODUCTION

```text
1. scripts/db_remediation/pg_instrumentation_probe.py    (lane rem-instr)
2. audit_signals_reconcile.py                            (scratch, read-only)
3. physical_readers.py                                   (scratch, read-only)
```

All three connect with `load_database_config("audit")` +
`build_postgres_url()` and run SELECT-only statements. They never print the
credential.

## ROOT CAUSE

Two independent root causes, previously conflated into one "audit_signals is
the problem" narrative:

1. **`audit_signals` — a repeated, unbounded, full-table read.** A query with no
   limiting predicate executed ~3.2M times, each reading all ~8.8K rows. The
   table is small, so this does not show up as disk I/O; it shows up as 4.19B
   buffer hits and ~30.5B tuples. The cost is CPU + buffer pool churn, invisible
   in any disk-centric metric.

2. **`shadow_decisions` — a poorly-cached, repeated large scan.** ~55K rows /
   ~86 MB heap, read 393,757 blocks from disk at a 27.5% hit ratio. Its working
   set does not fit or does not stay in a 128 MB `shared_buffers`, so repeated
   scans hit disk. This is 60% of all heap disk reads in the database.

Adding an index to `audit_signals` addresses (1) only if the missing predicate is
actually indexable — 618,286 index scans already happen, so the table is not
index-starved, it is query-shape-starved. (2) is a different fix entirely
(projection, caching strategy, or removing the scan), and would not be helped by
anything done to `audit_signals`.

## CHANGE

None. This is a read-only reconciliation. No DDL, no index, no config change, no
purge. `shared_buffers` (128 MB) is noted as context for the 27.5% hit ratio on
`shadow_decisions`, not as a recommendation — the PostgreSQL contract §14 forbids
changing planner/memory settings to hide application-level problems.

## BEFORE / AFTER

| Metric | Before | After | Delta | Evidence |
|---|---|---|---|---|
| attribution of contract §2 numbers | ambiguous (assumed post-P1) | same counter, older sample | clarified | ratio 6.20 / 6.04 |
| stats window | unknown | never reset (NULL) | identified | `pg_stat_database` |
| rows per seq scan on `audit_signals` | not computed | 9,483 of 8,791 | full-table every time | 30,548,840,111 / 3,220,584 |
| largest disk consumer | `shadow_decisions` (unquantified) | 3.15 GB = 60.4% of 5.22 GB | quantified | `pg_statio_user_tables` |
| `audit_signals` cost locus | assumed disk | 110 MB disk vs 4.19B buffer hits | corrected | `pg_statio_user_tables` |
| post-#561 effect | assumed measurable | NOT MEASURABLE | instrument gap | postmaster < merge |

## TRADEOFF

Reconciling by ratio rather than by timestamp means the conclusion rests on the
premise that both sources read the same counter. That premise is supported by
three independent facts (NULL `stats_reset`, PG15+ persistence, and agreement in
two dimensions) but is not proven by direct sampler instrumentation, because none
exists. If the contract's numbers came from a **different** database or a
**reset** cluster, the reconciliation would be invalid and this finding would need
to be re-derived.

## TEST

The reconciliation is a one-shot measurement, so it cannot be guarded by a
regression test. What *is* testable and now tested in CI
(`tests/unit/test_pg_query_logging.py`, 12 new tests):

- percentiles are accurate when not subsampling and stay within a statistical
  bound when subsampling;
- a bound operation outranks the generic driver verb without discarding it;
- repository rollups aggregate their operations;
- concurrent records are not lost (8 threads x 5,000 records);
- the aggregator performs no per-query persistence.

The two DB probes are read-only scripts and must be re-run, not re-derived, when
a real post-fix window exists.

## LIMITATION

1. The "P1 fixed it" claim is **not falsified** — it is **unverifiable** with the
   current instrumentation, because no post-fix window exists. Stated as such.
2. `n_live_tup` (7,453) disagrees with `count(*)` (8,791) by 15%, so the planner's
   row estimate for `audit_signals` is stale. Any plan-based reasoning must use
   the exact count until `ANALYZE` runs.
3. `shadow_decisions`' 27.5% hit ratio is lifetime-cumulative; a coincident 86 MB
   table against a 128 MB `shared_buffers` shared with 125 other tables makes a
   low ratio plausible, but the ratio is not proof of the mechanism.
4. Per-statement attribution of either pathology remains unavailable until
   `pg_stat_statements` is activated; table-level attribution cannot say *which*
   query caused 4.19B hits.
