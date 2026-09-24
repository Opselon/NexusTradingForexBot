# RTF-001 — PostgreSQL schema drift: `applied=37 errors=0` while columns are missing

**Lane:** `agent/nse-always-on-runtime-fix`
**Status:** RESOLVED UPSTREAM (by CHG-0067, PR #431) — VERIFIED_RUNTIME_LOCAL by this lane
**First seen:** 2026-09-24 05:45:47 (+03:30), live runtime log
**Severity:** CRITICAL — audit/financial record loss

## SYMPTOM

Startup reports `[DB-MIGRATE] schema applied=37 errors=0` while every runtime
INSERT fails:

```
column "signal_dedup_key" of relation "audit_signals" does not exist
column "account_source" of relation "audit_account_snapshots" does not exist
```

## ROOT CAUSE (verified on the real database)

The PostgreSQL schema is Python-generated and the pre-CHG-0067 migration only
applied what its **source scan** could see: triple-quoted `CREATE` literals in
`AuditRepository`. That scan captured 35 tables and 439 of the 463 required
columns. The other 24 columns are added at runtime by `ALTER TABLE ADD COLUMN`
calls inside `_add_column_if_missing` / `_create_sqlite_tables` — invisible to
a source scan. So the migration *legitimately* reported success while runtime
DDL differed from the code's own contract.

**Proven on the real database:** live `nexusdb` has all 35 tables but is
missing exactly the 24 columns, including both named in the runtime log.

## RESOLUTION (upstream, CHG-0067 — PR #431)

`sqlite_ddl_statements()` is no longer a source scan. It is now a **full
bootstrap replay** (`migration/schema_snapshot.py`): a disposable in-memory
SQLite connection records every DDL statement the real bootstrap executes via
a trace callback, then the ordered migration registry and engine meta tables
are applied on top. The replay captures what a fresh SQLite database would
actually hold — including the runtime `ALTER`s.

## VERIFICATION BY THIS LANE (real PostgreSQL, not logs)

1. **The gap is fully covered.** The live `nexusdb` is missing 24 required
   columns. The replay emits 27 `ALTER TABLE ADD COLUMN` statements covering
   **all 24** — `not_covered == []`. Both columns named in the runtime log are
   in that set.
2. **The dedup index exists.** The replay emits
   `CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_signals_dedup
   ON audit_signals(signal_dedup_key)`, so `ON CONFLICT(signal_dedup_key) DO
   NOTHING` is valid on PostgreSQL.
3. **Dependency ordering holds.** Every `CREATE [UNIQUE] INDEX` follows the
   introduction of every column it references (0 violations). SQLite tolerates
   an index on a not-yet-existing column; PostgreSQL does not, so this is the
   invariant that makes a *fresh* PostgreSQL database provisionable. The dedup
   index is emitted at position 76, its column's ALTER at 75.
4. **A fresh PostgreSQL domain provisions completely.** The real-DB test
   creates a throwaway `nse_rtf001_test` database, applies the translated
   replay, and asserts every required column lands and the dedup index is
   present — 2 passed.

## WHY THIS LANE DID NOT RE-AUTHOR THE FIX

This lane first wrote its own additive heal (`additive_columns_statements()`
diffing `APP_REQUIRED_COLUMNS` against the scan, dependency-ordered
`migrate_domain`). After rebasing onto `origin/main` it became clear CHG-0067
had already shipped a strictly better version of the same fix, so the local
rework was reverted and the lane's contribution narrowed to verification plus
RTF-002. The merge keeps origin/main's files verbatim; the only production
change on this lane is `audit_repository.py` (RTF-002).

## WHAT REMAINS

- The live `nexusdb` is still missing the 24 columns until the engine next
  boots against the merged code and the replay provisions them. The fix is
  present upstream; the database simply has not yet been re-provisioned by it.
- `audit_guard_telemetry` still fails with `column reference "count" is
  ambiguous` (RTF-003). It is masked by the `signal_dedup_key` failure today;
  once RTF-001's columns land on the live database it becomes the next failing
  write.
