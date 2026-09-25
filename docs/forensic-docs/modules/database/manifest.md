# src/nexus_scalp/database/manifest.py

- **PURPOSE:** Schema manifests — machine-readable expected schema
  contracts per domain (TASK-10 §13): AUDIT / NEWS / CANDLE / SETTINGS
  etc. Each manifest declares the expected tables/columns so migrations,
  hygiene, and schema-audit tooling can verify an on-disk DB against the
  contract.
- **ARCHITECTURE LAYER:** Database (contract registry).
- **RESPONSIBILITY:** (a) define expected schema per domain;
  (b) verification helpers (does this DB match the manifest?);
  (c) drift detection (missing tables/columns) consumed by hygiene and
  migrations.
- **DEPENDENCIES:** sqlite introspection, pydantic/dataclass contracts.
- **CONNECTS TO:** database/engine (open), migration framework (target
  schema), hygiene consistency checks, tests
  (test_database_migrations_phase18, test_database_hygiene_task11).
- **KEY CONCEPTS:** the manifest is the TARGET; migrations move the DB
  toward it; a manifest mismatch is REPORTED (hygiene never auto-fixes
  schema — destructive ops go through migrations, TASK-10 rule).
- **EDGE CASES & PITFALLS:** lazy-schema shadow tables legitimately absent
  (no run attached) must not flag as drift — manifests carry a
  "lazy/optional" marker for such tables.