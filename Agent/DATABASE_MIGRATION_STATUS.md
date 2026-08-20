# DATABASE PORTABILITY WORKSTREAM STATUS

> Maintained by the owning agent for the duration of the SQLite/PostgreSQL portability work.
> Other agents MUST read this file before touching anything under the `Do not modify` list.
> Coordination contract: `agents/multi-agent-git-contract.md`, `agents/locks.yaml`.

```text
STATUS: DATABASE PORTABILITY / SQLITE <-> POSTGRESQL PREPARATION IN PROGRESS

OWNER:
Hermes-DBPortability (DATABASE-PORTABILITY mission)

OBJECTIVE:
Prepare the entire application for seamless SQLite/PostgreSQL operation and
migration. Centralized provider selection at the persistence boundary; no
provider checks scattered in business logic; real PostgreSQL driver, schema,
migration path, health, UI management panel, Docker dev profile and CI
matrix coverage for BOTH providers.

IMPORTANT:
Other agents MUST NOT modify the database architecture, EF Core configuration,
schema/migrations, repository persistence contracts, migration tooling, or
database UI without coordinating with this workstream.

Current phase:
Audit complete -> core portability layer implementation (provider registry,
config model, driver layer, AuditRepository/News/Candle refactor)

Current branch:
main (working tree; commits as <AGENT>: <task> per repo contract)

Files currently being modified:
- src/nexus_scalp/database/config.py        (NEW - DatabaseConfig model)
- src/nexus_scalp/database/provider.py      (NEW - provider registry/selector)
- src/nexus_scalp/database/drivers/         (NEW - sqlite + postgres drivers)
- src/nexus_scalp/database/health.py        (NEW - DatabaseHealthService)
- src/nexus_scalp/database/migrate_sqlite_to_postgres.py (NEW - data migration)
- src/nexus_scalp/adapters/database/audit_repository.py (provider-aware)
- src/nexus_scalp/news/database.py           (provider-aware)
- src/nexus_scalp/candle_intelligence/store.py (provider-aware)
- src/nexus_scalp/settings/service.py        (DatabaseConfig persistence + switch)
- src/nexus_scalp/settings/paths.py          (secrets for PG password)
- src/nexus_scalp/cli/main.py                (db provider/migrate commands)
- src/nexus_scalp/web/server.py              (new /api/db/manage/* routes)
- Web/index.html, Web/app.js                 (DATABASE MANAGEMENT tab)

Known dependencies:
- sqlite3 stdlib (SQLite provider; WAL pragmas, INSERT OR IGNORE/REPLACE,
  AUTOINCREMENT rowid identity) - must keep working as default
- psycopg3 (PostgreSQL provider; will be ADDED as optional dependency
  `nexus[postgres]`, never mandatory)
- SettingsDatabase (app_settings.db) = authoritative provider selection store
- SecretStore (DPAPI) - PostgreSQL password never on disk in plain text
- TASK-10 DatabaseMigrationEngine (per-domain schema migration, unchanged)
- AuditRepository background writer (queue-based; must stay provider-safe)
- beforePush quality gate (ruff/mypy/pytest) - verify with
  .venv/Scripts/python.exe -m <tool>

Do not duplicate this work:
TRUE
```

## Coordination

```text
DATABASE PORTABILITY WORKSTREAM: ACTIVE

Owner:            Hermes-DBPortability
Started:          2026-08-20
Branch:           main (in-tree commits; no feature branch - parallel swarm)
Do not modify:    see status block above (database config/provider/drivers,
                  AuditRepository, news/candle persistence, db settings keys,
                  /api/db/* routes, DATABASE MANAGEMENT UI tab)
Completed:
  - Full repository database-surface audit (56 sqlite3 files, 3 persistent
    domains: audit / news / candle_intel, 25+ connect sites in audit repo)
  - Provider selection point identified: AuditRepository(db_url), NewsDatabase
    (db_path), CandleIntelligenceStore(db_path) - all constructed at app
    bootstrap; settings DB holds authoritative provider config
In progress:
  - Core portability layer (config/provider/drivers/health)
  - Provider-aware repository refactor
Remaining:         migration engine, UI panel, Docker, CI, docs, tests
Known risks:
  - AUDIT tables use integer AUTOINCREMENT identity -> PG needs BIGSERIAL
    conversion; small-int identity in PG tops out at 2^31 (audit_orders etc.)
  - INSERT OR IGNORE/REPLACE / AUTOINCREMENT / PRAGMA are SQLite-only ->
    driver-level dialect wrappers (ON CONFLICT / IDENTITY / no PRAGMA)
  - datetime('now') defaults -> PG timestamp with time zone / now()
  - Parallel swarm agents may touch Web/app.js or server.py concurrently -
    re-verify HEAD before staging (contract)
  - linter pins ruff==0.16.3 in CI; local venv must match
```

## Completion

```text
DATABASE PORTABILITY WORKSTREAM: COMPLETE
(only mark after verification gate §32 passes)
```

## Log

- 2026-08-20: workstream registered; audit completed (see above).