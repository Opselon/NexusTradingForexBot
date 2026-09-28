# DATABASE PORTABILITY - SQLite vs PostgreSQL

> Scope: how the Nexus Scalp Engine persistence layer runs on SQLite (default)
> or PostgreSQL, how to configure each, how to switch, migrate, validate and
> recover. Implemented by the DATABASE PORTABILITY workstream (2026-08-20).

## 1. The portability contract

The application's business logic (trading, risk, signals, research, models,
features) never cares which relational database is active. Every persistent
consumer reaches the database through the portable driver boundary:

    APPLICATION (engine, web, CLI, workers)
        |
        v
    DatabaseConfig  (authoritative connection/behaviour model)
        |
        v
    Driver (SQLite | PostgreSQL)   <-- provider resolution happens HERE only
        |
        v
    shared portable SQL dialect (qmark placeholders, ON CONFLICT upserts,
    UTC ISO-8601 TEXT timestamps, explicit identity columns)

Switching providers is a configuration change at the infrastructure boundary -
never a code change in business logic.

## 2. The three persistence domains

| Domain       | SQLite default path        | PostgreSQL default database |
|--------------|----------------------------|-----------------------------|
| audit (ledger) | artifacts/audit.db       | nse_audit                   |
| news         | artifacts/news.db           | nse_news                    |
| candle_intel | artifacts/candle_intel.db   | nse_candle_intel            |

Each domain can theoretically run on a different provider, but the standard
deployment keeps ONE active provider for all domains (the DATABASE MANAGEMENT
panel shows per-domain health).

## 3. How SQLite works (default/local mode)

* Zero configuration: the engine opens the canonical artifacts/*.db files.
* WAL journaling + synchronous=NORMAL + temp_store=MEMORY pragmas.
* Identity: INTEGER PRIMARY KEY AUTOINCREMENT (rowid alias).
* Upserts: INSERT OR REPLACE / INSERT OR IGNORE / ON CONFLICT DO NOTHING.
* Timestamps: TEXT in UTC ISO-8601 (the application always writes explicit
  timestamps; DATETIME('now') is never used in the hot path).

## 4. How PostgreSQL works (scalable/production mode)

Real PostgreSQL support via psycopg (v3, optional extra: pip install 'nexus[postgres]'):

* Identity: BIGSERIAL (avoids the 2^31 SERIAL ceiling for large datasets).
* Float precision: REAL -> DOUBLE PRECISION (same 64-bit IEEE 754 storage -
  trading precision is preserved, never silently downcast).
* Upserts: ON CONFLICT (pk OR unique) DO UPDATE / DO NOTHING - SQLite REPLACE
  semantics are emulated by the driver against the table's real constraints.
* Timestamps: TIMESTAMPTZ columns with the same UTC ISO-8601 TEXT values the
  app writes - string equality and ordering are identical to SQLite.
* Schema: DDL is PORTED automatically by the migrator (INTEGER identity to
  BIGSERIAL, REAL to DOUBLE PRECISION, BLOB to BYTEA, DATETIME to TIMESTAMPTZ).

## 5. Configuration model (DatabaseConfig)

The single authoritative configuration object (src/nexus_scalp/database/config.py):

  provider, connection string, database name, host, port, username,
  password/secret reference, ssl_mode, command timeout, migrate-on-startup,
  pooling toggle, connect timeout, sqlite path/URI.

Resolution order (later wins):
1. Defaults (SQLite, canonical artifacts path)
2. Settings database  (database.provider + database.postgresql_config)
3. Environment overrides (NSE_DATABASE__PROVIDER, NSE_DATABASE__PG_HOST, ...)

Secrets: the PostgreSQL password is stored in the OS-backed SecretStore
(DPAPI on Windows). It is NEVER stored in settings, config files or source.
The UI/CLI route the password to the store once and only ever display
whether it is set.

## 6. Switching providers (safe workflow)

1. docker compose --profile postgres up -d postgres  (development) or point the
   config at an existing PostgreSQL server.
2. Configure PostgreSQL: CLI 'nexus db-portability config --host ... --port ...
   --database ... --username ... --password ...' or the DATABASE MANAGEMENT
   panel (host/port/database/user/password/SSL mode + Save Config +
   Test Connection).
3. Preview the migration: CLI 'nexus db-portability preview' or the panel's
   Preview Migration (tables, rows, estimated volume, issues).
4. Run the migration: CLI 'nexus db-portability migrate --confirm' or the
   panel's Start Migration (streamed batches, resumable, never destroys the
   SQLite source).
5. Validate: CLI 'nexus db-portability validate' or the panel's Validate
   Migration (row counts, identity maxima, financial sum checks).
6. Switch the ACTIVE provider: CLI 'nexus db-portability switch postgresql' or
   the panel's Switch Active Provider - takes effect on next application start.

## 7. Migration engine (SQLite to PostgreSQL)

  SQLite source (read-only, never modified)
      |
      v  schema inspection + ported DDL
      v  streamed batched copy (default 2,000 rows/batch, configurable)
      v  per-batch checkpointing on the destination (resume after failure)
      v  validation (row counts, identity max, financial sums per table)
      v  migration report

Safety:
  * The SQLite source is NEVER modified - the original database remains fully
    recoverable (it IS the backup).
  * Destination tables are created idempotently; re-runs are additive with
    ON CONFLICT DO NOTHING (no silent data loss).
  * Dry-run preview before any write.
  * Explicit confirmation required for a real run (UI confirm dialog / CLI
    --confirm).
  * Checkpoint table _nse_migration_checkpoints on the destination enables
    resuming an interrupted migration (batch-level, not row-level) without
    restarting from zero.

## 8. Health and diagnostics

'nexus db-portability status' and the DATABASE MANAGEMENT panel report per
domain: provider, connection status, database version, schema version,
migration state, latency, database size, table count and critical-table
availability.

## 9. Docker / CI

* Docker Compose includes an optional PostgreSQL service, profile-gated:
  docker compose --profile postgres up -d postgres.  SQLite never requires
  Docker.
* CI runs a dedicated database-provider matrix arm with a real PostgreSQL
  service container; the provider suite (tests/unit/test_database_portability.py)
  runs against SQLite always and PostgreSQL when NSE_PG_TEST_URL is set.

## 10. Limitations

* The migration engine copies table data and re-creates schema and indexes; it
  does NOT copy SQLite stored procedures or triggers (the app defines none).
* Very large tables stream in batches without loading into RAM; the batch
  size (default 2000) can be tuned for the deployment I/O profile.
* PostgreSQL requires network connectivity to the server - offline operation
  is SQLite-only (the default mode).