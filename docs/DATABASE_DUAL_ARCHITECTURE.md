# Nexus Scalp Engine — Dual Database Architecture

## SQLite + PostgreSQL: Complete Provider Isolation, UI Migration, Observability, Purging & Performance

### 1. Architectural Invariant & Topology

Nexus Scalp Engine implements a **production-grade dual-database architecture** where **SQLite** and **PostgreSQL** are first-class, independent, fully supported operational providers.

```text
                               NEXUS SCALP ENGINE
                                      │
                           ┌──────────┴──────────┐
                           │                     │
                    SQLite Provider       PostgreSQL Provider
                           │                     │
                     Full Backend          Full Backend
                           │                     │
                    All Repositories       All Repositories
                           │                     │
                    All Operational       All Operational
                         Domains               Domains
```

#### The Golden Rule: Single Authoritative Provider
At any moment, exactly one database provider is active:
- **`ACTIVE_PROVIDER = sqlite`** $\implies$ **All Reads $\to$ SQLite, All Writes $\to$ SQLite**
- **`ACTIVE_PROVIDER = postgresql`** $\implies$ **All Reads $\to$ PostgreSQL, All Writes $\to$ PostgreSQL**

**Forbidden Anti-Patterns:**
1. **Never Split Read/Write**: `WRITE → PostgreSQL` with `READ → SQLite` is prohibited.
2. **Never Split Domains**: `audit → PostgreSQL`, `news → SQLite`, `orders → PostgreSQL` is prohibited.
3. **No Silent Fallback**: If PostgreSQL becomes unreachable while active, the engine fails loud into a safe state rather than silently degrading to SQLite with stale or conflicting data.

---

### 2. Provider Roles & Product Experience

| Dimension | SQLite Provider | PostgreSQL Provider |
| :--- | :--- | :--- |
| **Role** | Zero-dependency local default | Professional / High-performance path |
| **First Run** | Starts out of the box; no server needed | Optional opt-in configured via UI or env |
| **Concurrency** | Single-writer WAL mode | Multi-worker pooled connections (`PgPool`) |
| **Storage Engine** | Embedded B-Tree file (`artifacts/*.db`) | PostgreSQL server (`localhost:5432/nexusdb`) |
| **Use Case** | Local development, desktop runs, CI testing | Live trading, multi-engine telemetry, analytics |

---

### 3. Provider Abstraction Contract

Persistence consumers interact strictly with the abstract `DatabaseDriver` boundary (`nexus_scalp.database.drivers.base`):
- `driver.query(sql, args)`: Executes parameterized queries, returning row dictionaries.
- `driver.scalar(sql, args)`: Evaluates single scalar expressions.
- `driver.execute(sql, args)`: Executes DDL/DML statements.
- `driver.executemany(sql, params)`: Executes batched parameterized inserts.
- `driver.transaction()`: Context manager guaranteeing atomic unit of work with automatic rollback on error.
- `driver.table_exists(table)` & `driver.table_columns(table)`: Introspection.

Dialect differences (e.g. `%s` vs `?`, `rowid` vs `ctid`, `BIGSERIAL` vs `INTEGER PRIMARY KEY AUTOINCREMENT`) are isolated within `SQLiteDriver` and `PostgreSQLDriver`.

---

### 4. Provider Switching Lifecycle & State Machine

Switching active database providers is **not a simple configuration toggle**. It is governed by a formal state machine (`nexus_scalp.database.provider_lifecycle`):

```text
   CONFIGURED ──► TESTING ──► MIGRATING ──► VERIFYING ──► READY ──► ACTIVE
       │                                                    ▲
       └─────────────────── FAILED ◄────────────────────────┘
```

#### Migration Protocols
1. **SQLite $\to$ PostgreSQL Migration**:
   - `SqliteToPostgresMigrator` (`migrate_engine.py` / `migrate_copier.py`) streams data in transactional batches.
   - Compares table row counts and financial aggregates (PnL sums, equity).
   - Only marks status `READY` when all integrity gates pass.

2. **PostgreSQL $\to$ SQLite Reverse Migration**:
   - `PostgresToSqliteMigrator` (`migrate_reverse.py`) streams tables back from PostgreSQL to SQLite.
   - Performs divergence analysis (`check_divergence()`) warning operators if PostgreSQL contains unmigrated operational records.
   - Requires explicit operator confirmation before activating.

---

### 5. Data Lifecycle & Domain-Aware Purging

The purge engine (`nexus_scalp.database.lifecycle`) maintains database performance and bounds disk consumption without destroying audit integrity:

#### Data Tier Classification
- **`IMMUTABLE`**: Financial ledger and audit evidence. **NEVER purged.** Protected by `ImmutableDataProtectionError`:
  - `audit_ledger`, `audit_orders`, `audit_broker_orders`, `audit_broker_deals`, `audit_broker_trades`, `audit_executions`, `audit_account_snapshots`, `trade_decisions`, `risk_evaluations`.
- **`HOT`**: Active operational data within near-term execution windows.
- **`WARM`**: Operational history retained for near-term reporting.
- **`COLD`**: Analytical history archive.
- **`PURGEABLE`**: High-frequency telemetry, moving positions, and transient staging:
  - `audit_signals` (> 30 days)
  - `position_lifecycle_events` (`POSITION_MOVING` events > 14 days)
  - `audit_guard_telemetry` (> 7 days)
  - `model_runtime_health` (> 14 days)

#### Safe Batched Deletion & Maintenance
- Purges execute in chunks of 2,000–5,000 rows with commits per chunk to prevent long table locks and transaction bloat.
- **PostgreSQL**: Runs `ANALYZE` and standard `VACUUM` on active tables (never routine `VACUUM FULL`).
- **SQLite**: Runs `PRAGMA analyze`, `PRAGMA incremental_vacuum`, and `PRAGMA integrity_check`.

---

### 6. Database Observability & Structured Logging

- **Persistence Policy**: Normal `INFO`/`DEBUG` statements route to application log streams. Persistent storage in `db_operation_logs` (`nexus_scalp.database.log_store`) is strictly reserved for:
  - `WARNING`
  - `ERROR`
  - `CRITICAL`
- **Secret Redaction**: DSN passwords, tokens, and API credentials are automatically masked via `mask_query_text` (`user:***@host`).
- **Log Retention**: Persisted error records expire after configurable retention (default 30 days) and are pruned by `purge_expired()`.

---

### 7. Analytical Views

Pre-aggregated views (`nexus_scalp.database.views`) standardize repeated complex operational queries:
- `v_trade_timeline`: Chronological ledger outcomes and execution timings.
- `v_equity_curve`: Balance, equity, margin, and floating PnL progression.
- `v_broker_reconciliation`: Local orders joined with broker orders and deals.
- `v_risk_summary`: Active safety rule thresholds and parameters.
- `v_dashboard_summary`: Rolling operational aggregates.

---

### 8. Verification & Repository Contract Test Gates

All database invariants are enforced by unit and contract test suites:
- `tests/unit/test_database_contract.py`: Neutral driver contract semantics.
- `tests/unit/test_database_provider_lifecycle.py`: State machine, reverse migration, and divergence checks.
- `tests/unit/test_database_lifecycle_purge.py`: Tiering, immutable table protection, and batched purges.
- `tests/unit/test_database_log_store.py`: Structured error logging, redaction, and retention.
- `tests/unit/test_database_views.py`: View creation and cross-table queries.
- `tests/unit/test_pg_audit_read_plane_routing.py`: Active provider read/write routing without degradation.
