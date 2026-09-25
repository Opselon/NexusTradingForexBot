# DATABASE HYGIENE MATRIX — NSE (2026-08-18, TASK-11 inventory)

> Source of truth: live schema inspection of `artifacts/audit.db`, `artifacts/news.db`,
> `artifacts/candle_intel.db` on 2026-08-18. Row counts are snapshots (engine live).

## Data tiers (spec §3)

| Tier | Meaning | Auto-delete |
| :--- | :--- | :--- |
| TIER-0 | Immutable financial / broker truth | NEVER |
| TIER-1 | Canonical audit / experience | NEVER automatically |
| TIER-2 | Research / learning evidence | NEVER automatically (archive first) |
| TIER-3 | Strategy / model metadata | Never without migration/archive safety |
| TIER-4 | News intelligence (canonical evidence) | Never; derived/duplicates only |
| TIER-5 | Derived analytics | Rebuildable; bounded retention |
| TIER-6 | Cache | TTL-based cleanup allowed |
| TIER-7 | Temporary / job state | Short retention allowed |
| TIER-8 | Legacy / migration artifact | Only after TASK-10 migration verified |

## audit.db (35 tables, 50.9 MB, owner: AuditRepository)

| Table | Rows | Tier | Purpose / source of truth | Retention | Cleanup rule | Rebuildable | Deletable |
| :--- | ---: | :--- | :--- | :--- | :--- | :--- | :--- |
| audit_ledger | 266 | TIER-1 | Canonical trade ledger (opened+closed autopsy rows) | never_delete | none | NO | NO |
| audit_experiences | 229 | TIER-1 | Immutable decision snapshots (schema-versioned) | never_delete | none | NO | NO |
| audit_experience_outcomes | 74 | TIER-1 | Append-only outcome events | never_delete | none (DB UNIQUE dedup) | NO | NO |
| audit_experience_corrections | 0 | TIER-1 | Additive correction log | never_delete | none | NO | NO |
| audit_broker_deals | 7516 | TIER-0 | Broker deal truth (entry/exit) | never_delete | none | NO | NO |
| audit_broker_orders | 9634 | TIER-0 | Broker order history | never_delete | none | NO | NO |
| audit_broker_trades | 3635 | TIER-0 | Broker canonical trade reconstruction | never_delete | none | NO | NO |
| audit_broker_history_meta | 1 | TIER-1 | Sync metadata | never_delete | none | rebuildable on next sync | NO (canonical meta) |
| audit_account_snapshots | 997 | TIER-1 | Balance/equity history (accounting invariant) | never_delete | none | NO | NO |
| audit_executions | 261 | TIER-1 | Order dispatch records | never_delete | none | NO | NO |
| audit_orders | 7461 | TIER-1 | Order lifecycle rows | never_delete | none | NO | NO |
| audit_signals | 15142 | TIER-5 | Signal/rejection census (model funnel) | 7 days (existing purge) | bounded purge (BUG-054) | YES (lossy) | ONLY via existing purge |
| audit_guard_telemetry | 562 | TIER-7 | Guard counters | 13 days (existing purge) | bounded purge (BUG-054) | YES | ONLY via existing purge |
| position_lifecycle_events | 11875 | TIER-1* | Immutable position timeline (*POSITION_MOVING subset is telemetry) | MOVING: 3d; others never | purge MOVING only | NO (non-MOVING) | MOVING only |
| behavior_analysis | 264 | TIER-2 | Behavioral flags | never_delete | none | NO | NO |
| behavior_detections | 225 | TIER-2 | Detection records | never_delete | none | NO | NO |
| anomaly_events | 22 | TIER-2 | Anomaly evidence | never_delete | none | NO | NO |
| trade_autopsies | 73 | TIER-2 | Forensic narratives | never_delete | none | rebuildable from ledger | NO |
| strategy_intelligence_registry | 109 | TIER-3 | Strategy score evidence | never_delete | none | YES (derived) | NO (keep derived evidence) |
| strategy_registry | 2 | TIER-3 | Strategy manifest | never_delete | none | NO | NO |
| strategy_evolution_candidates | 0 | TIER-2 | Candidate evidence | never_delete | none | NO | NO |
| experience_model_registry | 2 | TIER-3 | Model provenance metadata | never_delete | none | NO | NO |
| model_governance_events | 0 | TIER-3 | Governance events | never_delete | none | NO | NO |
| model_governance_state | 0 | TIER-3 | Governance state | never_delete | none | NO | NO |
| model_runtime_health | 3 | TIER-3 | Health snapshots | never_delete | none | rebuildable | NO |
| model_shadow_comparisons | 0 | TIER-2 | Shadow eval | never_delete | none | NO | NO |
| model_comparisons | 0 | TIER-2 | Comparison evidence | never_delete | none | NO | NO |
| training_runs | 0 | TIER-3 | Training metadata | never_delete | none | NO | NO |
| research_runs | 0 | TIER-2 | Research runs | never_delete | none | NO | NO |
| research_worker_state | 0 | TIER-7 | Worker checkpoint | 30d stale | stale-only | YES | stale only |
| intelligence_worker_state | 0 | TIER-7 | Worker checkpoint | 30d stale | stale-only | YES | stale only |
| trading_rules_config | 30 | TIER-3 | Rule config (active) | never_delete | none | NO | NO |
| sqlite_sequence | 13 | TIER-7 | SQLite autoincrement bookkeeping | never_delete | none | NO | NO |

## news.db (16 tables, 6.4 MB, owner: NewsDatabase)

| Table | Rows | Tier | Purpose / source of truth | Retention | Cleanup rule | Rebuildable | Deletable |
| :--- | ---: | :--- | :--- | :--- | :--- | :--- | :--- |
| news_articles | 1677 | TIER-4 | Canonical article evidence (article_hash UNIQUE) | long (configurable) | dedup only (is_duplicate flag exists) | NO | duplicates-of-proven rows only |
| news_analysis | 877 | TIER-4 | Per-article analysis evidence | never_delete | none | rebuildable via provider but keep | NO |
| news_analysis_runs | 877 | TIER-4 | Run manifests | never_delete | none | NO | NO |
| news_entities | 2056 | TIER-4 | Entity extraction evidence | never_delete | none | NO | NO |
| news_impacts | 671 | TIER-4 | Impact evidence (trade/news link) | never_delete | none | NO | NO |
| news_topics | 1663 | TIER-4 | Topic tags | never_delete | none | NO | NO |
| news_sources | 11 | TIER-3 | Source config | never_delete | none | NO | NO |
| news_article_versions | 0 | TIER-4 | Version history | never_delete | none | NO | NO |
| news_consensus | 0 | TIER-4 | Consensus evidence | never_delete | none | NO | NO |
| news_event_links | 0 | TIER-4 | Event links | never_delete | none | NO | NO |
| news_trade_links | 0 | TIER-4 | Trade-context links | never_delete | none | NO | NO |
| news_post_event | 0 | TIER-4 | Post-event state | never_delete | none | NO | NO |
| news_health | 10 | TIER-5 | Health snapshots | 90d | bounded | YES | 90d+ |
| news_worker_state | 1 | TIER-7 | Worker checkpoint | 30d stale | stale-only | YES | stale only |

## candle_intel.db (13 tables, 1.0 MB + 4.2 MB WAL, owner: CandleIntelStore)

| Table | Rows | Tier | Purpose / source of truth | Retention | Cleanup rule | Rebuildable | Deletable |
| :--- | ---: | :--- | :--- | :--- | :--- | :--- | :--- |
| candles | 339 | TIER-5 | Derived candle snapshots (rebuildable from broker history) | 30d | bounded purge | YES | yes (rebuildable) |
| candle_closures | 339 | TIER-5 | Derived closure classifications | 30d | bounded purge | YES | yes (rebuildable) |
| candle_patterns | 1054 | TIER-5 | Derived pattern detections | 30d | bounded purge | YES | yes (rebuildable) |
| market_regimes | 393 | TIER-5 | Derived regime history | 30d | bounded purge | YES | yes (rebuildable) |
| risk_evaluations | 393 | TIER-5 | Derived risk eval | 30d | bounded purge | YES | yes (rebuildable) |
| trade_decisions | 393 | TIER-5 | Derived decision mirror (non-authoritative; audit.db is truth) | 30d | bounded purge | YES | yes (rebuildable) |
| rule_vetoes | 113 | TIER-5 | Derived veto log | 30d | bounded purge | YES | yes (rebuildable) |
| feature_vectors | 0 | TIER-6 | Cache | TTL | cache rule | YES | yes |
| trade_proposals | 0 | TIER-6 | Cache | TTL | cache rule | YES | yes |
| open_positions | 0 | TIER-7 | Active-state mirror | short | stale-only | YES | stale only |
| exit_signals | 0 | TIER-7 | Active-state mirror | short | stale-only | YES | stale only |
| audit_log | 0 | TIER-1 | Local audit log | never_delete | none | NO | NO |

## Source-of-truth rules (spec §5)

- Broker truth (audit_broker_*) and audit_ledger are NEVER deleted (TIER-0/1).
- candle_intel trade_decisions/market_regimes are DERIVED mirrors — audit.db
  is the authority; the candle store rows are rebuildable, NOT duplicates.
- news_articles dedup is by `article_hash` UNIQUE — `is_duplicate` /
  `duplicate_of` columns are the canonical marker; cleanup only targets rows
  flagged duplicate with a live canonical row present (confidence 1.0).
- research/strategy/model tables are evidence — archive before any removal.
- Migration metadata / schema history: NEVER junk (spec §28). TASK-10 owns
  the migration engine; the hygiene worker only CLASSIFIES legacy artifacts.

## Junk definition (spec §6, approved classes)

JUNK (only these may ever be auto-deleted, each with confidence 1.0):
1. audit_signals older than retention (existing BUG-054 purge).
2. position_lifecycle_events POSITION_MOVING older than retention (existing).
3. audit_guard_telemetry older than retention (existing).
4. candle_intel derived rows older than retention (NEW, bounded purge).
5. news_health rows older than 90 days (NEW, bounded purge).
6. stale worker-state rows (no active worker identity, 30d old) (NEW).
7. news_articles flagged `is_duplicate=1` with a verified canonical row
   present (article_hash match on duplicate_of) (NEW, confidence 1.0 only).
8. temp files under NSE-owned paths older than TTL, not locked (NEW).

NOT junk (never auto-delete): losing trades, old trades, BE/zero-PnL trades,
strategy candidates, rejected strategies, model failures, old news, research
runs, behavioral anomalies, failed executions, broker reconciliation records,
migration history, model manifests.