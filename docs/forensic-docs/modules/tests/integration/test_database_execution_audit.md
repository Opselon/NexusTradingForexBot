# tests/integration/test_database_execution_audit.py

- GUARDS: Database execution-audit pipeline — that every trade lifecycle step (open → fill → modify → close) lands as first-class audit rows with correlation and outcome linkage.
- KEY ASSERTIONS:
  - `TestWorkerWithEngine.test_database_execution_audit_pipeline`: the full order lifecycle writes audit evidence; broker events reach the ledger; audit rows are queryable and consistent (5 asserts).
- PITFALLS IT ENCODES: worker-flush discipline (audit_repo._queue.join() before asserting on writes).
- NOTES: Smallest integration file; wraps the same fake-engine/sqlite harness as test_accounting_api. Regression net for the execute→audit path.
