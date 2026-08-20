# tests/unit/test_accounting_core.py

- GUARDS: Phase 08 Unified Accounting & Performance Intelligence — canonical accounting core end-to-end against a real SQLite audit database (snapshots, periods, drawdown, closure classification, trade normalization, attribution, forensics, worker).
- KEY ASSERTIONS:
  - `TestPeriodBounds`: UTC-midnight half-open day/week/month/year bounds, naive datetimes assumed UTC, recent periods ordered.
  - `TestSnapshots`: recording, duplicate throttling, balance-change writes; `TestPeriodAggregation`: daily/weekly/monthly/yearly, empty has_data False, midnight belongs to new period.
  - `TestClosureClassification`: TP/SL/breakeven/trailing/manual/partial/emergency closures — breakeven SL is NOT a win; net loss after costs still a loss.
  - `TestTradeNormalization`: net = gross − costs; persisted net trusted; R-multiple reconstruction; open positions excluded.
  - `TestWorker`: start/stop, cycle refreshes cache, throttled, failure isolated, restart resumes, never duplicates records (178 asserts total).
- PITFALLS IT ENCODES: audit writes are queued — flush via `audit_repo._queue.join()` before querying; do NOT call close() mid-test (nulls the queue, BUG-058); worker restart must resume cleanly without duplicate rows.
- NOTES: The largest accounting unit suite (1519 lines); `_FakeAccount`/`_FakePosition`/`_FakeAdapter` harness; covers 14 test classes.
