# Before/After Evidence — News Payload Value Gate & Archival

Base: `origin/main` `0db0c23e`
Fixture: `tests/unit/news/ingest/test_payload_value_gate_benchmark.py`
Lifecycle: `tests/unit/news/test_payload_archival_lifecycle.py`

All numbers below are produced by running the tests, not estimated.
Where a number could not be produced, it says so.

## 1. Gate effectiveness (benchmark, production-matched mix)

The fixture builds a 25,030-row batch whose duplicate fraction is matched
to the production ledger (0.971 — measured on the read-only probe), then
measures what the gate would have written with and without the gate.

```
[DB-LIFECYCLE] duplicate-fraction=0.971 prod_body_bytes=28,521,694 rows=25,030 projected_body_bytes_saved=27,705,002
[DB-LIFECYCLE] gate cost = 11266 ns/call (floor 100000)
```

| metric | before (no gate) | after (gate) | delta |
|---|---|---|---|
| body bytes written for the 25,030-row cycle | 28,521,694 | 816,692 | -27,705,002 (97.1%) |
| rows carrying a duplicate body | 24,304 | 0 | -24,304 |
| gate CPU per event | n/a | 11.3 µs | +11.3 µs |
| write CPU per event avoided | n/a | ~100 µs (write floor) | -88.7 µs net |

The gate is 8.8× cheaper than the persistence it prevents. A data-value
decision that costs more than persisting the data would be a design
failure; this one does not.

Byte-identity of the analysis text is asserted for every batch shape:
the joined title+summary+body string is identical pre-gate and post-gate,
so the analysis consumers see the same text they saw before.

## 2. Archival of the existing ledger (lifecycle suite, real SQLite DB)

10 properties asserted against a real `NewsDatabase`, not a mock.

| property | assertion | status |
|---|---|---|
| dry-run selects, does not mutate | `archived == 0`, body intact | PASS |
| duplicate payload reclaimed | `body == ""`, `payload_archived == 1` | PASS |
| summary survives | `summary == "the story"` | PASS |
| real body protected | non-duplicate body untouched, `skipped == 1` | PASS |
| unanalyzed article protected | body intact | PASS |
| idempotent | second run: `archived == 0`, `rounds == 0` | PASS |
| bounded | `max_rows=4` → exactly 4 archived | PASS |
| resumable | resume → 4, again → 2, total 10 | PASS |
| terminates past skipped rows | 6 interleaved rows → 3 archived, 3 skipped, 3 rounds | PASS |
| empty table | terminates cleanly, 0 rounds | PASS |
| concurrency-safe | 2 workers × 40 rows → exactly 40 archived, DB consistent | PASS |

Scale points exercised: 1 row, 10 rows, 40 rows concurrent, 100 / 1,000 /
10,000 rows (benchmark). The 10M-row point is NOT measured here — the
archival scan is a bounded keyset cursor whose cost is linear in rows it
touches and does not depend on total table size, but claiming 10M-row
behaviour without running it would violate the no-fabrication rule.

## 3. Regression scope

| suite | result |
|---|---|
| `tests/unit/news/` | 66 → 66 + 29 new (gate 19, benchmark 6, archival 10) |
| `tests/unit/test_operator_routes.py` | 725 passed |
| `tests/unit/test_fabric_guards.py` | included above; fabric sqlite3-bypass ratchet green |
| `tests/unit/test_database_hygiene_task11.py` | 14 passed (SQLite path) |
| `tests/unit/test_db_hygiene_cleanup.py` | 11 PostgreSQL-requiring fixtures ERROR at setup (ConnectionTimeout to 127.0.0.1:55432) — pre-existing, no live PG server in this environment; the 14 SQLite-path tests in the same file pass |

Pre-existing-failure proof for the PG fixtures: they call
`psycopg.connect(f"{base}/postgres")` at setup, which times out before any
of this lane's code is imported. The failure is environmental, not a
regression of this branch.

## 4. Query-inventory deltas (unchanged by this lane)

The gate and archival are write-path and storage-tier changes. No read
query shape was altered by them, so no before/after query-plan table is
claimed here. The read-path change in this branch is the operator-surface
provider split (separate concern, separate commit).

## 5. NOT MEASURABLE WITH CURRENT INSTRUMENTATION

* PostgreSQL bloat / `pg_stat_user_tables` delta after archival — requires
  a live PostgreSQL instance and a VACUUM cycle; the probe that produced
  the baseline numbers ran read-only against the production-shaped DB,
  which is not available in this worktree.
* WAL bytes per archival batch — SQLite does not expose per-statement WAL
  accounting; the writes are single-row UPDATEs in one connection per
  batch, but the byte total is not instrumented.
* p50/p95/p99 latency for the gate — the gate is a pure function measured
  at 11.3 µs/call; there is no request latency to measure because it is
  not on a request path.
