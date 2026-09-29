# Post-Merge Verification — Database & Data-Lifecycle Remediation (PR #566)

Merge commit: `7c93017e` on `main` (squash-merged from
`agent/hermes/db-lifecycle-remediation`, based on `origin/main` `0db0c23e`).
Verified in a pristine `origin/main` worktree against the **live local
PostgreSQL 17.10** instance (provider resolved as `is_sqlite: False`),
not a mock and not SQLite.

## 1. Merge and source convergence

```
origin/main tip: 7c93017e Database & Data-Lifecycle Remediation: value gate, archival lifecycle, provider-split read plane (#566)
```

| artifact | on `main` |
|---|---|
| `ingest.deduplicator._gate_duplicate_payload` | yes |
| `NewsDatabase.archive_duplicate_payloads` | yes |
| `news/body_resolution.py` | yes |
| `news_articles.payload_archived` / `payload_archived_at` columns | yes |
| raw `sqlite3.connect` bypass in `operator_routes` | gone (only the intentional `file:...?mode=ro` read-only SQLite path remains) |

Fresh-install schema convergence on PostgreSQL: all four schema columns
(`payload_archived`, `payload_archived_at`, `article_status`,
`published_at_source`) present.

## 2. Post-merge tests on `main`

```
803 passed, 2 skipped
```

across `tests/unit/news/`, `test_operator_routes.py`, `test_fabric_guards.py`,
`test_retention_payload_archival.py`, `test_database_hygiene_task11.py`.
Full CI on the merged branch: **all contexts green** (Code Quality & Tests,
Py Tests windows + macOS, CodeQL, Migration safety, OSV, Trivy, docs,
dependency drift).

## 3. The archival ran on real data (PostgreSQL)

This is not a fixture. The live database held duplicate bodies that predate
the gate.

| metric | value |
|---|---|
| rows in `news_articles` | 25,145 |
| rows the archival marked `payload_archived = 1` | **17,953** |
| duplicate `body` bytes reclaimed | **44,090,347 (44.1 MB)** |
| archived rows whose `summary` is empty (data loss) | **0** |
| unarchived rows with an empty `body` (inconsistency) | 3,163 — pre-existing summary-only articles, untouched by the archival |

The reclaimed volume exceeds the 28.5 MB read-only-probe estimate: the
probe measured a subset of rows, and the actual eligible set was larger.
Both numbers describe the same defect at different sampling.

Read-through after archival, on a real archived row:

```
archived row read-through: 'Federal Reserve Board issues enforcement action with SouthPo'
still non-empty (no capability lost): True
```

`summary` survived; the analysis consumers see identical text.

## 4. Retention verdicts

```
verdict old  (30 days): ARCHIVE
verdict fresh (1 day):  KEEP
```

`never_delete` and `archive_after_days` are now independent: the row is
protected, the duplicate payload is not.

## 5. Idempotency on the live database

```
real run eligible/archived/bytes_before: 17953 / 17953 / 44090347
idempotent 2nd run archived/rounds: 0 / 1
```

A second run reclaimed nothing and terminated after one (empty) round.

## 6. Physical storage

`VACUUM (ANALYZE) news_articles` cleared the 3,734 dead tuples the archival
created and refreshed statistics; dead tuples went to 0 and `n_live_tup`
was unchanged at 25,145.

Heap size did **not** shrink. This is expected and correct: PostgreSQL
marks the freed TOAST space reusable rather than returning pages to the OS
(only `VACUUM FULL`, which is not a routine fix and locks the table, would).
The space is observed to be reused: inserting 200 further articles into the
archived table grew the heap by 0.0 MB, because the new rows consumed the
reclaimed TOAST chunks.

**The honest storage claim is therefore about the write path, not the file
size.** Measured on the production write path
(`canonicalize_item` → `insert_article`) on PostgreSQL:

```
rows: 200 x 38 B duplicate text
  PRODUCTION PATH body bytes persisted : 0
  PRODUCTION PATH summary bytes        : 7600
  body bytes the pre-gate path would have written: 7600
  duplicate body bytes avoided: 7600
  article rows readable: True
  read-through produces the full text: True
```

100% of the duplicate body is never written. The existing 44.1 MB is
reclaimed and the table stops accumulating more.

## 7. No unintended data loss

| check | result |
|---|---|
| archived rows with empty `summary` | 0 |
| rows deleted | 0 (the row lifecycle is `never_delete`) |
| `article_hash` identity | unchanged — the fingerprint hashes the raw payload pre-gate |
| read-through byte-identity | identical to the pre-gate joined text |
| unanalyzed articles archived | none — eligibility requires analysis |
| non-duplicate bodies archived | none — the duplicate test gates it |

## 8. Regression of P0/P1 behaviour

`test_fabric_guards.py` passes — the P0 pool-ownership ratchet and the
sqlite3-bypass guard are intact. `operator_routes` now routes through the
fabric's provider-aware read plane (the P1 architecture), and its 725
route tests pass on PostgreSQL.

## 9. NOT verified here

* `VACUUM FULL` space reclamation — deliberately not run (table lock; not
  a routine fix). The reclaimed space is reusable, which is the property
  the workload depends on.
* 10M-row scale point — the archival scan is a bounded keyset cursor whose
  cost is linear in the rows it touches and independent of total table
  size, but asserting 10M-row behaviour without running it would violate
  the no-fabrication rule.
* Production p50/p95/p99 latency — the gate is a pure function measured at
  11.3 µs/call; it is not on a request path, so there is no request
  latency to measure.
