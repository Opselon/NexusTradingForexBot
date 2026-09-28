# Nexus Scalp Engine — Database Migrations (TASK-10)

> Automatic, deterministic, idempotent schema evolution for every persistent
> domain. **No DB deletion. No manual SQL. No data loss.**

---

## 1. Migration Model

Each persistent database is an **independent schema domain** with its own
version:

| Domain | File | Baseline version | Schema version (2026-08-19) |
| :--- | :--- | :--- | :--- |
| `audit` | `artifacts/audit.db` | 1 | 4 |
| `news` | `artifacts/news.db` | 1 | 2 |
| `candle_intel` | `artifacts/candle_intel.db` | 1 | 2 |
| future | `artifacts/*.db` | — | — |

Application version ≠ database version. `nexus` 9.x may require audit schema 4,
news schema 2, candle schema 2 simultaneously (§19).

Engine states (§7):

```
DB_MIGRATION_NOT_REQUIRED   DB_MIGRATION_PENDING      DB_MIGRATING
DB_MIGRATION_SUCCEEDED      DB_MIGRATION_FAILED       DB_MIGRATION_ROLLBACK
DB_CORRUPTED                DB_BLOCKED                DB_DOWNGRADE_BLOCKED
DB_MIGRATION_IN_PROGRESS
```

## 2. Components

```
src/nexus_scalp/database/
    models.py      typed contracts: Domain, Migration, Risk, TransactionKind,
                   SchemaManifest/Table/Column, MigrationResult
    manifest.py    expected-schema manifests per domain (§13)
    registry.py    canonical ordered migrations per domain (§3/§14)
    engine.py      DatabaseMigrationEngine: plan/migrate/verify/backup/lock/
                   drift/integrity/history/repair
    gate.py        startup migration gate (§6/§7)
    __init__.py    package exports
src/nexus_scalp/cli/db_commands.py   `nexus db ...` (same engine, §24/§25)
```

## 3. Migration Registry

Every migration has an immutable ID, ordered transitions and a checksum (§41):

```text
AUDIT-0002-add-audit-orders-ticket-index     LOW   NON_TRANSACTIONAL   applied
AUDIT-0003-ledger-exit-evidence-columns      LOW   TRANSACTIONAL       applied
AUDIT-0004-ledger-close-time-index           LOW   NON_TRANSACTIONAL   applied
NEWS-0002-source-health-index                LOW   NON_TRANSACTIONAL   applied
CANDLE-0002-closure-composite-index          LOW   NON_TRANSACTIONAL   applied
```

History table `schema_migrations` (append-only):

```
migration_id (PK)  domain  version  description  checksum
applied_at         application_version  git_commit  execution_ms  status
```

## 4. Baseline Detection (§5)

A database created before this framework has **no schema_meta**. The engine:

1. inspects the actual tables,
2. if the domain's business tables are detected → records the baseline version,
3. applies only migrations after the baseline.

A fresh DB (no tables) gets baseline tables created as typed skeletons before
the first migration, so migrations that reference tables are valid. The
**application bootstrap owns the full table contract** (`CREATE TABLE IF NOT
EXISTS` is idempotent) — the migration engine never recreates existing tables
and never drops data.

## 5. Automatic Startup Migration (§6/§7/§28)

`cli/main.py::_run_engine` runs `run_startup_migration_gate()` BEFORE the
engine enters READY for all three domains:

- cheap fast path: version marker compare only (no per-column scans),
- pending safe-additive migrations applied automatically,
- failure → `DB_MIGRATION_FAILED`/`DB_BLOCKED` → engine refuses to start (§7:
  never report READY on migration failure).
- If a domain DB does not exist yet (first run) the gate marks it
  NOT_REQUIRED and the bootstrap creates it on first use.

## 6. CLI (§24/§25/§53/§54)

```
nexus db status                 # per-domain version + state (+ --json)
nexus db plan                   # dry-run plan, read-only (+ --json)
nexus db migrate                # apply pending safe migrations (+ --json)
nexus db verify                 # version + integrity + drift (+ --json)
nexus db migrations             # pending/applied catalogue (+ --json)
nexus db history                # applied history (+ --json)
nexus db repair                 # idempotent safe repair (+ --json)
nexus db create-migration       # generate a TEMPLATE (never executes)
```

All commands use the SAME canonical engine as startup. `--json` emits pure
JSON (logs silenced).

## 7. Backup & WAL Safety (§29/§30/§39)

Before any migration, the engine creates a WAL-consistent backup using the
SQLite streaming backup API (`Connection.backup()`) — this captures the main
DB plus uncheckpointed WAL data, unlike a naive `.db` copy. Backups are
stored in `<db_dir>/backups/<stem>_v<version>_<ts>.bak`.

`db migrate` / `db repair` record the backup path in the result; a failed
migration leaves the backup available for operator recovery.

## 8. Rollback / Downgrade (§15/§23)

- Transactional migrations roll the version marker back on failure.
- Non-transactional migrations (CREATE INDEX) follow
  BACKUP → PRECHECK → APPLY → VERIFY → VERSION RECORD; on failure the
  version marker is NOT advanced and the backup remains.
- **Downgrade is blocked** (`DB_DOWNGRADE_BLOCKED`) — a DB newer than the
  application's expected schema never runs silently.

## 9. Drift Detection (§12/§13)

`nexus db status` compares the ACTUAL schema (tables/columns/indexes) against
the expected manifest and classifies:

```
EXPECTED_MIGRATION   — will be closed by pending migrations
UNEXPECTED_DRIFT     — surfaced, never auto-fixed
UNKNOWN              — surfaced for operator review
```

EXTRA_COLUMN detection applies only to tables with a complete declared
contract (`full_contract=True`) so the application's own canonical columns are
never misreported.

## 10. Concurrency Lock (§18)

`<db>.migrate.lock` — an OS-level exclusive-create lock prevents two NSE
processes (engine + CLI + installer) migrating the same DB simultaneously.
The second process reports `DB_MIGRATION_IN_PROGRESS`.

## 11. Update Integration (§21/§48)

`nexus update` (TASK-9 `UpdateOrchestrator._run_migrations`) invokes the same
canonical engine for all three domains after the staged install. Order:
download/verify → backup → install staged app → run migration engine → verify
→ health → complete. A failed migration aborts the update transaction.

## 12. Health / Doctor / API (§38/§39)

- `nexus health` / `nexus doctor`: DATABASE entry includes migration state
  (READY/DEGRADED/BLOCKED).
- `GET /api/db/status`: per-domain `schema_version`, `expected_version`,
  `migration_state`, `pending_count`, `integrity`, `last_migration`,
  `tamper_detected`.

## 13. Developer Workflow (§51)

SCHEMA CHANGE → CHANGE-ID (agents/change_control.md) → MIGRATION
(registry.py) → TEST (TEST-DBM-xx) → SCHEMA MANIFEST update → DOCUMENTATION →
GATE → COMMIT. **Never add a column/table/index directly to bootstrap SQL
outside migration control.**

## 14. Release Requirements (§49)

A release that changes persistence schema MUST include: migration metadata +
tests, expected schema versions, migration notes, rollback/recovery strategy,
and a compatibility declaration (DB schema ↔ min app version).

## 15. Tests

`tests/unit/test_database_migrations_phase18.py` — TEST-DBM-01..40
`tests/unit/test_cli_db_phase18.py` — CLI parity with the engine
`scratch/probe_db_migration_scale.py` — large-DB + index performance probe