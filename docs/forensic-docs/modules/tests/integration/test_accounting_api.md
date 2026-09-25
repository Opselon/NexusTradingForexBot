# tests/integration/test_accounting_api.py

- GUARDS: Phase 08 Unified Accounting API — end-to-end verification that canonical AccountingCore, the background accounting worker, and the LiveEngine wiring serve one consistent performance truth over real SQLite audit tables.
- KEY ASSERTIONS:
  - `TestAccountingApi`: /api/account/performance, equity-curve, drawdown, strategy-contribution endpoints return the same numbers held in the temp DB (no synthetic values); period bounds UTC half-open; worker status format.
  - `TestWorkerWithEngine`: worker starts/stops with LiveEngine, refreshes cache, throttling respected, failures isolated, restart resumes, never duplicates records (81 asserts).
- PITFALLS IT ENCODES: audit-repo writes are queued to a background worker — tests must flush via `audit_repo._queue.join()` before querying; do NOT call close() mid-test (nulls the queue, BUG-058).
- NOTES: Uses temp SQLite + `_FakeAccount`/`_FakePosition`/`_FakeAdapter`; closes over all accounting invariants (net=gross-costs, one drawdown method, master rows rebuildable).
