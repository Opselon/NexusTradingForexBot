# tests/unit/test_database_hygiene_task11.py

- GUARDS: TASK-11 Database Hygiene Worker — regression guards (TEST-HYG-01..36): non-destructive safety contract — dry-run/audit-only makes ZERO mutations; exact duplicates detected via canonical identities; split fills NEVER treated as duplicates.
- KEY ASSERTIONS:
  - dry-run produces no writes; duplicate detection via canonical identity keys; split-fill rows preserved; retention bounded; hygiene worker failure isolated (40 asserts).
- PITFALLS IT ENCODES: hygiene is audit-first — any destructive path must be opt-in and tested as such; over-aggressive dedup would destroy real split fills.
- NOTES: 36 mapped requirements; pairs with test_database_migrations_phase18.py.
