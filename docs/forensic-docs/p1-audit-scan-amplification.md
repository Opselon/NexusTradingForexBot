# P1 — `audit_signals` Scan-Amplification Root-Cause Correction

Status: implemented on `agent/hermes/p1-audit-scan-amplification`.
Companion evidence: `docs/forensic-docs/postgresql-forensic-audit-2026-09-28.md` (the
forensic baseline this wave acts on) and the probes in
`scripts/forensics/p1_scan_amplification/`.

---

## 1. Root cause

**One sentence:** the SSE control-center loop (`server.py`, `asyncio.sleep(0.2)`
→ `get_system_state()` → `get_recent_predictions(40)`), the `/api/v1/decisions/stats`
and `/api/operator/summary` / `/funnel` / `/no-trade` routes, and the
`incidents/trace.py` diagnostic all issued *predicates that selected ~100% of the
table*, so PostgreSQL correctly chose a sequential scan every single time —
and four consumers polled at 0.2 s / 15 s / 60 s cadence against a 9,108-row
table that carries a 7-day retention window, so a window equal to the whole
retention period is a full-table scan by definition.

**Why ~533,577 seq scans read ~4.927 billion tuples over 18.11 h against only
~9,108 live rows:**

* The table is small (9,108 rows ≈ 1,872 heap pages ≈ 16 MB). Each scan read
  the whole table: **9,108 tuples / 1,872 pages per scan** — measured
  (evidence: `p1_attr_before.txt`).
* 533,577 scans / 65,196 s ≈ **8.2 scans/sec**. The SSE loop alone runs at
  5 Hz per connected client; the remaining cadence comes from the operator and
  v1 API routes plus additional connected UI clients (the forensic environment
  serves multiple consoles simultaneously).
* 4,927,831,046 tuples / 533,577 scans ≈ **9,235 tuples per scan** — the whole
  table, every time.

**Why the existing indexes did not solve it:** three indexes exist on
`audit_signals` — the `id` primary key, a unique `signal_dedup_key`, and
`idx_audit_signals_generated (generated_at DESC)`. None of them could help,
because the problem was never "no access path" — it was **no selectivity**:

| Shape | Predicate | Selectivity | Planner's only option |
| --- | --- | --- | --- |
| Q1 | `generated_at >= <7d ago>` | ~99.5% of rows | seq scan (index would be *slower*) |
| Q2 | `id IN (SELECT id ... LIMIT 20000)` | 20000 > 9108 → 100% | seq scan + hash semi-join |
| Q3 | `action = 'NO_TRADE'` | ~92% of rows | seq scan (majority predicate) |
| Q4 | `payload LIKE '%x%'` | 0% (leading wildcard) | seq scan + materialize 855 B/row |

A b-tree cannot profitably serve a predicate that matches every row. The
`generated_at` index *exists and was never used* — on Q1's measured plan the
filter removed only **44 of 9,108 rows** (`Rows Removed by Filter: 44`), so the
planner's seq-scan choice was correct and any index hint would have made the
query slower. **No index can rescue a predicate that selects the whole table.**

---

## 2. Query map (producer → consumer → defect)

| # | Query shape | Code location | Caller / cadence | Business purpose | Rows returned (before) | Rows examined (before) | Final strategy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Q1 | `generated_at >= ?` + `GROUP BY action, decision_stage` | `adapters/database/audit_repository.py` stats helper → `web/api_v1/decisions.py` `/stats` | dashboard poll, 1/min | recent decision distribution | 9 | **9,108** | bounded latest-N tail (2000) + optional `group_by_reason` |
| Q2 | `id IN (SELECT id ... LIMIT 20000)` + `WHERE id IN (...)` | `web/operator_routes.py` `/api/operator/summary` | operator console poll, 1/15s | census strip (action distribution) | 9,108 | **9,108 (+9,108 for the id list)** | one `ORDER BY id DESC LIMIT 2000` query, IN-list removed |
| Q2b | same, `LIMIT 50000` | `web/operator_routes.py` `/api/operator/funnel` | operator console poll, 1/15s | terminal-stage distribution | 9,108 | **9,108 (+9,108)** | same bounded-tail rewrite |
| Q3 | `WHERE action='NO_TRADE'` (×5 queries) | `web/operator_routes.py` `/api/operator/no-trade` | operator console poll, 1/15s | NO_TRADE forensics breakdown | ≤ 50 rows | **5 × 9,108** | one bounded tail read, aggregations computed in Python |
| Q3b | `COUNT(*) WHERE action='NO_TRADE'` | `adapters/database/audit_repository.py` `count_decisions` | `/decisions/no-trade` + `/decisions/stats` | headline NO_TRADE count | 1 | **9,108** | count over the bounded slice; vacuous `id <= (SELECT MAX(id))` subquery removed |
| Q4 | `WHERE ticket=? OR payload LIKE '%..%'` | `incidents/trace.py` `why_blocked` | `nexus incident` CLI (on demand) | "why was this trade blocked?" | ≤ 20 | **9,108** | `request_id` equality first; LIKE is the bounded, explicit last resort |
| Q4b | `ORDER BY id DESC LIMIT 40` | `get_recent_predictions` ← SSE `get_system_state` | **5 Hz per client** | newest decision stream | 40 | 40 (already index-served) | unchanged query — **a high-water-mark memo removes ~98% of the calls** |

**Classification (Phase 3):** Q4b is the only true HOT path. Q1/Q2/Q2b/Q3/Q3b
are WARM (dashboard/operator polls at 15–60 s). Q4 is COLD (manual CLI).

---

## 3. New architecture

### HOT — high-water-mark memo (the SSE path)

`get_recent_predictions(40)` was already a bounded, index-served tail read (11
heap pages). Its problem was **call frequency**: 5 Hz per client, and the
predictions section is a pure function of the ledger's newest rows.

`server.py::_recent_predictions_cached(app, engine)` now memoizes that section
on the ledger's **high-water mark** (`SELECT max(id)`):

```text
poll @5Hz
  → SELECT max(id)              (cheap, index-only)
  → unchanged?  return memo     (no tail read at all)
  → moved?      read newest 40, refresh memo
```

Measured on a static ledger: **50 tail reads over 10 s → 1** (the first one).
On a 1 Hz decision cadence the tail read happens ~1×/s instead of 5×/s per
client, and is skipped entirely on every idle cycle.

`id` is monotonic and the ledger is append-only, so `max(id)` cannot regress —
the memo key cannot serve a stale tail. The memo is per-app (server lifetime),
never per-request, and stores a defensive copy so a caller cannot mutate it.
`hw is None` (empty/unreadable ledger) is treated as a **miss**, not a hit: the
memo may hold rows from before a truncate, so it is reset to empty.

### WARM — bounded latest-N at the repository layer

Every aggregation now reads a bounded tail slice, bounded at the source so no
caller can restore the unbounded shape:

```sql
SELECT ... FROM audit_signals ORDER BY id DESC LIMIT 2000
```

The planner serves this with `Index Scan Backward using audit_signals_pkey`
(measured) — it reads 2000 rows / 412 pages and stops, and it **cannot grow into
a scan** because the LIMIT is in the statement itself.

* `audit_repository.py::get_decision_stats(hours_back, sample_rows, group_by_reason)`
  — one bounded read serves both the per-`decision_stage` distribution (`/stats`)
  and the per-`reason_code` breakdown (`/no-trade/reasons`). `sample_rows` is
  hard-capped by a module constant, so a caller passing `10**9` still gets 2000.
* `operator_routes.py` — the `id IN` semi-join is replaced by a single bounded
  query. `_SUMMARY_COLUMNS` / `_FUNNEL_COLUMNS` are projected adaptively so the
  bounded read stays valid on ledgers that predate `decision_stage` /
  `blocked_by`.
* `count_decisions` — the vacuous `id <= (SELECT MAX(id) FROM audit_signals)`
  subquery is gone; the count runs over the bounded slice.

### COLD — sargable first, substring last

`why_blocked` tries `WHERE request_id = ?` (index-served) and falls back to the
bounded `payload LIKE` **only when the equality found nothing** — i.e. for rows
written before the correlation key existed. The old `WHERE ticket=? OR payload
LIKE '%..%'` is gone entirely: `ticket` is not even a column on `audit_signals`,
so the OR's first branch matched nothing and the LIKE branch did all the damage.

---

## 4. Query contracts (hot paths)

| Query | Purpose | Max rows | Ordering | Freshness | Cursor/state | Max window | Selectivity | Duplicates |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `SELECT max(id)` | memo key | 1 | n/a | per-poll | the memo | n/a | index-only | n/a |
| `ORDER BY id DESC LIMIT 40` | newest decision stream | **40** | newest-first | ≤ 200 ms (memo) | high-water mark | 40 rows | 40/9108 = 0.4% | no |
| `ORDER BY id DESC LIMIT 2000` | decision distribution | **2000** | newest-first | per-request | none (snapshot) | 2000 rows | 2000/9108 = 22% | no |
| `WHERE request_id = ? ... LIMIT 20` | diagnostic lookup | **20** | newest-first | on demand | none | 20 rows | ~1 row | no |
| `payload LIKE ... LIMIT 20` | legacy fallback | **20** | newest-first | on demand | none | 20 rows | bounded scan | no |

Every hot query now has an explicit maximum result size stated in the SQL. No
query in this set can silently evolve into an entire-table read, because the
bound is a literal in the statement rather than a caller-supplied "window"
larger than the table.

---

## 5. Database evidence (measured)

Isolated PostgreSQL 17 instance, representative fixture: 9,108 rows, 1,872 heap
pages, NO_TRADE ≈ 92% — matching the forensic production shape. Statistics read
on fresh connections (`pg_stat_clear_snapshot()` inside one implicit transaction
returns stale zeros).

### Metric 1 — sequential-scan growth, 50 iterations of each path

| Path | BEFORE seq_scan Δ | BEFORE seq_tup_read Δ | AFTER seq_scan Δ | AFTER seq_tup_read Δ |
| --- | --- | --- | --- | --- |
| Q1 stats (by stage) | +50 | +455,400 | **+0** | **+0** |
| Q1 stats (by reason) | +50 | +455,400 | **+0** | **+0** |
| Q2 operator summary | +50 | +455,400 | **+0** | **+0** |
| Q2b operator funnel | +50 | +455,400 | **+0** | **+0** |
| Q3 count NO_TRADE | +50 | +455,400 | **+0** | **+0** |
| Q3b no-trade reasons | +50 | +455,400 | **+0** | **+0** |
| Q4b recent predictions | +0 | +0 | +0 | +0 |
| SSE hot path (5 Hz × 10 s) | 50 tail reads | — | **1 tail read** | +0 |

**Every fixed path eliminated sequential scans on `audit_signals` entirely.**

### Metric 2/3 — rows examined per useful row

| Path | examined (before) | returned (before) | ratio | examined (after) | returned | ratio |
| --- | --- | --- | --- | --- | --- | --- |
| Q1 stats | 9,108 | 9 | **1012:1** | 2,000 | 9 | **222:1** |
| Q2 summary | 9,108 + 9,108 | 9,108 | 2:1 | 2,000 | 2,000 | **1:1** |
| Q3b no-trade reasons | 9,108 | ≤50 | ≥182:1 | 2,000 | ≤50 | ≤40:1 |
| Q4b predictions | 40 | 40 | 1:1 | 40 | 40 | 1:1 (calls ÷50) |

### Metric 5 — plan shape

| Path | BEFORE | AFTER |
| --- | --- | --- |
| Q1 | `Seq Scan` (Filter removed 44/9108), `shared hit=1872` | `Index Scan Backward using audit_signals_pkey`, `shared hit=412` |
| Q2 | `Hash Semi Join` over two `Seq Scan`s, `shared hit=3713` | `Index Scan Backward using audit_signals_pkey`, `shared hit=412` |
| Q4b | `Index Scan Backward`, `shared hit=11` | unchanged (`shared hit=11`), called 50×→1× |

### Metric 4 — result boundedness

Every hot query carries a literal `LIMIT` in its statement (40 / 2000 / 20),
and the repository cap is enforced inside the helper, not trusted from the
caller. Pinned by `tests/unit/test_p1_audit_scan_amplification.py`.

### Plan evidence

Full `EXPLAIN (ANALYZE, BUFFERS, VERBOSE)` for every before/after shape:
`scripts/forensics/p1_scan_amplification/p1_before.txt` and `p1_after.txt`.

---

## 6. Indexes — why none were added

**Zero indexes were added or modified in this wave.**

Each of the four shapes was proven to be a *predicate* defect, not an access-path
defect, so an index would have been pure dead weight:

1. **Q1** — `idx_audit_signals_generated (generated_at DESC)` already covers the
   `generated_at` predicate and the planner still chose a seq scan, because a
   7-day window on a 7-day-retention table matches ~99.5% of rows. An index would
   make the query *slower*. Fixing the shape (bounded tail) made the **existing
   primary key** the correct access path.
2. **Q2** — the semi-join's inner and outer relation are the same table; the
   planner's seq-scan + hash join was optimal for "join the table to itself". No
   index can make a self-join cheaper than not doing it. Removing it did.
3. **Q3** — `action='NO_TRADE'` matches ~92% of rows. A b-tree on a majority
   predicate is never used (the planner ignores it as unselective), and a bitmap
   scan would read the whole heap anyway. The fix is the bound, not the index.
4. **Q4** — leading-wildcard `LIKE '%x%'` is non-sargable to *any* index type
   that could serve it cheaply at this scale. The correct fix was to stop asking
   the question on the hot path and make it a last-resort fallback.

The only index the hot path needs — the `id` primary key — already existed. The
bounded-tail rewrites made it *usable*; it was never the bottleneck.

---

## 7. Remaining work (explicitly P2, out of scope here)

From the forensic report, intentionally **not** touched by this wave:

* duplicate/overlapping index cleanup (storage audit findings)
* `news_articles.body` storage
* `ai_provider_decisions` schema
* broad schema cleanup / archival
* retention changes
* the raw-SQLite bypass in `operator_routes.py::_connect_ro` — this wave bounded
  the queries it issues, but the operator routes still open a direct SQLite file
  connection rather than going through the shared read plane. Routing them
  through `fetch_rows_bounded` / the PG read plane is a **P2 lifecycle change**
  (it changes which database a live operator console reads), deliberately left
  out of a performance wave.

---

## 8. Risk assessment

| Dimension | Before | After | Risk |
| --- | --- | --- | --- |
| **Ordering** | summary/funnel returned rows in an arbitrary semi-join order; `rows[0]` was read as "latest" but the IN-list fetch was unordered and returned the **oldest** row of the window | `ORDER BY id DESC`, newest-first; `latest_decision_at` is now the **max** of the ledger's own timestamps, never `rows[0]` | strictly more correct — the old code could report the oldest decision as the latest |
| **Freshness** | every poll re-read the whole table | bounded tail + memo | bounded query results reflect the newest 2000 rows, not the whole history; the summary's window is 2000 rows ≈ 33 h at 1 decision/min |
| **Pagination** | none (no offset support) | none (bounded latest-N) | no change; these endpoints are latest-N by design, not paginated |
| **Duplicates** | n/a | memo returns a defensive copy | no duplicate rows possible; the memo cannot serve stale content because `max(id)` is monotonic |
| **Missed rows** | n/a | memo misses nothing: a new row always bumps `max(id)`, forcing a refresh | none |
| **NO_TRADE counts** | exhaustive over all history | bounded to the newest 2000 rows | the headline number is now a recent-window count, not an all-time count. This is the intended semantic: the dashboard asks "what is the system doing lately", not "how many NO_TRADE have ever occurred" |
| **Concurrent writers** | n/a | memo key is `max(id)`; the ledger is append-only | no reads of partial writes; `id` is assigned atomically by the primary key |
