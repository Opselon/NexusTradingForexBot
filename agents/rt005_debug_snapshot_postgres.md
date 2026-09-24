# RT-005 — debug_snapshot audit probe on PostgreSQL

**Lane:** `agent/nse-always-on-runtime-fix`
**Status:** FIXED and MERGED — PR #447, squash `70f24157` on `origin/main`
**Verified:** exception reproduced directly; guard routes correctly; SQLite unchanged
**Severity:** MEDIUM — every snapshot logged a spurious warning and lost the audit schema version

## SYMPTOM

```
debug_snapshot db schema probe error error="WindowsPath('.') has an empty name"
```

## ROOT CAUSE

The probe resolved `Path(getattr(engine.audit, "_db_path", "") or "")`. A
PostgreSQL-configured `AuditRepository` has `_db_path == ""` (only SQLite
populates it), and `Path("")` is `WindowsPath('.')`. Feeding that to the
SQLite-only `DatabaseMigrationEngine` raised on `with_suffix()`.

Same class as RT-004: a provider that is not file-based read through a
filesystem-only API.

## FIX

The probe reports an explicit provider mismatch
(`POSTGRES_DOMAIN_NOT_FILE_BASED`, health `READY`) instead of crashing, so
the snapshot stays useful and the warning stream stays clean. The SQLite
path is unchanged.

## VERIFICATION

- The exception is reproduced directly: `Path('')` passed to
  `DatabaseMigrationEngine` raises `ValueError: empty name`.
- The guard routes a URL-only repo to the provider branch.
- The SQLite path still resolves to a filesystem path.
- 3 regression tests pass.

## NOTE

The 6 pre-existing `TestDebugApi` failures in `test_debug_snapshot_phase20.py`
are **unrelated**: they fail identically on pristine `origin/main`
(`nse-baseline` worktree, commit `1b56bc5f`). Filed here rather than silently
absorbed — they predate this lane's work.
