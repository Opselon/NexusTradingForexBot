# AUDIT-0012 — P0 `pg-read` slow query: experience attribution join

**Date:** 2026-10-01 · **Severity:** P0 (live `[PG-QUERY] slow query` warning) · **Status:** fixed by PR (see taskboard row `P0-PG-QUERY-EXEC-ID-JOIN`)

## The warning

```text
[warning] [PG-QUERY] slow query domain=pg-read op=query
duration_ms=359.00 threshold_ms=200.00 rows=315

SELECT o.execution_id, e.experience_id, e.strategy_id, e.strategy_version,
       e.model_id, e.model_version, e.feature_schema_id, e.feature_dimension
FROM audit_experience_outcomes o
JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key
WHERE o.execution_id IN (?,?,?, ... hundreds of placeholders ...)
```

## Trace (Phase 1)

- SQL owner: `src/nexus_scalp/accounting/core.py` → `AccountingCore._attach_identity`
  (the strategy-attribution enrichment of `load_trades` report rows).
- Input: broker tickets collected from the ledger report rows (up to
  `MAX_TRADE_ROWS`); already chunked at 400 placeholders per statement but
  **not deduplicated** (a ticket can appear on multiple ledger rows).
- Aliases: `o` = `audit_experience_outcomes` (PK `outcome_id`, UNIQUE
  `idempotency_key`, **no index on `execution_id`**), `e` = `audit_experiences`
  (UNIQUE `idempotency_key`).
- The join is 1:1 in practice: 547 rows out of 545 distinct execution ids on
  the live DB.

## Forensics (Phase 2, live nexusdb, PostgreSQL 17.10)

Row counts: 22,132 experiences / 4,391 outcomes.

**Baseline plan (no `idx_exp_outcome_exec`) — both tables Seq Scan:**

```text
Hash Join  (actual ... rows=547)
  -> Seq Scan on audit_experience_outcomes   Filter: execution_id = ANY(...)   Buffers: 1,1xx
  -> Hash  -> Seq Scan on audit_experiences (22,132 rows)                      Buffers: 1,463
cold median: 11.04 ms, 2,577 shared buffers
```

**After `idx_exp_outcome_exec` on `audit_experience_outcomes(execution_id)`:**

```text
Hash Join
  -> Index Scan on idx_exp_outcome_exec (BitmapHeapScan of matching outcomes)  Buffers: 131
  -> Hash  -> Seq Scan on audit_experiences                                    Buffers: 1,640
```

The outcomes probe collapses to an index scan; the planner *keeps* a bounded
Seq Scan on `audit_experiences` and probes the join key with the existing
UNIQUE index `audit_experiences_idempotency_key_key`. A composite/covering
`audit_experiences(idempotency_key, ...payload)` index was measured (see
below) and **rejected**: it only wins at hypothetical ≥1,000-ticket probes and
costs ~14 MB plus every-write overhead.

## Scale evidence (isolated scratch DB `nse_scale_probe`, dropped afterwards)

The live DB (22k rows) is too small to show the win — both plans run <11 ms
there. The warning's 359 ms was I/O-dominated (cold cache / concurrent load),
and the Seq-Scan baseline grows **linearly with the table** while the indexed
plan stays flat. Cold-cache, 315 tickets (the exact warning shape):

| experiences | outcomes | no index (prod today) | with `idx_exp_outcome_exec` |
|---:|---:|---:|---:|
| 22k (live today) | 4.4k | 11.04 ms | 6.15–7.9 ms (plans equivalent at this size) |
| 50k | 50k | 20.5 ms | 4.6 ms (**4.5×**) |
| 100k | 100k | 30.3 ms | 6.0 ms (**5.0×**) |
| 200k | 200k | 55.3 ms | 4.3–6.5 ms (**8.5–12.9×**, zero Seq Scans) |

1,000-ticket probe at 200k rows: 55.3 → 9.6 ms (5.6×) with the index; the
bounded `audit_experiences` Seq Scan remains the dominant cost there, which is
why a second index was benchmarked and declined.

**SQLite sensitivity** (same migration chain, 10k experiences / 5k outcomes):
`EXPLAIN QUERY PLAN` flips from `SCAN o` (no index) to
`SEARCH o USING INDEX idx_exp_outcome_exec` (with index) — asserted by
`tests/unit/test_p0_outcome_execution_id_index.py` (11 tests: fresh install,
v11→v12 upgrade, single emission, plan shape, dedup, edge cases).

## Write-amplification check (required pass criterion 8)

`audit_experience_outcomes` is written ~4.4k times lifetime (5–15 rows/day on
the live DB). A single-column btree on `execution_id` adds one small index
probe per insert (≈60 KB at today's size, ~4 MB at 200k outcomes) — negligible
and justified by the read-path win.

## Redundant-index finding (documented, deliberately NOT removed here)

`idx_exp_outcome_key` on `audit_experience_outcomes(idempotency_key)`
duplicates the table's UNIQUE constraint index
(`audit_experience_outcomes_idempotency_key_key`) — the same defect class
AUDIT-0011 removed for `release_metadata`. Dropping it measures faster on the
hot query (4.23 vs 11.03 ms at one point) but the effect is **noisy** at the
live table's size (warm-buffer runs swing 6–12 ms), and removal is a separate
schema decision (it would also need its own migration + manifest edit and an
SSOT pin bump). It is tracked as follow-up in the taskboard; this PR stays
focused on the root cause of the warning: the missing `execution_id` index.

## Code changes

- `database/registry.py` — `AUDIT-0012-experience-outcome-execution-id-index`
  (v11→v12): `_ensure_index idx_exp_outcome_exec` + verify + rollback +
  `ANALYZE` so the planner adopts the new path (mirrors AUDIT-0002/0010;
  index lives ONLY in the migration so schema replay emits it exactly once).
- `database/manifest.py` — `AUDIT_SCHEMA_VERSION` 11→12;
  `audit_experience_outcomes.indexes += idx_exp_outcome_exec` (SSOT).
- `accounting/core.py` `_attach_identity` — dedup tickets
  (`dict.fromkeys`, order-stable; the result map is `{ticket: row}` so
  duplicates were pure IN-list inflation), chunk size 400→500 as the
  documented `_IDENTITY_CHUNK_SIZE` constant. SQL text, join, columns,
  parameterization, and provider-aware `_query` routing are **unchanged**
  (provider-neutral; LATERAL was measured on SQLite: 1.50 ms vs 0.80 ms —
  rejected).
- Tests: new `test_p0_outcome_execution_id_index.py`; SSOT pins 11→12 in
  `test_l4_schema_lifecycle_indexes.py` + `test_database_platform_task_db.py`;
  dedup regression test in `test_accounting_core.py`.
- Benchmark: `scripts/perf/p0_execution_id_join_bench.py` (plan-structure
  assertions + cold-cache latency, deterministic where possible).

## Observability

`[PG-QUERY]` logging is an external shim — untouched. The fixed path keeps the
same op/rows/duration logging; after deploy the query is expected to log well
under the 200 ms threshold (target <50 ms margin at production scale).
