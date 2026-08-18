"""
Schema Manifests — expected schema contracts per domain (TASK-10 §13)
=====================================================================
Machine-readable expected schema for AUDIT / NEWS / CANDLE_INTEL.

The ACTUAL database is inspected at runtime; drift = expected minus actual
(minus known pending migrations). These manifests are the canonical
"expected schema" for `nexus db verify`, drift detection and the startup gate.

Current baseline version per domain (2026-08-18):
    audit       = 1  (all 22 tables as created by AuditRepository bootstrap)
    news        = 1  (13 tables)
    candle_intel= 1  (12 tables)

New schema changes MUST go through registry.py migrations (bump version,
add migration) — never by editing this manifest alone.
"""

from __future__ import annotations

from nexus_scalp.database.models import (
    DatabaseDomain,
    SchemaColumn,
    SchemaManifest,
    SchemaTable,
)

# ---------------------------------------------------------------------------
# AUDIT domain (audit.db)
# ---------------------------------------------------------------------------

AUDIT_SCHEMA_VERSION: int = 1

AUDIT_TABLES: tuple[SchemaTable, ...] = (
    SchemaTable(
        name="trading_rules_config",
        columns=(SchemaColumn("rule_id", "TEXT", nullable=False),),
    ),
    SchemaTable(
        name="audit_signals",
        unique_indexes=("idx_audit_signals_dedup",),
    ),
    SchemaTable(
        name="audit_guard_telemetry",
        indexes=("idx_guard_telemetry_window",),
    ),
    SchemaTable(
        name="audit_orders",
        indexes=("idx_orders_ticket",),
    ),
    SchemaTable(
        name="audit_ledger",
        columns=(
            SchemaColumn("ticket", "INTEGER", nullable=False),
            SchemaColumn("symbol", "TEXT"),
            SchemaColumn("direction", "TEXT"),
            SchemaColumn("volume", "REAL"),
            SchemaColumn("entry_price", "REAL"),
            SchemaColumn("exit_price", "REAL"),
            SchemaColumn("status", "TEXT"),
            SchemaColumn("pnl", "REAL"),
            SchemaColumn("commission", "REAL"),
            SchemaColumn("swap", "REAL"),
            SchemaColumn("duration_sec", "REAL"),
            SchemaColumn("timestamp", "TEXT"),
            SchemaColumn("mae", "REAL"),
            SchemaColumn("mfe", "REAL"),
            SchemaColumn("initial_sl_price", "REAL"),
            SchemaColumn("final_sl_price", "REAL"),
            SchemaColumn("is_risk_free_hit", "INTEGER"),
            SchemaColumn("exit_mechanism", "TEXT"),
            SchemaColumn("order_id", "TEXT"),
            SchemaColumn("open_time", "TEXT"),
            SchemaColumn("close_time", "TEXT"),
            SchemaColumn("duration_seconds", "REAL"),
            SchemaColumn("open_price", "REAL"),
            SchemaColumn("close_price", "REAL"),
            SchemaColumn("gross_pnl_usd", "REAL"),
            SchemaColumn("net_pnl_usd", "REAL"),
            SchemaColumn("entry_reason", "TEXT"),
            SchemaColumn("ai_confidence_at_open", "REAL"),
            SchemaColumn("market_regime_at_open", "TEXT"),
            SchemaColumn("was_sl_modified", "INTEGER"),
            SchemaColumn("MAE_usd", "REAL"),
            SchemaColumn("MFE_usd", "REAL"),
            SchemaColumn("account_balance_after", "REAL"),
            SchemaColumn("account_equity_after", "REAL"),
            SchemaColumn("drawdown_percent_after", "REAL"),
            SchemaColumn("entry_setup_snapshot", "TEXT"),
            SchemaColumn("exit_reason_source", "TEXT"),
            SchemaColumn("exit_evidence", "TEXT"),
            SchemaColumn("exit_reason_confidence", "REAL"),
            SchemaColumn("reversal_events_json", "TEXT"),
        ),
        full_contract=True,
    ),
    SchemaTable(name="audit_executions"),
    SchemaTable(name="audit_account_snapshots"),
    SchemaTable(
        name="audit_experiences",
        indexes=(
            "idx_exp_strategy_time",
            "idx_exp_symbol_time",
            "idx_exp_request",
            "idx_exp_schema",
        ),
    ),
    SchemaTable(
        name="audit_experience_outcomes",
        indexes=("idx_exp_outcome_key",),
    ),
    SchemaTable(name="audit_experience_corrections", indexes=("idx_exp_corrections_key",)),
    SchemaTable(
        name="strategy_intelligence_registry",
    ),
    SchemaTable(
        name="experience_model_registry",
    ),
    SchemaTable(
        name="position_lifecycle_events",
        indexes=("idx_lifecycle_ticket", "idx_lifecycle_type"),
    ),
    SchemaTable(
        name="trade_autopsies",
        indexes=("idx_autopsy_strategy",),
    ),
    SchemaTable(
        name="behavior_detections",
        indexes=("idx_behavior_ticket", "idx_behavior_pattern"),
    ),
    SchemaTable(
        name="behavior_analysis",
        indexes=("idx_behavior_analysis_ticket", "idx_behavior_analysis_version"),
    ),
    SchemaTable(
        name="anomaly_events",
        indexes=("idx_anomaly_events_ticket", "idx_anomaly_events_type"),
    ),
    SchemaTable(
        name="strategy_evolution_candidates",
        indexes=("idx_evolution_status",),
    ),
    SchemaTable(name="intelligence_worker_state"),
    SchemaTable(
        name="strategy_registry",
        indexes=("idx_registry_id", "idx_registry_lifecycle"),
    ),
    SchemaTable(
        name="research_runs",
        indexes=("idx_research_runs_strategy",),
    ),
    SchemaTable(name="research_worker_state"),
    SchemaTable(name="schema_migrations"),
    SchemaTable(name="schema_meta"),
)

AUDIT_MANIFEST = SchemaManifest(
    database=DatabaseDomain.AUDIT,
    schema_version=AUDIT_SCHEMA_VERSION,
    tables=AUDIT_TABLES,
)

# ---------------------------------------------------------------------------
# NEWS domain (news.db)
# ---------------------------------------------------------------------------

NEWS_SCHEMA_VERSION: int = 1

NEWS_TABLES: tuple[SchemaTable, ...] = (
    SchemaTable(name="news_sources"),
    SchemaTable(
        name="news_articles",
        indexes=(
            "idx_news_articles_published",
            "idx_news_articles_source",
            "idx_news_articles_dup",
        ),
    ),
    SchemaTable(
        name="news_article_versions",
        indexes=("idx_news_versions_article",),
    ),
    SchemaTable(name="news_entities"),
    SchemaTable(name="news_topics"),
    SchemaTable(
        name="news_analysis",
        indexes=("idx_news_analysis_article",),
    ),
    SchemaTable(
        name="news_impacts",
        indexes=("idx_news_impacts_asset",),
    ),
    SchemaTable(name="news_consensus"),
    SchemaTable(name="news_analysis_runs"),
    SchemaTable(name="news_worker_state"),
    SchemaTable(name="news_event_links"),
    SchemaTable(
        name="news_trade_links",
        indexes=("idx_news_trade_links_trade", "idx_news_trade_links_article"),
    ),
    SchemaTable(
        name="news_health",
        indexes=("idx_news_health_source",),
    ),
    SchemaTable(name="schema_migrations"),
    SchemaTable(name="schema_meta"),
)

NEWS_MANIFEST = SchemaManifest(
    database=DatabaseDomain.NEWS,
    schema_version=NEWS_SCHEMA_VERSION,
    tables=NEWS_TABLES,
)

# ---------------------------------------------------------------------------
# CANDLE_INTEL domain (candle_intel.db)
# ---------------------------------------------------------------------------

CANDLE_SCHEMA_VERSION: int = 1

CANDLE_TABLES: tuple[SchemaTable, ...] = (
    SchemaTable(name="candles"),
    SchemaTable(name="candle_closures"),
    SchemaTable(name="candle_patterns"),
    SchemaTable(name="market_regimes"),
    SchemaTable(name="feature_vectors"),
    SchemaTable(name="trade_proposals"),
    SchemaTable(name="trade_decisions"),
    SchemaTable(name="open_positions"),
    SchemaTable(name="exit_signals"),
    SchemaTable(name="risk_evaluations"),
    SchemaTable(name="rule_vetoes"),
    SchemaTable(name="audit_log"),
    SchemaTable(name="schema_migrations"),
    SchemaTable(name="schema_meta"),
)

CANDLE_MANIFEST = SchemaManifest(
    database=DatabaseDomain.CANDLE_INTEL,
    schema_version=CANDLE_SCHEMA_VERSION,
    tables=CANDLE_TABLES,
)

#: Registry of all domain manifests.
MANIFESTS: dict[DatabaseDomain, SchemaManifest] = {
    DatabaseDomain.AUDIT: AUDIT_MANIFEST,
    DatabaseDomain.NEWS: NEWS_MANIFEST,
    DatabaseDomain.CANDLE_INTEL: CANDLE_MANIFEST,
}


def manifest_for(domain: DatabaseDomain) -> SchemaManifest:
    return MANIFESTS[domain]


def expected_version_for(domain: DatabaseDomain) -> int:
    return MANIFESTS[domain].schema_version
