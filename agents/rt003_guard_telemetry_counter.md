# RT-003 — guard-telemetry counter upsert ambiguous on PostgreSQL

**Lane:** `agent/nse-always-on-runtime-fix`
**Status:** FIXED and MERGED — PR #444, squash `214f02de` on `origin/main`
**Verified:** post-merge against the live PostgreSQL domain (rolled back)
**Severity:** HIGH — silent loss of all guard/rejection counters

## SYMPTOM

The runtime log lists `ambiguous count` for `audit_guard_telemetry`. Every
increment of the guard/rejection counter was failing. This is the telemetry
that records *why* the engine refused to trade (BUG-054) — so the engine was
losing its own rejection reasons silently.

## ROOT CAUSE (reproduced against the real domain)

```sql
INSERT INTO audit_guard_telemetry (window_start, symbol, reason_code, count)
VALUES (?, ?, ?, 1)
ON CONFLICT(window_start, symbol, reason_code)
DO UPDATE SET count = count + 1
```

PostgreSQL resolves the bare `count` in the `DO UPDATE` arm against **both**
the target row and `excluded.count` and rejects it:

```
psycopg.AmbiguousColumn: column reference "count" is ambiguous
```

SQLite accepts the bare form. The statement was correct on the development
dialect and broken on the deployed provider — the exact class of defect
RTF-001/CHG-0067 was about, in a write path instead of a schema path.

## THE FIRST FIX WAS WRONG, AND A TEST CAUGHT IT

`excluded.count + 1` fixes PostgreSQL but is **not portable**: on SQLite it
yields `2` for three events instead of `3`, because SQLite's UPSERT here does
not populate `excluded` the way PostgreSQL does. This was caught by the
existing BUG-054 SQLite test, not by reasoning. Both candidate forms were
then verified empirically on both dialects before shipping anything:

| Form | SQLite (3 events) | PostgreSQL (3 events) |
|---|---|---|
| `count = count + 1` | 3 ✓ | AmbiguousColumn ✗ |
| `count = excluded.count + 1` | 2 ✗ | 3 ✓ |
| `count = t.count + 1` (aliased target) | 3 ✓ | 3 ✓ |

## THE FIX

Alias the target table and qualify the reference:

```sql
INSERT INTO audit_guard_telemetry AS t (window_start, symbol, reason_code, count)
VALUES (?, ?, ?, 1)
ON CONFLICT(window_start, symbol, reason_code)
DO UPDATE SET count = t.count + 1
```

One line changed in production code.

## VERIFICATION

- `test_audit_db_growth_bug054.py` (BUG-054, SQLite): passes — confirms the
  SQLite increment still works.
- `test_rt003_guard_telemetry_counter.py` (real PostgreSQL, rolled back):
  three events → `count = 3`.
- Post-merge probe of the **actual statement string on origin/main** against
  the live `nexusdb`: `count = 3`. The incident is closed.
- Static sweep of all 24 `DO UPDATE` statements repo-wide: 0 unqualified
  RHS references remain. The class is clear, not just the instance.

## LESSON

`excluded` is not portable between SQLite and PostgreSQL. When a dialect fix
is needed, verify the candidate on **both** dialects empirically — the
portable-looking fix regressed the other dialect by the same amount it
fixed the first.
