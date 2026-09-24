# ADR: Database Fabric — Provider-Agnostic Persistence for NSE

- **Status**: PROPOSED (implementation phase, PR plan active)
- **Date**: 2026-09-23
- **Branch**: `agent/feature/DB-FABRIC-001` (worktree `nse_db_fabric`)
- **Supersedes**: none (formalizes and completes the 2026-08-20 "DATABASE
  PORTABILITY" mission)
- **Evidence base**: `docs/architecture/DB-FABRIC-001-phase0-inventory.md`
  (full-repo scan: 10,148 hits / 897 files; live read-only schema probe of the
  production databases)

---

## 1. Context and problem statement

NSE persists **16 logical domains** across **6 active SQLite databases**
(audit.db 425MB/70 tables; news.db 241MB/22 tables; strategies.db 30MB;
candle_intel.db 11MB; models.db; AppData app_settings.db). The repository
already has a partial portability layer (`database/` package: config, provider,
drivers, migration engine, manifests, CLI, web console) — but repo-wide
evidence shows it is not complete:

- **59 production consumers import `sqlite3` directly** and own their own DDL
  and connection handling (competing schema authorities).
- **155 raw `sqlite3.connect` sites in `src/`**.
- **The write plane does not exist on PostgreSQL.** `AuditRepository` is gated
  by `_is_sqlite` at 20+ sites: under PG the background worker no-ops and every
  write method early-returns. The abstraction is present; the PG write path is
  empty.
- **No read/write separation.** `DatabaseDriver` methods are single-plane;
  `query_readonly` exists but is opt-in and unrouted. Nothing prevents a
  dashboard GET from opening a write-capable connection.
- **No PostgreSQL pooling** — `PostgreSQLDriver.connect()` opens one connection
  per call.
- `DatabaseDomain` knows 3 domains; reality is 16. `models.db` and the AppData
  settings DB are not provider-switchable at all.

The requirement: **DOMAIN LOGIC MUST NOT KNOW WHICH DATABASE IT IS USING.**
SQLite and PostgreSQL both first-class. Reads and writes architecturally
separated. Zero-config SQLite retained. User switches provider by connection
string alone.

---

## 2. Decision — the Database Fabric

A **ports-and-fabric** layer inserted between the domain repositories and the
provider drivers. Domain code talks to typed repository interfaces; the fabric
owns routing, pools, transactions, consistency, and provider differences.

```
 APPLICATION / DOMAIN
   ┌────────────────────────────────────────────────────────────────┐
   │  TradeLedgerRepository  NewsRepository  CandleRepository       │
   │  ModelRepository  SettingsRepository  GovernanceRepository ... │   ← typed semantic methods
   └───────────────────────────┬────────────────────────────────────┘
                             │  ReadGateway / UnitOfWork  (ports)
   ┌───────────────────────────▼────────────────────────────────────┐
   │                     DATABASE FABRIC                             │
   │  ┌──────────────────────┐    ┌───────────────────────────────┐  │
   │  │   READ PLANE         │    │   WRITE PLANE                 │  │
   │  │  · read pool         │    │  · write pool (PG)            │  │
   │  │  · STRONG routing    │    │  · bounded writer queue (SQL) │  │
   │  │  · EVENTUAL routing  │    │  · batching / backpressure    │  │
   │  │  · query timeout     │    │  · idempotency keys           │  │
   │  │  · no DDL/DML        │    │  · dead-letter + overflow     │  │
   │  └──────────┬───────────┘    └──────────────┬────────────────┘  │
   │             └───────────────┬───────────────┘                   │
   │                     ConsistencyRouter                          │
   │                     Metrics / Health                           │
   └─────────────────────────────┬──────────────────────────────────┘
                          ProviderDriver
                    ┌─────────────┴──────────────┐
                 SQLiteDriver            PostgreSQLDriver
              (WAL, 1 writer,           (psycopg_pool, read pool,
               N read conns)             write pool, optional replica)
```

**Provider selection happens only at the configuration/bootstrap boundary.**
Domain code contains no `if sqlite:` / `if postgres:` / `PRAGMA:` /
`sqlite3.Connection` / `psycopg.Connection`.

---

## 3. Read/write separation — exact mechanism

Every repository method declares a **ConsistencyClass**:

| Class | Examples | Routing |
|---|---|---|
| `STRONG` | accounting ledger, positions, execution state, risk state, governance/promotion state, migration state, any read immediately after a write | **primary read pool** — never a replica; same authority as the write plane |
| `EVENTUAL` | analytics, dashboards, research aggregates, reporting, telemetry | read pool; replica **only when a `READ_DSN` is configured** |

Enforcement is mechanical, not convention:

- **Read plane**: connections opened with **read-only intent**.
  - SQLite: `file:<db>?mode=ro` URI **plus** the existing C-level
    `set_authorizer` callback (already implemented in `SQLiteDriver`) — the
    kernel rejects INSERT/UPDATE/DELETE/DDL, not just our convention.
  - PostgreSQL: `SET default_transaction_read_only = on` + `SET
    statement_timeout` on every pooled read connection; the pool never lends a
    read connection for a write statement.
- **Write plane**: explicit `UnitOfWork` / `driver.transaction()` scope. Writes
  outside a write-scoped context raise `WriteIntentRequiredError`.
- **Write-through-read ban**: a regression test asserts no write statement is
  issued on a read-plane connection (both providers) — Phase 50 guard #4/#5.

The read plane is a **first-class pool**, not a flag on a query.

---

## 4. Provider model

### SQLite — deliberate concurrency model

- WAL on file DBs (already); `synchronous=NORMAL`, `temp_store=MEMORY`.
- **N read connections** (read-only URI, one per reader thread; SQLite permits
  unlimited concurrent readers under WAL).
- **Exactly one writer per database/domain**, owning a single write
  connection. All domain writes funnel through the writer queue → the writer
  connection — no second write handle, no lock thrash.
- `busy_timeout` bounded; deterministic transaction scope; no long writer
  transactions (batch ≤ 500 rows per commit, matching the proven audit
  writer).
- Checkpoint policy: passive WAL checkpoints on the writer's idle pass;
  WAL size + lock-wait metrics surfaced.
- Shared in-memory (`file::memory:?cache=shared`) semantics preserved — the
  read plane reuses the owner's persistent connection for shared-cache DBs.

Rationale: SQLite's process-wide write lock means the only scalable local
shape is many readers + one disciplined writer. This is exactly the shape the
audit writer already proved at production scale (425MB, 82k-row research
tables, batched `executemany`).

### PostgreSQL — pooled, production-grade

- `psycopg_pool.ConnectionPool` (v3), separate **read pool** and **write pool**.
- Configurable min/max size, `connection_timeout`, `statement_timeout`,
  `idle_timeout`; health checks; pool-exhaustion metrics; graceful `close()`.
- Every connection receives session configuration (`default_transaction_read_only`
  on read connections, `jit=off` for stable p99, `application_name` for
  observability).
- Optional **read replica**: `READ_DSN` config; `ConsistencyRouter` sends only
  `EVENTUAL` reads there. No `READ_DSN` ⇒ reads use the primary read pool.
  Replica lag surfaced as a health metric when configured.

**No ORM** (Phase 3): typed repository interfaces + explicit SQL + native
placeholders + `executemany` batching. Evidence: the hot path is a batched
async queue today and must stay that way; an ORM adds indirection with no
measured benefit for a 70-table monolith whose queries are already written.

---

## 5. Topology decision — one database per domain vs one database with schemas

**Decision: one PostgreSQL database per logical domain, with a shared
connection profile** — i.e. `nse_audit`, `nse_news`, `nse_strategies`,
`nse_candle`, `nse_models`, `nse_settings` (Phase 19).

Rationale (measured/technical, not aesthetic):

| Factor | One-db-per-domain (chosen) | Single db, multiple schemas |
|---|---|---|
| Pool efficiency | 1 pool per domain; no cross-domain contention on the pool's connection cap | one shared pool; a chatty domain (audit) can exhaust the pool for settings reads |
| Isolation | physical: a news VACUUM/backup/restore never touches audit truth | logical only; one bad migration can span schemas |
| Backup/restore | per-domain RPO/RTO; audit evidence can be snapshotted without quiescing news | whole-db only (or complex schema-level tooling) |
| Migration | per-domain migration gate + version (already the NSE model) | cross-schema coordination |
| Operational complexity | 6 small DBs to name; one connection profile keeps it 1 DSN in practice | 1 DB, but schema-level permissions are fiddlier |
| Future scaling | shard/split a domain to its own server with zero rewrite | must split later anyway |

The user provides **one primary connection string**; the fabric derives
per-domain database names from it (convention: `<db_prefix>_audit`,
`_news`, ...) unless a **domain override** is set. Advanced deployments may
supply per-domain DSNs.

---

## 6. Hot-path contract (Phase 8)

The tick path must not synchronously wait on DB I/O. `AuditRepository` is
already fully async (queue + single background writer). The fabric **keeps**
that shape for both providers:

- `ZERO_DB_WAIT` — in-memory snapshots / caches (reference data, settings,
  model metadata) loaded at boot or on invalidation.
- `ASYNC_WRITE` — audit/experience telemetry: enqueue, never wait.
- `CACHED_READ` — immutable reference data.
- `BOUNDED_READ` — STRONG reads with a `statement_timeout`.
- `MANDATORY_STRONG_READ` — e.g. `has_ledger_opened(ticket)` before a duplicate
  open: must read the primary read pool with a bounded timeout.

Latency budgets are **not invented here**. PR 6 establishes the benchmark
suite (Phase 29/48) and records p50/p95/p99/max + environment + dataset size +
concurrency + provider + cache state. Budgets are then set from measured
baselines, and any operation exceeding its budget is surfaced by the metrics
layer.

---

## 7. Financial integrity (Phase 24)

`audit_ledger`, executions, broker deals/orders/trades, account snapshots,
PnL, commission/swap, MAE/MFE, position lifecycle, experience outcomes are
HIGH RISK. Contract:

- **No silent drops.** The existing three-tier defense (bounded backpressure →
  durable overflow file → dead-letter store + CRITICAL log + counters) is
  **kept verbatim** and **extended to PostgreSQL** (today it is
  SQLite-only — that is defect G3).
- **At-least-once + deduplication**, never claimed exactly-once without proof.
  Idempotency keys (`idempotency_key` on experiences/outcomes, `ticket` on
  ledger, `_signal_dedup_key`) are the dedup boundary; a write is retried until
  ack'd by the writer, and replay is idempotent on the key.
- **Read-after-write for STRONG pairs** is `flush()` + primary read pool — the
  BUG-140 pattern (pre-trade experience queued → post-trade outcome reads it).
- **No NUMERIC migration of money columns without proof.** Financial fields are
  `REAL` (IEEE-754 double) in the current schema and the application's truth is
  double semantics. PostgreSQL keeps `DOUBLE PRECISION` — **not** NUMERIC —
  for these columns so bit-for-bit value equality holds; migration validation
  proves equality within 0 ULP for exact values and a documented epsilon for
  computed ones. A change to NUMERIC would be a separate, deliberately-scoped
  decision with reconciliation evidence.
- **Sequence preservation**: monotonic `id` / rowid order is preserved by
  BIGSERIAL on PG and rowid on SQLite; migration verifies identity ranges.

---

## 8. Migration model (Phase 15/16)

Upgrade the existing migrator into a **resumable, verifiable, copy-only**
data-plane migration. States: DISCOVERING → PRECHECK_FAILED / READY → COPYING →
VALIDATING → RECONCILING → READY_FOR_CUTOVER → CUTOVER_COMPLETE | FAILED |
ROLLBACK_AVAILABLE | BLOCKED.

- **Source is never modified, never dropped.** SQLite source remains the
  recovery point until an operator explicitly retires it (Phase 40: no
  big-bang removal).
- Deterministic chunked copying with **per-batch transaction boundaries** and
  **checkpoint persistence** (a `migration_checkpoints` table on the
  destination) ⇒ idempotent re-run resumes at the last committed chunk.
- Per-table row counts + checksums; financial aggregate verification
  (sum(pnl), sum(volume), count by ticket); identity/sequence reconciliation;
  critical-row spot checks; final reconciliation report.
- **Operator approval before cutover** — the CLI/UI shows source/target, table
  inventory, row counts, estimated size, schema diff, warnings, precheck and
  dry-run results, then requires explicit confirmation (Phase 38).
- Cutover is **transactional at the configuration level**: VALIDATE DSN → TEST
  CONNECTION → CHECK SCHEMA → MIGRATE IF REQUIRED → VALIDATE DATA → WRITE
  PROVIDER CONFIG → HEALTH CHECK → VERIFY APPLICATION → MARK ACTIVE. Any stage
  fails ⇒ previous provider stays active, no half-switched state (Phase 39).
- **Cutover modes**: A offline bulk / B quiesced (implemented first) / C
  dual-write + capture-and-drain / D shadow PG verification (designed, not
  enabled by default). Dual-write is not enabled blindly: it requires
  idempotency, ordering, divergence detection, reconciliation and rollback —
  all specified in Phase 16 and gated behind explicit operator opt-in.

---

## 9. Configuration & secrets (Phase 17/35/36)

One canonical `DatabaseFabricConfig` (provider, primary DSN, read DSN, domain
overrides, pool min/max, connect/command/transaction timeouts, migration
policy, read consistency, replica usage, health-check interval, retry policy,
batch size). No subsystem may define a competing DB config schema.

- User configures via **connection string** (`postgresql://user@host:5432/db`,
  keyword form `host=... port=... dbname=... user=...`, or SQLAlchemy-style
  `postgresql://`). No code changes, no import changes.
- **Passwords never persisted in plaintext.** The existing OS-backed
  `SecureSecretStore` (DPAPI) is the only secret mechanism — no second store.
  Persisted config separates non-secret connection metadata from a secret
  reference; `mask_url_password` redacts in logs, API, UI, diagnostics,
  exceptions, reports, CLI output. Secrets are never metrics labels.
- Default with no configuration: **SQLite, zero-config, existing paths
  recognized, no relocation** (Phase 34).

---

## 10. Why the ORM question was decided "no" (Phase 3)

Explicit SQL + typed repositories + a thin driver contract already exists and
is demonstrably fast. An ORM would (a) add a mapping layer over a 70-table
monolith with no measured latency benefit, (b) complicate the batched async
write plane that the hot path depends on, (c) add migration risk to financial
columns whose semantics must not drift, and (d) introduce a heavy dependency
into a PyInstaller-frozen desktop bundle. The task explicitly permits an ORM
only on evidence; the evidence here says no.

---

## 11. Rollback strategy

- SQLite source databases are **never deleted by the migration** and remain the
  fallback.
- Provider switch writes config only after validation; failure keeps the prior
  provider.
- `nexus db rollback` restores the previous provider configuration (the data is
  still on the SQLite source; PG-side data is never the sole copy until an
  operator explicitly retires SQLite).

---

## 12. Operational trade-offs (honest)

- **More moving parts**: two pools per domain, a router, a metrics layer. The
  payoff is enforced separation and measurable latency, not simplicity.
- **PG operational burden**: the operator runs a server. Mitigated by keeping
  SQLite a fully-supported zero-config default — this is a deployment choice,
  not a deprecation.
- **One-db-per-domain means 6 databases** to create/secure/backup on PG. The
  `Test Connection` / `Inspect` flow provisions them; a single DSN still drives
  the default profile.
- **Migration wall-clock** for a 425MB audit.db + 241MB news.db is non-trivial;
  chunked resumable copying with checkpoints is the mitigation, plus quiesced
  mode so the engine is not writing into the source mid-copy.

---

## 13. Measured baselines available so far

- PostgreSQL 17.10 (live, `localhost:5432`): cold `connect` ≈ **104 ms** from
  Python with `psycopg` 3.3.6 — the argument for pooling in one number.
- SQLite audit.db: 425.7 MB, 70 tables; largest tables `research_events`
  82,726 rows, `model_governance_events` 59,161, `shadow_decisions` 55,342 —
  any "10 rows" test is unrepresentative; Phase 30 mandates 1k/10k/100k/1M
  deterministic fixtures.
- Full p50/p95/p99/max throughput tables are produced by the PR 6 benchmark
  suite, not asserted here.

---

## 14. PR plan (Phase 41)

| PR | Branch | Scope | Depends |
|---|---|---|---|
| 1 | `agent/feature/DB-FABRIC-001` | Fabric core: read plane, write plane, pools (SQL + PG), `ConsistencyRouter`, metrics, health states, fabric config, repository interfaces | — |
| 2 | `agent/feature/DB-AUDIT-002` | Audit/accounting: write plane on PG (G3), read/write split, financial durability parity tests, crash-recovery tests | PR 1 |
| 3 | `agent/feature/DB-PORTABILITY-003` | News / candle / research / shadow / incidents / hygiene consumers | PR 1 |
| 4 | `agent/feature/DB-DOMAINS-004` | models / strategies / governance / settings (incl. settings DB provider switch) | PR 1 |
| 5 | `agent/feature/DB-MIGRATION-005` | Migration engine 2.0 + cutover UX + reconciliation | PRs 2–4 |
| 6 | `agent/feature/DB-CI-006` | Benchmark suite, real PostgreSQL CI service, regression guards, leak-detection checks, docs | PRs 2–5 |

---

## 15. Compliance matrix (target states)

| Requirement | Target |
|---|---|
| Domain code free of `sqlite3` | ENFORCED IN CODE + CI (guard scans `src/nexus_scalp` outside `database/`) |
| Write through read path / read path mutates | ENFORCED IN TESTS (both providers) |
| Hot path blocks on DB writer | ENFORCED IN TESTS |
| Financial event silently dropped | ENFORCED IN TESTS (overflow + dead-letter + counters) |
| DSN password in logs | ENFORCED IN TESTS + CI (secret scan) |
| Replica used for STRONG read | ENFORCED IN TESTS |
| Provider half-switched state | ENFORCED IN TESTS |
| Migration skips / duplicates financial rows | ENFORCED IN TESTS (checksum + key parity) |
| SQLite/PG semantics diverge | ENFORCED IN TESTS (parity suite) |
| Latency claims | DOCUMENTED from measured benchmarks only |

---

## 16. Open questions (explicit)

1. Should `settings` (AppData `app_settings.db`) become provider-switchable, or
   remain local-SQLite-by-contract (it holds installation config, arguably
   machine-local)? **Current decision: make it switchable but default local**,
   since a clustered deployment may want shared settings. Revisited at PR 4.
2. `marketplace.db` and `experiments.db` — verify ownership during PR 3/4; not
   yet traced to a version authority.
3. Dual-write (cutover mode C) is designed but intentionally not enabled by
   default; it needs the divergence-detection harness before it can be trusted
   on financial writes.
