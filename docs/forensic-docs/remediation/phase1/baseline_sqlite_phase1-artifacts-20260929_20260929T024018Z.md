# SQLite zero-write baseline (phase1-artifacts-20260929)

Collected (UTC): 20260929T024018Z

## C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db
- size_bytes: 436670464
- mtime: 2026-09-28T01:30:02.100303+00:00
- wal_present: True (size 0)
- quick_check: ok
- pragmas: journal_mode=wal synchronous=2 page_size=4096 page_count=106609 freelist_count=734 auto_vacuum=0

| table | rows |
|---|---|
| anomaly_events | 22 |
| application_settings | 0 |
| audit_account_snapshots | 8015 |
| audit_broker_deals | 8482 |
| audit_broker_history_meta | 1 |
| audit_broker_orders | 10647 |
| audit_broker_trades | 4115 |
| audit_dead_letter | 45 |
| audit_executions | 3 |
| audit_executions_reconciled | 954 |
| audit_experience_corrections | 0 |
| audit_experience_outcomes | 1715 |
| audit_experiences | 5239 |
| audit_guard_telemetry | 506 |
| audit_ledger | 435 |
| audit_orders | 2379 |
| audit_paper_executions | 0 |
| audit_signals | 11266 |
| behavior_analysis | 432 |
| behavior_detections | 333 |
| configuration_metadata | 0 |
| experience_model_registry | 26 |
| factory_candidates | 295 |
| factory_events | 4 |
| factory_failures | 0 |
| factory_generations | 3 |
| factory_loop_state | 1 |
| factory_provider_usage | 0 |
| factory_runs | 0 |
| incident_events | 13 |
| incident_quarantine | 0 |
| incident_value_traces | 0 |
| incidents | 8 |
| intelligence_worker_state | 1 |
| learning_cycle_events | 0 |
| learning_cycles | 0 |
| model_comparisons | 4 |
| model_governance_events | 59164 |
| model_governance_state | 0 |
| model_promotion_audit | 0 |
| model_rollback_audit | 0 |
| model_runtime_health | 1935 |
| model_shadow_comparisons | 63 |
| position_lifecycle_events | 3817 |
| release_metadata | 0 |
| research_events | 82726 |
| research_events_archive | 0 |
| research_evidence | 18316 |
| research_evidence_archive | 0 |
| research_gates | 26140 |
| research_run_snapshots | 4357 |
| research_runs | 4357 |
| research_worker_heartbeat | 1 |
| research_worker_state | 1 |
| runtime_risk_state | 1 |
| schema_meta | 1 |
| schema_migrations | 8 |
| settings_audit | 0 |
| shadow70_drift_alerts | 0 |
| shadow70_events | 0 |
| shadow70_feature_health | 0 |
| shadow70_observations | 2 |
| shadow_comparisons | 61 |
| shadow_decisions | 55342 |
| shadow_promotions | 0 |
| shadow_runs | 63 |
| strategy_evolution_candidates | 1 |
| strategy_intelligence_registry | 373 |
| strategy_registry | 4109 |
| trade_autopsies | 395 |
| trading_rules_config | 30 |
| training_runs | 6 |

## C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\news.db
- size_bytes: 241434624
- mtime: 2026-09-28T01:45:32.553047+00:00
- wal_present: True (size 0)
- quick_check: ok
- pragmas: journal_mode=wal synchronous=2 page_size=4096 page_count=58944 freelist_count=1973 auto_vacuum=0

| table | rows |
|---|---|
| calendar_events | 293 |
| calendar_worker_state | 1 |
| news_ai_analysis | 2705 |
| news_analysis | 20388 |
| news_analysis_runs | 25587 |
| news_analyzed_hashes | 15020 |
| news_article_versions | 0 |
| news_articles | 24919 |
| news_consensus | 0 |
| news_entities | 61265 |
| news_event_links | 0 |
| news_health | 12 |
| news_impacts | 23381 |
| news_junk_hashes | 9249 |
| news_post_event | 0 |
| news_prune_audit | 9269 |
| news_sources | 13 |
| news_topics | 42288 |
| news_trade_links | 0 |
| news_worker_state | 1 |
| schema_meta | 1 |
| schema_migrations | 1 |

## C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\strategies.db
- size_bytes: 30511104
- mtime: 2026-09-27T09:44:50.613566+00:00
- wal_present: False (size 0)
- quick_check: ok
- pragmas: journal_mode=delete synchronous=2 page_size=4096 page_count=7449 freelist_count=0 auto_vacuum=0

| table | rows |
|---|---|
| factory_candidates | 4108 |
| factory_events | 4445 |
| factory_failures | 8932 |
| factory_generations | 29 |
| factory_loop_state | 2 |
| factory_provider_usage | 27 |
| factory_runs | 3926 |
| strategy_research_meta | 2 |

## C:\Users\Capsizer\source\repos\nse-review-main\artifacts\audit.db
- size_bytes: 435593216
- mtime: 2026-09-27T00:15:00.883627+00:00
- wal_present: False (size 0)
- quick_check: ok
- pragmas: journal_mode=wal synchronous=2 page_size=4096 page_count=106346 freelist_count=0 auto_vacuum=0

| table | rows |
|---|---|
| ai_provider_activation | 0 |
| ai_provider_config | 0 |
| anomaly_events | 22 |
| application_settings | 0 |
| audit_account_snapshots | 8038 |
| audit_broker_deals | 8482 |
| audit_broker_history_meta | 1 |
| audit_broker_orders | 10647 |
| audit_broker_trades | 4115 |
| audit_dead_letter | 45 |
| audit_executions | 3 |
| audit_executions_reconciled | 954 |
| audit_experience_corrections | 0 |
| audit_experience_outcomes | 1715 |
| audit_experiences | 5239 |
| audit_guard_telemetry | 509 |
| audit_ledger | 435 |
| audit_orders | 8721 |
| audit_paper_executions | 0 |
| audit_signals | 11300 |
| behavior_analysis | 432 |
| behavior_detections | 333 |
| configuration_metadata | 0 |
| experience_model_registry | 26 |
| factory_candidates | 295 |
| factory_events | 4 |
| factory_failures | 0 |
| factory_generations | 3 |
| factory_loop_state | 1 |
| factory_provider_usage | 0 |
| factory_runs | 0 |
| incident_events | 13 |
| incident_quarantine | 0 |
| incident_value_traces | 0 |
| incidents | 8 |
| intelligence_worker_state | 1 |
| learning_cycle_events | 0 |
| learning_cycles | 0 |
| model_comparisons | 4 |
| model_governance_events | 59161 |
| model_governance_state | 0 |
| model_promotion_audit | 0 |
| model_rollback_audit | 0 |
| model_runtime_health | 1940 |
| model_shadow_comparisons | 63 |
| position_lifecycle_events | 3888 |
| release_metadata | 0 |
| research_events | 82726 |
| research_events_archive | 0 |
| research_evidence | 18316 |
| research_evidence_archive | 0 |
| research_gates | 26140 |
| research_run_snapshots | 4357 |
| research_runs | 4357 |
| research_worker_heartbeat | 1 |
| research_worker_state | 1 |
| runtime_risk_state | 1 |
| schema_meta | 1 |
| schema_migrations | 8 |
| settings_audit | 0 |
| shadow70_drift_alerts | 0 |
| shadow70_events | 0 |
| shadow70_feature_health | 0 |
| shadow70_observations | 2 |
| shadow_comparisons | 61 |
| shadow_decisions | 55342 |
| shadow_promotions | 0 |
| shadow_runs | 63 |
| strategy_evolution_candidates | 1 |
| strategy_intelligence_registry | 373 |
| strategy_registry | 4109 |
| trade_autopsies | 395 |
| trading_rules_config | 30 |
| training_runs | 6 |

