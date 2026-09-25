# tests/unit/test_database_migrations_phase18.py

- GUARDS: Database Migration System — TASK-10 regression suite (TEST-DBM-01..40): fresh DB reaches current schema; legacy baseline detected; old schema upgrades automatically; history + checksums; failure/rollback; locking.
- KEY ASSERTIONS:
  - `TestUpgrade`: legacy auto-upgrade + chained migrations; `TestIdempotency`: re-running migrates nothing twice; `TestHistoryAndChecksum`: history rows + checksums, modified historical migration DETECTED; `TestFailureAndRollback`: failed transactional migration rolls back, non-transactional has recovery; `TestLock`: concurrent runner prevented (31 asserts).
- PITFALLS IT ENCODES: migration integrity = idempotency + checksums + rollback + lock; a tampered historical migration must fail loudly, never silently re-run.
- NOTES: 9 classes, 421 lines; pairs with hygiene suite.
