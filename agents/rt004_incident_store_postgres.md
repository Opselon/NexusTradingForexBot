# RT-004 — incident worker never starts on PostgreSQL

**Lane:** `agent/nse-always-on-runtime-fix`
**Status:** FIXED and MERGED — PR #446, squash `df78ab27` on `origin/main`
**Verified:** construction + `list_incidents` + `count()` against the live domain
**Severity:** HIGH — production incidents were not recorded at all

## SYMPTOM

```
[INCIDENT_WORKER] event=START_FAILED (isolated)
error='IncidentStore requires db_path or audit_repo'
```

## ROOT CAUSE

`live_engine._ensure_incident_worker` reads
`getattr(self.audit, "_db_path", "")`. A PostgreSQL-configured
`AuditRepository` sets `_db_path = ""` (only SQLite populates it), so
`IncidentStore.__init__` raised and the incident worker never started.

The write path already worked — it queues through the repo's `_queue` — so
the defect was purely at **construction**. The worker was unreachable, not
broken at runtime.

This is the same class as RT-005: a provider that is not file-based read
through a filesystem-only API.

## FIX

`IncidentStore` accepts a repo carrying a `_db_url` instead of a `_db_path`.
A `sqlite:///` url still collapses to a filesystem path, so the SQLite
behaviour is unchanged. Reads are provider-aware through `_connect` (a small
shim translating the placeholder style, since the read queries were authored
in SQLite dialect and are otherwise portable). `COUNT(*)` aggregates now use
an explicit alias because a dict row is not indexable by position.

## VERIFICATION

- 108 existing incident tests (SQLite) pass — no regression.
- Construction against a PG-configured repo passes (raised before).
- `list_incidents` and `count()` run against the live PostgreSQL domain.
- The empty-store guard still raises — the fix weakens nothing.
