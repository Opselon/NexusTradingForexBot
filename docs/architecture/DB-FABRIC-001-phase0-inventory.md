# DATABASE FABRIC — Phase 0/1 Inventory (evidence-based)

Generated from a full-repo scan of `agent/feature/DB-FABRIC-001` (origin/main @ 9a6a0c21)
plus a read-only live-schema probe of the production SQLite databases.
Method: automated pattern sweep (10,148 hits, 897 files), then per-consumer
classification, then live `PRAGMA table_xinfo` / row-count enumeration on the
real databases (read-only URI mode — WAL files left attached, engine undisturbed).

---

## 1. Physical persistence inventory (LIVE, read-only probe)

| DB file | Size | Tables | Notes |
|---|---|---|---|
| `artifacts/audit.db` | **425.7 MB** | **70** | The monolith. Real domain count ≫ 3. |
| `artifacts/news.db` | 241.4 MB | 22 | news + calendar + AI analysis |
| `artifacts/strategies.db` | 30.5 MB | 8 | strategy factory / research |
| `artifacts/candle_intel.db` | 11.0 MB | 14 | candle + regime + risk evaluations |
| `artifacts/models.db` | 0.1 MB | 2 | model checkpoints + load history |
| `%LOCALAPPDATA%/NexusScalpEngine/databases/app_settings.db` | 0.2 MB | 3 | settings (NOT under artifacts/) |
| `artifacts/marketplace.db` | 0.15 MB | — | model marketplace |
| `artifacts/experiments/experiments.db` | — | — | model lab experiments |
| `artifacts/archive/_hygiene_state/hygiene_state.db` | — | — | hygiene archive |
| `artifacts/_quarantine/quarantine_store.db` | — | — | quarantine |
| Zero-byte decoys | 0 | 0 | `app_settings.db` (repo copy), `nexus.db`, `nexus_scalp.db`, `research.db`, `settings.db` |

Zero-byte decoys are documented NSE behavior: the live settings DB is the
AppData copy, never the repo copy.

---

## 2. REAL domain map — 16 domains, not 3

The existing `DatabaseDomain` enum only knows AUDIT / NEWS / CANDLE_INTEL.
The audit.db monolith physically co-locates 12+ logical domains. Domains
derived from actual table ownership + file layout:

| # | Domain | Physical home | Tables (owner) | Largest tables | Provider switching? |
|---|---|---|---|---|---|
| 1 | audit_ledger | audit.db | `audit_ledger`, `audit_orders`, `audit_executions*`, `audit_broker_*` | audit_broker_orders 10.6k | yes (v1..v9) |
| 2 | signals_telemetry | audit.db | `audit_signals` 8.1k, `audit_guard_telemetry`, `audit_paper_executions` | 8.1k | yes |
| 3 | experience | audit.db | `audit_experiences` 4.3k, `audit_experience_outcomes` 1.6k, `audit_experience_corrections`, `position_lifecycle_events` 4.3k | 4.3k | yes |
| 4 | snapshots_risk | audit.db | `audit_account_snapshots` 7.2k, `runtime_risk_state` | 7.2k | yes |
| 5 | research | audit.db | `research_events` **82,726**, `research_evidence` 18.3k, `research_gates` 26.1k, `research_runs` 4.4k, `research_run_snapshots`, `research_worker_*` | **82.7k** | yes |
| 6 | models_governance | audit.db | `model_governance_events` **59,161**, `model_runtime_health` 1.7k, `model_comparisons`, `model_promotion_audit`, `model_rollback_audit`, `model_governance_state`, `experience_model_registry` | **59.2k** | yes |
| 7 | models_registry | models.db | `model_checkpoints` 68, `model_load_history` 23 | 68 | **NO — never in DatabaseDomain** |
| 8 | strategies_factory | strategies.db | `factory_*` (7 tables) + `strategy_research_meta` | factory_failures 8.9k | partial (own store, own DDL, not in enum) |
| 9 | strategy_intelligence | audit.db | `strategy_intelligence_registry` 355, `strategy_registry` 4.1k, `strategy_evolution_candidates` | 4.1k | yes (no own version) |
| 10 | shadow | audit.db | `shadow_decisions` **55,342**, `shadow_runs` 63, `shadow_comparisons`, `shadow_promotions` + `shadow70_*` | **55.3k** | yes |
| 11 | news | news.db | `news_articles` 24.4k, `news_impacts` 23k, `news_entities` 60.3k, `news_topics` 41.6k, `news_analysis*`, `calendar_events`, `news_sources` | 60.3k | yes |
| 12 | candle_intel | candle_intel.db | `candles`, `candle_closures`, `candle_patterns`, `market_regimes`, `risk_evaluations`, `trade_decisions`, `rule_vetoes` | 10.8k | yes |
| 13 | incidents | audit.db | `incidents` 8, `incident_events`, `incident_quarantine`, `incident_value_traces` | 8 | yes (no own version) |
| 14 | hygiene | audit.db | `hygiene_state` (external file), quarantine tables | small | yes (no own version) |
| 15 | behavior_intelligence | audit.db | `behavior_detections` 323, `behavior_analysis` 414, `anomaly_events` | 414 | yes (no own version) |
| 17 | settings | app_settings.db (AppData) | `application_settings` 49, `configuration_metadata` 7, `settings_audit` 1.4k | 1.4k | **NO — hardcoded SQLite, AppData path** |

**Domain 7 (models.db) and 17 (settings) are NOT in `DatabaseDomain` and are
NOT covered by provider switching.** The task statement's "verify whether
models.db/strategies.db are included" → models.db: **excluded**;
strategies.db: partially portable (own store, not in the enum).

---

## 3. Consumer classification (297 non-test/non-doc consumers)

From the full sweep (schema: `.db` / `.sqlite` / PRAGMA / WAL / `sqlite3` /
DDL / transaction patterns; 897 files, 10,148 hits):

| Classification | Count | Meaning |
|---|---|---|
| FULLY_PORTABLE | 71 | no sqlite3 import; provider-abstracted already |
| DRIVER_PORTABLE | 135 | no direct sqlite3 import; may use SQLite-shaped SQL via driver |
| PARTIALLY_PORTABLE | 26 | imports sqlite3 for types/connection only |
| **DIRECT_SQLITE** | **59** | own schema, own DDL, own connection handling — hard porting |
| UNKNOWN | **0** | all 6 initial UNKNOWNs resolved (below) |

### The 59 DIRECT_SQLITE consumers (by domain)

- **audit**: `adapters/database/audit_repository.py` (4,031 LOC), `experience/evaluator.py`, `experience/outcome_recovery.py`, `experience/spread_sketch.py`
- **models** (6): `model_generation/model_registry.py`, `model_lab/experiment_registry.py`, `model_lifecycle/{registry,store,learning_cycle}.py`, `model_provisioning/official_install.py`
- **strategies**: `strategies/factory/store.py`
- **news**: `news/memory/post_event.py`
- **candle_intel**: `candle_intelligence/store.py`
- **governance** (4): `governance/{engine,evidence,load_gate,store}.py`
- **hygiene** (9): `hygiene/{archive,consistency,detectors,hygiene_runtime,index_health,quarantine,state,worker,worker_runner}.py`
- **incidents** (3): `incidents/{occurrences,store,trace_lineage}.py`
- **research** (5): `research/{archive,champion_ceiling,contract_check,observability,store}.py`
- **shadow** (2): `shadow/store.py`, `shadow/shadow70/store.py`
- **forensics** (4): `forensics/{checks_support,experience_gap,news_sources}.py` + `scripts/forensics/exit_policy_counterfactual.py`
- **settings** (1): `settings/service.py`
- **release_ops** (5): `release/{diagnostics,health}.py`, `release/update_engine/{backup_migrate,orchestrator}.py`, `smoke/runner.py`
- **surface** (6): `cli/{db_commands,doctor}.py`, `web/{api_v1/incidents,calibration_monitor,debug_research_routes,operator_routes}.py`
- **other** (7): `adapters/database/{broker_history,dead_letter_store,executions_idempotency}.py`, `risk/concurrency_policy.py`, `storage/runtime.py`, CI/runtime scripts

### UNKNOWN → resolved (0 remain)

| File | Resolution |
|---|---|
| `experience/decision_evidence.py` | PARTIALLY_PORTABLE — `sqlite3.Connection` type annotation + `sqlite3.OperationalError` catch, no connect |
| `model_lifecycle/champion_sentinel.py` | PARTIALLY_PORTABLE — `sqlite3.Row` row factory only |
| `news/database.py` | DRIVER_PORTABLE — uses `get_driver`, imports sqlite3 only for `sqlite3.Row` |
| `release/versioning.py` | FULLY_PORTABLE — comment references only |
| `strategies/factory/orchestrator.py` | DIRECT_SQLITE — raw `_sq.connect` at line 1258 |
| `model_lifecycle/learning_loop.py` | FULLY_PORTABLE — comment references only |

---

## 4. Hot-path findings (Phase 23 evidence)

`AuditRepository` is the only DB writer on the tick path, and it is **already
fully async** — `_enqueue_financial` / `_enqueue_telemetry` feed a
`queue.Queue(maxsize=10000)` drained by a single background worker (batch ≤
500/transaction, `executemany` grouped by statement). No synchronous insert on
the tick path.

But: the PG path is empty. `_is_sqlite` gates the whole writer —
`_process_queue_worker` returns immediately when `not self._is_sqlite`, and
every write method early-returns. **Under PostgreSQL the audit domain records
nothing.** 20+ `if not self._is_sqlite: return` sites. This is the single
largest portability defect: the abstraction exists but the write plane was
never implemented for PG.

---

## 5. Existing infrastructure — what already exists (do NOT rebuild)

- `DatabaseConfig` + `load_database_config` (settings DB + `NSE_DATABASE__*` env resolution order)
- `mask_url_password`, `resolve_password`, `build_postgres_url` (secret store → URL at connect time)
- `SecureSecretStore` (OS-backed, DPAPI on Windows) — reuse, do not reinvent
- `DatabaseProvider.parse/from_url`, `default_sqlite_path`, `url_for_provider`
- `DatabaseDriver` ABC with `query_readonly` — SQLite enforces a **real C-level authorizer** (not a stub); PG `query_readonly` is a passthrough stub
- `SQLiteDriver`: WAL, synchronous=NORMAL, temp_store=MEMORY, shared-memory support, single connect site
- `PostgreSQLDriver`: psycopg v3, placeholder translation, `ON CONFLICT` upsert/insert-ignore, BIGSERIAL identity, DDL porting, `pg_type_for`
- `port_create_table` (ddl_port.py) — SQLite → PG DDL translation
- `DatabaseMigrationEngine` (1,270 LOC) + `REGISTRY` (771 LOC) + manifests (audit v9, news, candle_intel)
- `run_startup_migration_gate` — safe additive migration gate
- CLI: `nexus db status/plan/migrate/verify/migrations/history/repair/doctor` + portability sub-app (status/config/switch/test/preview/migrate/validate/backup)
- Web: `/api/db/console/*` SSMS-style explorer, already driver-abstracted

## 6. What is genuinely missing (the real work)

| # | Gap | Phase | Severity |
|---|---|---|---|
| G1 | **No read/write plane separation** — driver methods are single-plane; `query_readonly` optional, no routing, no pool, no consistency classes | P4/P5 | **P0** |
| G2 | **No PostgreSQL pooling** — `PostgreSQLDriver.connect` = 1 connection per call, no `psycopg_pool` | P6 | **P0** |
| G3 | **AuditRepository write plane absent on PG** — `_is_sqlite` disables everything; `_process_queue_worker` no-ops | P9/P10 | **P0** |
| G4 | **No SQLite writer model** — 155 raw `sqlite3.connect` sites in `src/`; read-only connections only in forensics; no read pool | P7 | **P0** |
| G5 | **Domain map incomplete** — `DatabaseDomain` = 3, reality = 16; models.db/settings not switchable | P1 | P1 |
| G6 | **59 consumers with own DDL/connection handling** — competing schema authorities | P14 | P1 |
| G7 | **No PostgreSQL schema authority for the 70-table audit monolith** — manifest covers a subset | P14 | P1 |
| G8 | **Cross-provider SQL parity untested** — 38 AUTOINCREMENT / 32 INSERT OR REPLACE / 10 BEGIN IMMEDIATE / 310 sqlite datetime call sites | P11 | P1 |
| G9 | **No benchmark suite** — latency claims unmeasured | P29 | P1 |
| G10 | **PostgreSQL not in CI** — optional extra, skipped when absent | P49 | P1 |
| G11 | **Timestamp/type contract informal** — no provider-parity proof | P12/P13 | P2 |
| G12 | **DB health collapsed to a few states** — no DEGRADED/POOL_EXHAUSTED/MIGRATION_* granularity | P27 | P2 |
| G13 | **Migration engine lacks resumable checkpoints / checksums / dry-run for SQLite→PG** — current migrator is PG→SQLite direction only (check) | P15 | P1 |
| G14 | **Settings DB hardcoded SQLite + AppData** — not provider-switchable | P4 | P2 |

---

## 7. SQLite-specific surface to isolate (Phase 11 audit)

From the sweep:

| Pattern | Files | Owner going forward |
|---|---|---|
| `PRAGMA` | 183 | SQLiteDriver only |
| `sqlite_master` | 37 | SQLiteDriver only |
| `INSERT OR REPLACE` | 32 | `driver.upsert()` |
| `INSERT OR IGNORE` | 32 | `driver.insert_ignore()` |
| `AUTOINCREMENT` | 38 | `driver.identity_ddl()` |
| `BEGIN IMMEDIATE` | 10 | `driver.transaction()` |
| `last_insert_rowid` | 4 | `driver.last_insert_rowid()` |
| `datetime()`/`strftime()`/`julianday` | 310 | banned from domain SQL; app writes explicit UTC ISO strings |
| `json_extract` | 3 | banned; serialize in code |
| `file::memory:`/`mode=ro` | 186 | SQLiteDriver only |
| `VACUUM`/`ATTACH` | 52 | SQLiteDriver only |

---

## 8. Safety constraints discovered (must respect)

- **Read-only URI probes are mandatory** against live DBs (`file:...?mode=ro`, uri=True) — WAL stays attached, engine undisturbed. Applied throughout this audit.
- `audit.db` is 425MB / 70 tables — **never** auto-drop, auto-VACUUM-full, or rewrite. Migration is copy-only, source untouched.
- `settings.service.SettingsDatabase` opens AppData with `check_same_thread=False` + one shared connection — the provider switch must not relocate it silently.
- `SecureSecretStore` is the ONLY secret mechanism. No new password storage.
- Repo is `core.autocrlf=true`; compare blob-vs-blob after CRLF normalization.
- Worktree pytest needs `PYTHONPATH=src` (editable install resolves to main checkout's `src/`).

---

## 9. Next steps (per Phase 54 execution order)

1. **Phase 2 ADR** — target fabric architecture (this task, next artifact)
2. **PR 1 — Fabric core**: read plane, write plane, pools, routing, consistency classes, metrics, health (G1, G2, G4)
3. **PR 2 — Audit/accounting**: AuditRepository write plane on PG (G3) + read/write split + financial durability proofs
4. **PR 3 — News/candle/research**, **PR 4 — models/strategies/governance/settings** (G5, G6)
5. **PR 5 — Migration engine 2.0** + cutover UX (G13)
6. **PR 6 — CI enforcement + benchmarks + docs** (G8, G9, G10)
