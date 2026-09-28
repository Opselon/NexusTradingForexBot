# DATABASE CATALOG — Canonical Table & Capability Reference (TASK-DB-PLATFORM)

> Owner: Database Platform (Nexus-Main DB/data-plane) — 2026-09-02.
> Source of truth: actual `CREATE TABLE`/`ALTER TABLE` statements in code and the
> live operator databases; never docs alone. This catalog is the reference for
> completeness checks (`nexus db status/verify`, doctor, /api/db/status,
> /api/debug/state, /api/db/manage/status).
> SSOT rule: `database/manifest.py` (expected schema) and `database/registry.py`
> (expected version = baseline + migrations) must always agree — pinned by
> `tests/unit/test_database_platform_task_db.py::TestManifestRegistryAgreement`.

## 1. Persistence domains (one SQLite file / one PG database each)

| Domain | File (SQLite) | PG database | Schema version | Migration count | Bootstrap owner |
|---|---|---|---|---|---|
| audit | artifacts/audit.db | nse_audit | 7 | 6 (AUDIT-0002..0007) | AuditRepository._setup_storage + gate skeletons |
| news | artifacts/news.db | nse_news | 2 | 1 (NEWS-0002) | NewsDatabase.__init__ (SchemaMixin) |
| candle_intel | artifacts/candle_intel.db | nse_candle_intel | 2 | 1 (CANDLE-0002) | CandleIntelStore init |
| settings | %LOCALAPPDATA%/NexusScalpEngine/databases/app_settings.db | — | (not versioned) | 0 | SettingsDatabase init |
| strategies | artifacts/strategies.db | — | (not versioned) | 0 | StrategyResearchStore.ensure_schema |
| secrets | <user-data>/secrets.enc | — | n/a | n/a | SecureSecretStore (file-backed, never a DB) |

## 2. audit.db tables (required unless marked OPTIONAL)

| Table | Subsystem | Class | API consumer | UI consumer | Notes |
|---|---|---|---|---|---|
| trading_rules_config | strategy rules | REQUIRED_FOR_BOOT | /api (rules) | rules panel | seeded 30 rules; BUG-197 heal added rule_name/is_enabled/category/parameters to skeleton |
| audit_signals | signal audit | REQUIRED_FOR_FEATURE | /api/debug/state, debug | Debug tab | signal_dedup_key unique; CHG-0043 decision-evidence columns |
| audit_guard_telemetry | guard telemetry | REQUIRED_FOR_FEATURE | diagnostics | Debug | window index |
| audit_orders | execution/orders | REQUIRED_FOR_FEATURE | accounting, incidents | Performance tab | (ticket, order_id) index AUDIT-0002; execution_id ALTER |
| audit_ledger | accounting | REQUIRED_FOR_FEATURE | /api accounting, ledger UI | Performance | ticket INTEGER PK (BUG-197 retype on empty skeleton); close_time index AUDIT-0004; exit-evidence columns AUDIT-0003 (old rows stay ''/0.0 = NOT_RECORDED) |
| audit_executions | execution | REQUIRED_FOR_FEATURE | accounting | Performance | orphan check green on real DB |
| audit_account_snapshots | accounting | REQUIRED_FOR_FEATURE | accounting | Performance | 3408 rows real |
| audit_experiences / audit_experience_outcomes / audit_experience_corrections | experience | REQUIRED_FOR_FEATURE | experience worker | Debug/intelligence | |
| strategy_intelligence_registry / experience_model_registry | intelligence | REQUIRED_FOR_FEATURE | intelligence | Debug | |
| position_lifecycle_events | trade lifecycle | REQUIRED_FOR_FEATURE | TASK-3/7 forensics | Debug | |
| trade_autopsies / behavior_detections / behavior_analysis / anomaly_events | intelligence | REQUIRED_FOR_FEATURE | TASK-2 | Debug | |
| strategy_evolution_candidates | research | OPTIONAL | research | Debug | |
| intelligence_worker_state / research_worker_state | workers | LAZY_INITIALIZED | worker checkpoints | Debug | created by first worker run |
| strategy_registry / research_runs | research | REQUIRED_FOR_FEATURE | research API | Research | created by AuditRepository bootstrap (not lazy despite doctor's old wording) |
| research_gates / research_events / research_evidence / research_run_snapshots / research_worker_heartbeat | research | OPTIONAL | research | Research detail | |
| factory_generations / factory_candidates / factory_failures / factory_events / factory_runs / factory_provider_usage / factory_loop_state | strategy factory | OPTIONAL (feature) | factory API | Factory panel | LAZY via strategies/research_store bootstrap |
| incidents / incident_events / incident_value_traces / incident_quarantine | incident response | REQUIRED_FOR_FEATURE | /api/diagnostics/* | Incident Center | AUDIT-0006 |
| model_promotion_audit / model_rollback_audit | governance | REQUIRED_FOR_FEATURE | /api/models/governance/* | Governance UI | AUDIT-0005 |
| model_governance_events / model_governance_state / model_shadow_comparisons / model_runtime_health | governance | LAZY_INITIALIZED | governance, forensics | Governance UI | GovernanceStore.ensure_schema on first write |
| shadow_runs / shadow_decisions / shadow_promotions | shadow (Phase 11) | LAZY_INITIALIZED / SHADOW_ONLY | /api/models/shadow/* | Shadow UI | ShadowStore.ensure_schema on first decision; absence = NOT_INITIALIZED by design (forensics CHECK-SHD-01 treats as UNKNOWN) |
| training_runs / model_comparisons | training | LAZY_INITIALIZED | model lifecycle | Training UI | ModelLifecycleStore.ensure_schema on first run |
| shadow70_observations / shadow70_events / shadow70_feature_health / shadow70_drift_alerts | 70D shadow | SHADOW_ONLY / LAZY_INITIALIZED | /api/models/shadow70* (attach) | Shadow70 UI | Shadow70Store.ensure_schema on first observation; runtime disabled by default (INV-018) |
| release_metadata | release | REQUIRED_FOR_FEATURE | updater, /api status | Settings/About | AUDIT-0007 key/value |
| audit_broker_orders / audit_broker_deals / audit_broker_trades / audit_broker_history_meta | broker history | OPTIONAL (LIVE_ONLY) | broker sync | Performance | sync worker |
| application_settings / configuration_metadata / settings_audit | settings (encroached) | FOREIGN_SCHEMA in audit.db | settings service | Settings UI | BUG-146 legacy co-tenancy; expected extension, not corruption |
| schema_meta / schema_migrations | platform | REQUIRED_FOR_BOOT | /api/db/status, doctor | Debug | migration ledger |

## 3. news.db tables (all created by NewsDatabase.initialize_schema; version 2)

news_sources, news_articles (NOT "articles"), news_article_versions, news_entities,
news_topics, news_analysis, news_impacts, news_consensus, news_analysis_runs,
news_worker_state, news_event_links, news_trade_links, news_health,
news_analyzed_hashes, news_junk_hashes, news_prune_audit, news_ai_analysis,
schema_migrations, schema_meta.

- Doctor previously probed `articles`/`events` — tables that NEVER existed in any
  revision (proven via git -S across history) → permanent false WARN. Corrected
  (CHG-0043 health): probe `news_articles`/`news_impacts`; disabled feature →
  DISABLED state; absent DB → NOT_INITIALIZED.

## 4. candle_intel.db tables (version 2)

candles, candle_closures, candle_patterns, market_regimes, feature_vectors,
trade_proposals, trade_decisions, open_positions, exit_signals, risk_evaluations,
rule_vetoes, audit_log, schema_migrations, schema_meta.

## 5. Capability semantics (NOT the old "phase tables missing" collapse)

| State | Meaning | Example |
|---|---|---|
| REQUIRED_FOR_BOOT missing | FAIL (genuine defect) | audit_ledger absent after engine start |
| LAZY_INITIALIZED absent | NOT_INITIALIZED (INFO, never WARN) | shadow_runs before first shadow decision |
| Feature disabled | DISABLED (operator choice) | news.enabled=false, no news.db |
| Optional domain absent | NOT_APPLICABLE / NOT_INITIALIZED | strategies.db before setup runs |
| Foreign table present | EXPECTED_EXTENSION (surface, never drop) | settings tables inside audit.db |
| schema_meta missing but tables match | baseline-recorded at current version | pre-TASK-10 legacy DB |
| Version > expected | DB_DOWNGRADE_BLOCKED | rolled-back app version |

## 6. DB → repository → service → API → client map (client-visible paths)

| Capability | Repository/Store | Service/API | UI |
|---|---|---|---|
| Migration/schema truth | DatabaseMigrationEngine.status() | /api/db/status, /api/db/manage/status, doctor DATABASE | Debug DB card (schema_version — BUG-195), DB Management panel |
| Per-domain versions for operator snapshot | default_db_versions_provider (BUG-196 state key) | /api/operator/summary database section | operator summary |
| DB explorer / SQL console | db_console router | /api/db/console/* | SSMS-style panel |
| Incidents | IncidentStore (limits clamped 1..500) | /api/diagnostics/* | Incident Center |
| Shadow runs/decisions | ShadowStore.list_runs/list_decisions (limit params) | /api/models/shadow/* | Shadow UI |
| Governance audits | GovernanceStore + AUDIT-0005 tables | /api/models/governance/* | Governance UI |
| Accounting/ledger | AuditRepository ledger queries | accounting API | Performance tab |
| Release metadata | release_metadata KV | updater + /api/version | About/Update |

## 7. Retention & growth notes (audit §18, no data moved)

- Real-DB observations (2026-09-02): audit_broker_orders 10 431 / audit_broker_deals
  8 288 / audit_orders 8 463 / audit_signals 2 194 / news_articles 16 263 — bounded
  by hygiene worker (TASK-11) with news_prune_audit trail. No unbounded log table
  found; broker history rows are LIVE_ONLY sync mirrors, candidates for future
  parquet archival (documented, NOT moved here).
- Perf: `ORDER BY COALESCE(NULLIF(close_time,''), timestamp)` full-scans; the
  AUDIT-0004 covering index serves the plain `close_time` ordering path. Finding
  recorded; no hot-path change made (327 rows → 2 ms today).

## 8. PostgreSQL portability status

- Code path complete: DatabaseConfig provider switch, PostgreSQLDriver,
  ddl_port (AUTOINCREMENT→BIGSERIAL etc.), SqliteToPostgresMigrator with
  checkpoints, CI database-provider arm (real postgres:16 service).
- psycopg lives in the `postgres` extra; PG integration tests self-skip when
  NSE_PG_TEST_URL is unset (local default) and run against the CI service.
- CI URL quirk (documented, not a secret leak): the env var value is a masked
  `***` placeholder acting as a feature flag — the tests authenticate via the
  secret-store password (`_ensure_pw`), not the env URL.
