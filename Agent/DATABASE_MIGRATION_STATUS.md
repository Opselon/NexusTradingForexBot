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
Other agents MUST NOT modify the database architecture, schema/migrations,
repository persistence contracts, migration tooling, or database UI without
coordinating with this workstream.

Current phase:
Implementation complete -> verification + final report

Current branch:
main (commits as <AGENT>: <task> per repo contract)

Files already modified/created (committed):
- src/nexus_scalp/database/provider.py        (NEW - DatabaseProvider registry/selector)
- src/nexus_scalp/database/config.py          (NEW - DatabaseConfig model + loader)
- src/nexus_scalp/database/drivers/          (NEW - base/sqlite/postgres/proxy drivers)
- src/nexus_scalp/database/health.py          (NEW - DatabaseHealthService)
- src/nexus_scalp/database/ddl_port.py        (NEW - SQLite DDL to PostgreSQL DDL porter)
- src/nexus_scalp/database/migrate_copier.py  (NEW - streamed batch copier + checkpoints)
- src/nexus_scalp/database/migrate_engine.py  (NEW - migration orchestrator + validation)
- src/nexus_scalp/adapters/database/audit_repository.py (provider-aware, portable SQL)
- src/nexus_scalp/news/database.py             (provider-aware via portable proxy)
- src/nexus_scalp/candle_intelligence/store.py  (provider-aware via portable proxy)
- src/nexus_scalp/settings/service.py          (provider switch + PG config + secret store)
- src/nexus_scalp/cli/db_commands.py           (nexus db-portability commands)
- src/nexus_scalp/cli/main.py                  (registers db-portability group)
- src/nexus_scalp/web/server.py               (DATABASE MANAGEMENT API routes)
- Web/index.html + Web/app.js                (DATABASE MANAGEMENT UI tab + JS)
- docker-compose.yml                          (optional postgres profile)
- .github/workflows/ci.yml                    (database-provider matrix arm + PG service)
- tests/unit/test_database_portability.py     (34-test provider matrix suite)
- docs/DATABASE.md                            (full documentation)

Completed:
  - Full repository database-surface audit (56 sqlite3 files, 3 persistent
    domains, 25+ connect sites)
  - Provider registry + DatabaseConfig + secret-store password handling
  - SQLite + PostgreSQL drivers (real psycopg3), portable proxy, DDL porter
  - Migration engine (preview/run/validate/resume/backup) verified against
    a REAL PostgreSQL 16 container
  - DATABASE MANAGEMENT UI tab + API + CLI
  - Docker compose PG profile + CI database-provider arm
  - docs/DATABASE.md
  - Provider matrix test suite (34 tests, includes real PG integration)

In progress:
  - Full beforePush gate verification
  - Final report

Remaining:
  - Telegram final report to operator

Known risks:
  - Parallel swarm agents keep committing around this work; re-verify HEAD
    before staging (contract). My commits are incremental and isolated.
  - psycopg is an optional extra; SQLite remains fully functional without it.
  - The PG integration tests require NSE_PG_TEST_URL - CI provides it.

Do not modify:
  - src/nexus_scalp/database/ (provider registry, config, drivers, migrator,
    health, DDL porting)  [owned by this workstream]
  - persistence adapters (audit_repository, news/database, candle store)
  - settings provider keys (database.provider / database.postgresql_config)
  - /api/db/manage/* routes
  - DATABASE MANAGEMENT UI tab
  - docs/DATABASE.md
  - docker-compose.yml postgres service

Do not duplicate this work:
TRUE
```

## Coordination

```text
DATABASE PORTABILITY WORKSTREAM: ACTIVE

Owner:            Hermes-DBPortability
Started:          2026-08-20
Branch:           main
Completed:        architecture, providers, migration, UI, Docker, CI, tests
In progress:      final verification + report
Remaining:        none (barring gate findings)
```

## Log

- 2026-08-20: workstream registered; audit completed.
- 2026-08-20: core layer, providers, migration engine, UI, CLI, Docker, CI,
  tests all implemented and committed. PG integration verified against a real
  PostgreSQL 16 container.