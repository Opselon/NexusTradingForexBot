# Phase 2A — Storage Inventory

generated: 2026-09-27T23:29:24.329169+00:00
repo: `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot`

## Runtime provider (from the live settings store)

- resolved provider: **postgresql**
- settings store: `C:\Users\Capsizer\AppData\Local\NexusScalpEngine\databases\app_settings.db`
  - `database.provider` = `postgresql` (source `USER_SETTINGS`)
  - `database.postgresql_config` = `{"provider": "postgresql", "domain": "audit", "host": "localhost", "port": 5432, "database": "nexusdb", "username": "postgres", "ssl_mode": "", "command_timeout_sec": 30, "migrate_on_startup": true, "pooling_enabled": true, "connect_timeout_sec": 10}` (source `USER_SETTINGS`)

## SQLite stores

| path | bytes | tables | WAL | SHM | latest ts |
| --- | --- | --- | --- | --- | --- |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\agent14_probe.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_qa2_probe.db` | 487424 | 41 | yes | yes | 2026-09-11T13:04:04.192776+00:00 |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_qa3.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_qa3d.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_qa4.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_qa4b.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_qa4c.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_qa4d.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_qa5_probe.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_qa9.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_ws.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\audit_ws2.db` | 487424 | 41 | yes | yes | None |
| `C:\c\Users\Capsizer\source\repos\NexusTradingForexBot\scratch\tmp_safety.db` | 479232 | 41 | yes | yes | None |
| `C:\Users\Capsizer\AppData\Local\NexusScalpEngine\databases\ai_provider_decisions.db` | 4096 | 1 | yes | yes | None |
| `C:\Users\Capsizer\AppData\Local\NexusScalpEngine\databases\app_settings.db` | 73728 | 5 | yes | yes | 1790551516.0217683 |

## PostgreSQL databases


## Domain map (source-derived)

### audit
- provider: sqlite file audit.db | postgresql (fabric audit plane)
- tables: audit_signals, audit_orders, audit_ledger, audit_experiences, audit_experience_outcomes, audit_executions, audit_account_snapshots, audit_dead_letter
- owner: `adapters/database/audit_repository.py (AuditRepository)`
- writer: background write queue / AuditWritePlane (fabric pooled backend under PG)
- reader: provider_store.query_rows / _connect_sqlite seam
- purpose: immutable experience ledger — source of truth for all research

### model lifecycle
- provider: sqlite experience_model_registry | PG table of same name
- tables: experience_model_registry, training_runs, model_comparisons, model_governance_events, model_governance_state, model_promotion_audit, model_rollback_audit, model_runtime_health, model_load_history
- owner: `model_lifecycle/registry.py + experience/provenance.py`
- writer: ModelLifecycleRegistry.set_status / register_candidate (SQLite-only path)
- reader: ModelLifecycleRegistry.get_status / list_models / champion
- purpose: champion/challenger lifecycle state + promotion lineage

### research / strategy
- provider: sqlite strategy_registry + research_* | PG tables of same names
- tables: strategy_registry, research_runs, research_gates, research_events, research_evidence, research_run_snapshots, research_worker_state, research_worker_heartbeat
- owner: `research/registry.py + research/pipeline.py + research/observability.py`
- writer: research pipeline _record_run; observability gate/evidence inserters
- reader: research/store.py read facade (SQLite-only path)
- purpose: derived strategy validation memory; rebuildable from the ledger

### shadow / robustness
- provider: sqlite shadow_* + shadow70_* | PG tables of same names
- tables: shadow_runs, shadow_decisions, shadow_comparisons, shadow_promotions, shadow70_observations, shadow70_events, shadow70_feature_health, shadow70_drift_alerts
- owner: `shadow/store.py + shadow/shadow70/store.py`
- writer: shadow recorders via ops_queue_write(domain=...)
- reader: shadow stores via ops_query_rows
- purpose: challenger shadow evidence; never production authority

### news intelligence
- provider: PG-only on this box (news.db present but empty in live tree)
- tables: news_articles, news_analysis, news_topics, news_entities
- owner: `news store (news.db domain)`
- writer: news worker
- reader: news API
- purpose: news analysis intelligence

### settings
- provider: sqlite application_settings table inside the audit db
- tables: application_settings, settings_audit
- owner: `settings/service.py`
- writer: settings service (source column records the actor)
- reader: settings service + DB fabric bootstrap
- purpose: persisted provider selection + audit of who changed it

### artifacts / model bundles
- provider: filesystem (gitignored)
- tables: (filesystem)
- owner: `application/live/model_bundle_store + model_generation`
- writer: trainer (model.pt + sibling scaler + signed manifest.json + model.meta.json)
- reader: champion loader / load gate
- purpose: serving model bytes + governance fingerprints

