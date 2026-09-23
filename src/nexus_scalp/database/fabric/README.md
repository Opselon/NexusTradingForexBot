# Database Fabric

Provider-agnostic persistence for the Nexus Scalp Engine. Implements the
DB-FABRIC-001 mission: **domain logic must not know which database it is
using.**

This package is the single persistence abstraction for the application.
SQLite and PostgreSQL are first-class backends; reads and writes are
architecturally separated; provider switching is a configuration operation.

See `docs/decisions/ADR-DB-FABRIC-001.md` for the rationale and
`docs/architecture/DB-FABRIC-001-phase0-inventory.md` for the evidence base.

## Structure

```
database/fabric/
├── __init__.py          DatabaseFabric   — the one facade (read + write + health)
├── consistency.py       ConsistencyClass — STRONG / EVENTUAL routing intent
├── routing.py           ConsistencyRouter — decides which plane a read lands on
├── config.py            FabricConfig     — the canonical configuration object
├── health_states.py     DatabaseHealthState — READY/DEGRADED/POOL_EXHAUSTED/…
├── metrics.py           FabricMetrics    — p50/p95/p99, queue depth, counters
├── sqlite_planes.py     SQLiteReadPlane / SQLiteWritePlane
└── pg_planes.py         PgReadPlane / PgWritePlane / PgPool
```

## Usage

```python
from nexus_scalp.database.fabric import DatabaseFabric
from nexus_scalp.database.fabric.consistency import ConsistencyClass
from nexus_scalp.database.fabric.config import FabricConfig
from nexus_scalp.database.provider import DatabaseProvider

fabric = DatabaseFabric.for_domain("audit", fabric_config=cfg)
fabric.open()
try:
    # READ — read-only by construction
    row = fabric.read.with_method("has_ledger_opened").query_one(
        "SELECT * FROM audit_ledger WHERE ticket = ?",
        (12345,),
        consistency=ConsistencyClass.STRONG,
    )

    # WRITE — bounded unit of work
    with fabric.write as uow:
        uow.execute(
            "INSERT INTO audit_ledger (ticket, symbol, direction) VALUES (?, ?, ?)",
            (12345, "XAUUSD", "BUY"),
            financial=True,
        )
finally:
    fabric.close()  # drains the writer, closes every pool
```

No driver, connection, pool, provider branch, `sqlite3` symbol or `psycopg`
symbol appears in domain code — only the facade.

## Read/write separation

The read plane and write plane are distinct objects with distinct connection
sources:

- **Read plane** opens connections with read-only intent:
  - SQLite: `file:<db>?mode=ro` URI **plus** the kernel-level authorizer
    (`set_authorizer`) — the SQLite C API rejects INSERT/UPDATE/DELETE/DDL.
  - PostgreSQL: `SET default_transaction_read_only = on` on every pooled read
    connection — the server rejects mutations.
- **Write plane** is the only path that may mutate:
  - SQLite: exactly **one** writer connection per domain, fed by a bounded
    queue, drained in bounded transactions (batch ≤ 500). All other writers
    were removed.
  - PostgreSQL: a dedicated write pool.

A mutation attempted through the read plane raises — enforced by the
database engine, not by convention.

## Consistency classes

Every repository read declares `ConsistencyClass.STRONG` or
`.EVENTUAL`:

- **STRONG** — accounting ledger, positions, execution/risk state, governance
  and promotion state, migration state, and any read immediately after a
  write. Always the primary read authority; **never** a replica.
- **EVENTUAL** — analytics, dashboards, research aggregates, reporting. May
  use a read replica **only when `READ_DSN` is configured and the replica is
  healthy**; otherwise the primary read pool.

`classify_read(method_name)` resolves the class from a declared value or the
`STRONG_READ_OPERATIONS` contract table, so the strong surface is auditable
in one place.

## SQLite concurrency model

- WAL journaling on file databases; `synchronous=NORMAL`,
  `temp_store=MEMORY`.
- Unlimited concurrent readers (read-only connections).
- Exactly one writer per domain, owning a single write connection.
- Bounded `busy_timeout` on every connection; bounded transactions.
- Read-only connections never call write-side PRAGMAs — `journal_mode` is
  switched only by the writer. (The read plane sets `busy_timeout` alone.)
- Shared in-memory (`file::memory:?cache=shared`) semantics preserved:
  readers reuse the owner's persistent connection.

## PostgreSQL pooling

`psycopg_pool.ConnectionPool` v3, separate read and write pools:

- configurable min/max size, connect timeout, statement timeout,
  `max_idle`, `max_lifetime`;
- readiness probe (`check` callback) — dead connections are replaced;
- read connections are `default_transaction_read_only=on`;
- `jit=off` for stable p99; `application_name` set for observability;
- optional read-replica pool for `EVENTUAL` reads only;
- graceful `close()`.

The pool exists because a cold `psycopg.connect` costs ~104ms on this host;
checkout from a warm pool is sub-millisecond (measured, not assumed — see the
benchmark suite in PR 6).

## Financial durability

`financial=True` (the default on `UnitOfWork.execute`) engages the
no-silent-drop contract:

1. bounded backpressure (the producer waits for queue capacity);
2. durable overflow file if the queue stays saturated;
3. dead-letter + CRITICAL log + metric counter if even the file fails.

Telemetry writes (`financial=False`) are droppable by design — counted and
logged, never silent.

## Health

`fabric.health` returns a `HealthProbe` carrying a distinct
`DatabaseHealthState` — `READY`, `DEGRADED`, `UNAVAILABLE`,
`MIGRATION_REQUIRED`, `MIGRATING`, `MIGRATION_FAILED`, `SCHEMA_DRIFT`,
`AUTHENTICATION_FAILED`, `POOL_EXHAUSTED`, `READ_ONLY`, `WRITE_BLOCKED`,
`REPLICA_LAGGING`. Nothing collapses to "DB down".

## Metrics

`fabric.metrics.snapshot()` returns per-domain counters and latency
percentiles. **No secrets appear in any metric name or label.**

## Configuration

`FabricConfig` is the canonical object (provider, primary/read DSN, domain
overrides, pool limits, timeouts, migration policy, read consistency, retry
policy, batch size). No subsystem defines a competing schema.

Passwords are never persisted in plaintext:

- the OS-backed `SecureSecretStore` (DPAPI on Windows) is the only secret
  mechanism;
- `FabricConfig.to_persistable()` stores connection metadata + a secret
  **reference**;
- `masked()` / `mask_url_password()` redact in logs, API, UI, diagnostics,
  exceptions and CLI output;
- the fabric never hands a DSN to any log call — not even the masked form.

## Migration status (2026-09-23)

PR 1 (this package) delivers the fabric core. 86 production modules still
import `sqlite3` directly; they are migrated domain by domain by PRs 2–4
under a ratchet guard (`tests/fixtures/fabric_sqlite3_baseline.txt` + the
guards in `tests/unit/test_fabric_guards.py`) that fails CI on any **new**
violation and requires the baseline to only ever shrink.
