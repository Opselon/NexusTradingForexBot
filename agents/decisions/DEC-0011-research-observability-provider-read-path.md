# DEC-0011: research observability provider-portable read path

**Date:** 2026-09-28
**Status:** accepted (pending merge)
**Supersedes:** none (extends PG-READ-PLANE-001 D9 / CHG-0067)

## Context

The live engine persisted `database.provider = postgresql` in the operator
settings store (`%LOCALAPPDATA%\NexusScalpEngine\databases\app_settings.db`,
`source = USER_SETTINGS`). Since PG-READ-PLANE-001 / D9,
`AuditRepository._db_path` holds the **provider DSN**
(`postgresql://localhost:5432/nexusdb`) rather than a filesystem path, so that
consumers which build a `Path` from it (`debug_snapshot`'s schema probe) keep
working under a pooled provider.

`src/nexus_scalp/research/observability.py` had a module-level helper:

```python
def _connect(repo):
    conn = sqlite3.connect(repo._db_path, timeout=5.0)
```

It handed the DSN to `sqlite3.connect()`, which treats the URI as a literal
filename. Reproduced verbatim from the live attribute value:

```
sqlite3.connect('postgresql://localhost:5432/nexusdb', timeout=5.0)
-> sqlite3.OperationalError: unable to open database file
```

That is the exact string in the operator's report:

```
[RESEARCH_OBS] heatmap failed error='unable to open database file'
[RESEARCH_OBS] family analytics failed error='unable to open database file'
```

### Scope of the failure

Every read in `ResearchObservabilityStore` was affected, not just the two
logged aggregates. The methods that called `_connect` **without** an
`_is_sqlite` guard raised the DB-open error and logged it
(`gate_failure_heatmap`, `family_analytics`, `_registry_entry`, `_runs_for`).
The methods that *did* guard returned their empty default silently —
`if not self.audit_repo._is_sqlite: return []` — so gates, events, evidence,
snapshots, worker health and queue state all reported `0` / `[]` / `available:
False` while PostgreSQL held 26,140 gate rows, 82,726 event rows and 18,316
evidence rows. The surface was either erroring or silently empty across the
board.

## Decision

Introduce provider-portable read helpers in `research/observability.py`:

- `_query(repo, sql, args)` — SQLite opens its own connection (via `_connect`,
  now SQLite-only); a pooled provider reads through
  `AuditRepository._provider_read_guard(..., kind="rows")`, the same registered
  fabric READ plane the audit read surface already serves (CHG-0067).
- `_query_one(...)` — single-row form over `_query`.
- `_scalar(...)` — single-value form (`kind="scalar"`).

All reads in the store were converted to these helpers, and the
`if not _is_sqlite: return <empty>` early returns were removed from read
methods so a pooled provider is served by the plane instead of being gated out.

`_connect` now **raises `RuntimeError`** when reached under a non-SQLite
provider. This is deliberate: a caller that reaches it has bypassed the
portable path, and the previous behaviour turned that into an
`unable to open database file` error whose cause was invisible.

`_archive_available(conn)` became `_archive_available()`: SQLite introspects
`sqlite_master`, a pooled provider introspects `information_schema` with
`current_schema()`.

## Consequences

- The research observability surface now reads the store the engine writes,
  on both providers. Under PostgreSQL it is no longer fail-silent.
- A failed route still degrades observably — the guard counts and warns; it
  never raises into the caller (mission s91).
- SQLite semantics are byte-for-byte unchanged: `_query`/`_scalar` take the
  same SQL and return the same row shape (`dict`), and `_connect` is the same
  SQLite connection the store used before.
- The class of bug is pinned statically by
  `tests/unit/test_research_observability_provider_portability.py`: no
  function outside the SQLite helpers may contain a `sqlite3.connect`.

## Alternatives considered

- **Special-case `postgresql://` in `_connect`.** Rejected: a scheme test is
  the wrong predicate (D9's contract is "any non-SQLite provider"), and it
  would have kept the silent-empty behaviour of the gated methods.
- **Expose `research_read_plane()` on `AuditRepository`** (as the concurrent
  foreign WIP in `nse-review-main` does). Rejected for this change: it does
  not exist on the committed `review/main` HEAD, and `_provider_read_guard`
  already provides the routed + observable-degradation contract.
