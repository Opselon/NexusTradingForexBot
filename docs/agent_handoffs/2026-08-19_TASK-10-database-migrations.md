# TASK-10 Handoff — Database Migration / Schema Evolution / Index Management

**Agent:** Hermes-DBMigrate
**Role:** Database Migration / Schema Evolution / Index Management Engineer
**Task:** TASK-10 — automatic, data-preserving schema migration for every persistent domain.
**Date:** 2026-08-19

---

## Summary

Built the canonical, deterministic, in-house SQLite-aware migration engine
(`src/nexus_scalp/database/`) that lets application releases evolve schemas
automatically — no DB deletion, no manual SQL, no data loss. Integrated into
startup (migration gate), CLI (`nexus db ...`), TASK-9 updater, health/doctor
and the web API. Migrated the real `artifacts/*.db` in place with invariants
verified unchanged.

**Starting HEAD:** `5512d40` (HERMES-TASK1: dependabot bounds + working-tree snapshot)
**Branch:** `main`

---

## 1. CURRENT DATABASE ARCHITECTURE — PROVEN
Three independent WAL-mode SQLite domains, each bootstrapped by its own module
with `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`:
- **audit.db** (`adapters/database/audit_repository.py`) — 35 tables incl. 22
  business tables (ledger, experiences, outcomes, lifecycle, behavior,
  anomaly, research, strategy registry, etc.) + ad-hoc `ALTER TABLE ADD COLUMN`
  with silent try/except.
- **news.db** (`news/database.py`) — 17 tables (articles, analysis, impacts,
  consensus, health…).
- **candle_intel.db** (`candle_intelligence/store.py`) — 15 tables (candles,
  closures, patterns, feature_vectors …).
No schema version tracking existed anywhere; health checked table presence by
name only. TASK-9 had a minimal `DatabaseMigrator` (single `schema_meta`
marker, file-byte rollback, target_version only — no real DDL registry).

## 2. MIGRATION ARCHITECTURE — PROVEN
```
src/nexus_scalp/database/
  models.py     DatabaseDomain, Migration, MigrationRisk, TransactionKind,
                MigrationState, MigrationStatus, SchemaManifest/Table/Column
  manifest.py   expected-schema manifests per domain (schema_version + tables)
  registry.py   AUDIT/NEWS/CANDLE ordered migrations (id, from, to, checksum,
                apply/verify/rollback, risk, transaction kind)
  engine.py     DatabaseMigrationEngine: plan / migrate / verify / backup /
                restore / lock / drift / integrity / history / repair
  gate.py       run_startup_migration_gate() — startup gate (§6/§7)
cli/db_commands.py  nexus db status|plan|migrate|verify|migrations|history|
                repair|create-migration (+ --json)
```

## 3. DATABASE VERSIONS — PROVEN
| Domain | baseline | current (2026-08-19) | migrations applied |
| :--- | :--- | :--- | :--- |
| audit | 1 | **4** | AUDIT-0002/0003/0004 |
| news | 1 | **2** | NEWS-0002 |
| candle_intel | 1 | **2** | CANDLE-0002 |

Per-domain independent versions (§2) — no fake global version.

## 4. BASELINE DETECTION — PROVEN
Legacy DBs (no schema_meta) are inspected: business tables present → record
baseline version → apply only later migrations. Fresh DBs get typed baseline
skeletons so migrations referencing tables are valid; the application
bootstrap owns the full table contract (idempotent CREATE IF NOT EXISTS).

## 5. MIGRATION REGISTRY — PROVEN
Checksummed immutable IDs: `AUDIT-0002-add-audit-orders-ticket-index`,
`AUDIT-0003-ledger-exit-evidence-columns`, `AUDIT-0004-ledger-close-time-index`,
`NEWS-0002-source-health-index`, `CANDLE-0002-closure-composite-index`.
History in `schema_migrations` (append-only): migration_id, domain, version,
description, checksum, applied_at, application_version, git_commit,
execution_ms, status. Tamper detection (§41) compares recorded checksums to
registry identity → `MIGRATION_TAMPERED` → BLOCKED.

## 6. INDEX MIGRATION — PROVEN (§10/§46)
- `idx_orders_ticket ON audit_orders(ticket, order_id)` ensured (the P3
  forensic finding in skill.md — query plan SCAN→INDEX).
- `idx_audit_ledger_close_time` added (documented P3).
- NEWS + CANDLE indexes aligned to REAL column names (news_health source_id +
  last_success_at; candle_closures symbol + ts).
- Measured benefit (large-DB probe): 100k-order table lookup 4.316 ms →
  **0.018 ms** (240×), EXPLAIN plan 216→62. Idempotent: no duplicates.

## 7. SCHEMA DRIFT DETECTION — PROVEN (§12)
`status()` compares actual vs manifest: MISSING_TABLE / MISSING_COLUMN /
MISSING_INDEX / EXTRA_COLUMN classified EXPECTED_MIGRATION / UNEXPECTED_DRIFT
/ UNKNOWN. Extra-column detection is limited to full-contract tables
(audit_ledger) so the app's own canonical columns are never misreported.
Unexpected drift is surfaced, never auto-fixed.

## 8. BACKUP STRATEGY — PROVEN (§29)
WAL-consistent backup via SQLite streaming backup API (captures uncheckpointed
WAL) → `<db>/backups/<stem>_v<version>_<ts>.bak`; recorded in migrate result;
restore path for compensation. TEST-DBM-18/38/39/40 verify WAL data is in the
backup and rollback preserves rows.

## 9. WAL SAFETY — PROVEN (§30/§39)
All three domains run `PRAGMA journal_mode=WAL`. Backups use
`Connection.backup()` (WAL-aware), never a bare file copy. Test proves a
WAL-mode DB with uncheckpointed data backs up and migrates safely.

## 10. STARTUP MIGRATION — PROVEN (§6/§7/§28)
`cli/main.py::_run_engine` runs the gate before READY: cheap version check;
pending safe-additive migrations applied automatically; failure →
`DB_MIGRATION_FAILED`/`DB_BLOCKED` → engine refuses to start (never READY on
failure). Missing DB (first run) → NOT_REQUIRED, bootstrap creates later.

## 11. CLI MIGRATION — PROVEN (§24/§25/§53/§54)
`nexus db status|plan|migrate|verify|migrations|history|repair`,
`nexus db create-migration` (template only). All use the same engine as
startup. `--json` emits pure JSON. Verified end-to-end via
`tests/unit/test_cli_db_phase18.py` (dry-run read-only, status shape,
migrate+verify, migrations/history, domain filter).

## 12. UPDATE INTEGRATION — PROVEN (§21/§48)
`UpdateOrchestrator._run_migrations` now runs the canonical engine for all
three domains (replaces the hardcoded single-version `DatabaseMigrator` call).
A failed migration aborts the update transaction. TEST-DBM-27 asserts the
orchestrator references `DatabaseMigrationEngine`.

## 13. CONFIG COMPATIBILITY — PROVEN
`AppConfig` paths drive the domains (`artifacts/audit.db`, `news.db`,
`candle_intel.db`); the engine resolves per-domain paths from the workspace,
same as the app bootstrap.

## 14. DB COMPATIBILITY — PROVEN
App version ≠ DB version. Downgrade blocked (`DB_DOWNGRADE_BLOCKED`) when the
DB schema is newer than the app expects; startup refuses to run.

## 15. ROLLBACK — PROVEN (§8/§15/§40)
Transactional migrations roll the version marker back on failure;
non-transactional follow BACKUP→APPLY→VERIFY→RECORD and never advance the
version on failure; a failed run records status=failed + backup path.
Compensating DROP INDEX rollback implemented for index migrations.

## 16. REAL DATA MIGRATION RESULT — PROVEN (§45)
Copies of artifacts/*.db taken, engine migrated in place:
- audit: 266 ledger rows (PnL sum −5359.84, 266 unique tickets), 229
  experiences, 74 outcomes, 3635 broker trades, 15142 signals — ALL UNCHANGED.
- news: 1677 articles, 671 impacts, 11 sources — UNCHANGED.
- candle: 339 candles — UNCHANGED.
- integrity_check = ok on all three; versions recorded; migration history
  rows present (audit 3, news 1, candle 1).

## 17. LARGE DB RESULT — PROVEN (§47)
100k orders / 50k ledger (WAL): migration 0.24 s, integrity ok, second run
idempotent (NOT_REQUIRED). Size 4.8 MB → 8.4 MB (index storage). Probe:
`scratch/probe_db_migration_scale.py`.

## 18. INDEX PERFORMANCE RESULT — PROVEN (§46)
See §6: 4.316 ms → 0.018 ms per lookup (240×), query plan evidence recorded
in the probe output file.

## 19-22. DATA INVARIANTS — PROVEN (§33-36)
Financial/news/research/model invariants all PASS on the real migrated DBs
(row counts, unique IDs, PnL aggregate, article hashes, strategy IDs,
artifact hashes unchanged). TEST-DBM-20..23 cover them in unit form.

## 23. API/UI RESULT — PROVEN (§38)
`GET /api/db/status` exposes per-domain schema_version, expected_version,
migration_state, pending_count, integrity, last_migration, tamper_detected.
(No new dashboard panel — status is surfaced via the API contract + doctor.)

## 24. HEALTH/DOCTOR RESULT — PROVEN (§39)
`nexus health`/`doctor` DATABASE entry now includes migration state:
READY / DEGRADED (pending) / BLOCKED (newer schema or failure), with
suggestions pointing at `nexus db migrate|status|repair`.

## 25-26. BUGS FOUND / FIXED
Found: no framework-level bugs were proven in legacy code beyond the existing
schema-bootstrap antipattern (ad-hoc ALTER with silent errors). No new
BUG-NNN appended — the migration system itself prevents the class of
"delete the DB" bugs; the previous bug ledger already documents related
schema/DB issues (BUG-094 schema-related, BUG-062 index-related).
Docs gap (not a runtime bug) documented as DESIGN NOTE in DATABASE_MIGRATIONS.md.

## 27-28. TESTS ADDED / RESULTS
- `tests/unit/test_database_migrations_phase18.py` — 46 tests (TEST-DBM-01..40
  plus startup/update/API-shape): ALL GREEN.
- `tests/unit/test_cli_db_phase18.py` — 5 CLI parity tests: ALL GREEN.
- Full unit suite: green except pre-existing parallel TASK-11 interference
  (`test_database_hygiene_task11.py`, `test_anomaly_verify01_duplicates.py` —
  untracked parallel files; release_system passes in isolation).
- ruff/format/mypy clean on all new files.

## 29. CI RESULT
Full beforePush cannot be green until parallel TASK-11 files merge (their
tests are untracked and interfere). The migration suite + release suite +
accounting/intelligence integration are green and CI-equivalent locally.

## 30. PERFORMANCE IMPACT — PROVEN
Startup no-op path = version lookup only (no schema scans, no DDL) when
current (§28/§35). Migration runs only when pending. Large-DB migration
bounded (0.24 s @ 100k rows).

## 31. FILES CHANGED
- src/nexus_scalp/database/ (new package: __init__, models, manifest,
  registry, engine, gate)
- src/nexus_scalp/cli/db_commands.py (new)
- src/nexus_scalp/cli/main.py (db sub-app + startup gate)
- src/nexus_scalp/release/updater.py (canonical engine in _run_migrations)
- src/nexus_scalp/release/health.py (migration state in DATABASE check)
- src/nexus_scalp/web/server.py (/api/db/status)
- tests/unit/test_database_migrations_phase18.py (new)
- tests/unit/test_cli_db_phase18.py (new)
- scratch/probe_db_migration_scale.py (+ .out.txt)
- scratch/realtime_{audit,news,candle}_pre10.db (pre-migration copies)
- docs/DATABASE_MIGRATIONS.md (new), docs/RELEASE.md (§11)
- agents/skill.md, contracts.md, runtime_invariants.md (INV-013),
  change_control.md (CHG-0002 VERIFIED), taskboard.md (TASK-10 READY_FOR_REVIEW),
  repository_state.md

## 32. COMMITS
1 pending agent-labeled commit (see Git section).

## 33. REMAINING RISKS
- The application bootstrap (`AuditRepository._create_sqlite_tables` etc.)
  still performs its own `CREATE TABLE IF NOT EXISTS` + ad-hoc ALTERs. The
  migration engine runs FIRST, then the bootstrap fills the full contract —
  additive and idempotent, but future schema changes MUST go through the
  registry (INV-013) and NOT the bootstrap (developer discipline).
- `test_database_hygiene_task11.py` + `test_anomaly_verify01_duplicates.py`
  are parallel TASK-11 untracked files that interfere with the full suite;
  coordinate with the TASK-11 owner before final merge.
- Legacy DBs whose tables predate the manifest's expected set will show
  MISSING_COLUMN/INDEX drift until the next migration closes it — surfaced,
  not auto-fixed (by design).

## 34. HANDOFF TO TASK-11

**EXACT NEXT-AGENT INSTRUCTIONS (TASK-11 — database hygiene/retention):**
1. Your hygiene/cleanup new tables and any schema changes MUST be declared as
   versioned migrations in `src/nexus_scalp/database/registry.py` (bump the
   domain version, add a checksummed migration with apply/verify/rollback)
   and the manifest table set — do NOT add DDL to bootstrap SQL.
2. Use `DatabaseMigrationEngine(db_path, domain)` + `migrate()` for any
   new-table creation; startup already runs the gate before READY (§6).
3. `tests/unit/test_database_hygiene_task11.py` and
   `tests/unit/test_anomaly_verify01_duplicates.py` currently interfere with
   the full unit suite (untracked parallel files); make them hermetic
   (isolated temp DBs, no shared state) before merge so CI can go green.
4. Re-run `nexus db verify` after your changes; keep financial/news/research
   invariants tests green (TEST-DBM-20..23).
5. Document any cleanup that touches schema in DATABASE_MIGRATIONS.md and
   follow INV-013.