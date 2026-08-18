"""
Retention Policy Engine (TASK-11)
=================================
Policy-driven retention rules per database table. Every table may declare:

    minimum_retention   how long a row is guaranteed kept
    maximum_retention   after which the row is a candidate (still NOT
                        auto-deleted unless it is a proven-safe class)
    archive_after       age at which the row should be archived first
    delete_after        age at which a proven-safe row may be deleted
    never_delete        hard guard (TIER-0/1/2/3/4)

The engine NEVER decides deletion alone: cleanup requires an approved
class (duplicate with canonical row, stale temporary state, expired cache,
rebuildable derived) AND confidence 1.0 AND a journal record.

Default: UNKNOWN retention → KEEP (spec §73: when not 100% certain, keep).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from nexus_scalp.hygiene import DataTier


@dataclass(frozen=True)
class RetentionRule:
    """One table's retention policy. All ages in days; 0/None = not applicable."""

    database: str
    table: str
    tier: DataTier
    purpose: str = ""
    source_of_truth: bool = False
    minimum_retention_days: float | None = None
    maximum_retention_days: float | None = None
    archive_after_days: float | None = None
    delete_after_days: float | None = None
    never_delete: bool = False
    cleanup_class: str = (
        "KEEP"  # KEEP | DUPLICATE_WITH_CANONICAL | STALE_TEMP | EXPIRED_CACHE | REBUILDABLE_DERIVED
    )
    owner: str = ""
    rebuildable: bool = False

    def is_age_candidate(self, row_age_days: float) -> bool:
        """True when the row is old enough to be considered by a cleanup class."""
        if self.never_delete:
            return False
        threshold = self.delete_after_days
        if threshold is None:
            threshold = self.maximum_retention_days
        if threshold is None:
            return False
        return row_age_days >= float(threshold)

    def is_archive_candidate(self, row_age_days: float) -> bool:
        if self.never_delete:
            return False
        if self.archive_after_days is None:
            return False
        return row_age_days >= float(self.archive_after_days)


# ---------------------------------------------------------------------------
# Canonical retention registry (2026-08-18, from DATABASE_HYGIENE_MATRIX.md)
# ---------------------------------------------------------------------------
# Values are evidence-based: the existing BUG-054 purge already uses
# signal=7d / moving=3d / guard-telemetry=13d; everything else defaults to
# never_delete == KEEP unless it is proven rebuildable derived data.

AUDIT_RETENTION: dict[str, RetentionRule] = {
    "audit_ledger": RetentionRule(
        database="audit",
        table="audit_ledger",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="canonical trade ledger (opened + closed autopsy rows)",
        source_of_truth=True,
        never_delete=True,
        owner="AuditRepository/OrderManager",
    ),
    "audit_experiences": RetentionRule(
        database="audit",
        table="audit_experiences",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="immutable decision snapshots",
        source_of_truth=True,
        never_delete=True,
        owner="ExperienceLedger",
    ),
    "audit_experience_outcomes": RetentionRule(
        database="audit",
        table="audit_experience_outcomes",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="append-only outcome events",
        source_of_truth=True,
        never_delete=True,
        owner="ExperienceLedger",
    ),
    "audit_broker_deals": RetentionRule(
        database="audit",
        table="audit_broker_deals",
        tier=DataTier.TIER_0_BROKER_TRUTH,
        purpose="broker deal truth",
        source_of_truth=True,
        never_delete=True,
        owner="MT5 adapter / broker_history_sync",
    ),
    "audit_broker_orders": RetentionRule(
        database="audit",
        table="audit_broker_orders",
        tier=DataTier.TIER_0_BROKER_TRUTH,
        purpose="broker order history",
        source_of_truth=True,
        never_delete=True,
        owner="MT5 adapter / broker_history_sync",
    ),
    "audit_broker_trades": RetentionRule(
        database="audit",
        table="audit_broker_trades",
        tier=DataTier.TIER_0_BROKER_TRUTH,
        purpose="broker canonical trade reconstruction",
        source_of_truth=True,
        never_delete=True,
        owner="MT5 adapter / broker_history_sync",
    ),
    "audit_account_snapshots": RetentionRule(
        database="audit",
        table="audit_account_snapshots",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="balance/equity history (accounting invariant)",
        source_of_truth=True,
        never_delete=True,
        owner="AuditRepository",
    ),
    "audit_orders": RetentionRule(
        database="audit",
        table="audit_orders",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="order lifecycle rows",
        source_of_truth=True,
        never_delete=True,
        owner="AuditRepository",
    ),
    "audit_executions": RetentionRule(
        database="audit",
        table="audit_executions",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="order dispatch records",
        source_of_truth=True,
        never_delete=True,
        owner="AuditRepository",
    ),
    "audit_signals": RetentionRule(
        database="audit",
        table="audit_signals",
        tier=DataTier.TIER_5_DERIVED_ANALYTICS,
        purpose="signal/rejection census (model funnel)",
        minimum_retention_days=7.0,
        delete_after_days=7.0,
        cleanup_class="KEEP",
        owner="AuditRepository",
        rebuildable=True,
    ),
    "audit_guard_telemetry": RetentionRule(
        database="audit",
        table="audit_guard_telemetry",
        tier=DataTier.TIER_7_TEMPORARY_STATE,
        purpose="guard telemetry counters",
        minimum_retention_days=13.0,
        delete_after_days=13.0,
        cleanup_class="KEEP",
        owner="AuditRepository",
        rebuildable=True,
    ),
    "position_lifecycle_events": RetentionRule(
        database="audit",
        table="position_lifecycle_events",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="immutable position timeline (MOVING subset is telemetry)",
        minimum_retention_days=3.0,
        delete_after_days=3.0,
        cleanup_class="KEEP",
        owner="PositionLifecycleTracker",
    ),
    "behavior_detections": RetentionRule(
        database="audit",
        table="behavior_detections",
        tier=DataTier.TIER_2_RESEARCH_EVIDENCE,
        purpose="behavioral detection evidence",
        never_delete=True,
        owner="BehaviorDetectionEngine",
    ),
    "behavior_analysis": RetentionRule(
        database="audit",
        table="behavior_analysis",
        tier=DataTier.TIER_2_RESEARCH_EVIDENCE,
        purpose="behavioral flags",
        never_delete=True,
        owner="BehaviorDetectionEngine",
    ),
    "anomaly_events": RetentionRule(
        database="audit",
        table="anomaly_events",
        tier=DataTier.TIER_2_RESEARCH_EVIDENCE,
        purpose="anomaly evidence",
        never_delete=True,
        owner="BehaviorDetectionEngine",
    ),
    "trade_autopsies": RetentionRule(
        database="audit",
        table="trade_autopsies",
        tier=DataTier.TIER_2_RESEARCH_EVIDENCE,
        purpose="forensic narratives",
        never_delete=True,
        owner="TradeAutopsyEngine",
        rebuildable=True,
    ),
    "strategy_intelligence_registry": RetentionRule(
        database="audit",
        table="strategy_intelligence_registry",
        tier=DataTier.TIER_3_MODEL_METADATA,
        purpose="strategy score evidence",
        never_delete=True,
        owner="ExperienceEvaluator",
    ),
    "strategy_registry": RetentionRule(
        database="audit",
        table="strategy_registry",
        tier=DataTier.TIER_3_MODEL_METADATA,
        purpose="strategy manifest",
        never_delete=True,
        owner="StrategyRegistry",
    ),
    "strategy_evolution_candidates": RetentionRule(
        database="audit",
        table="strategy_evolution_candidates",
        tier=DataTier.TIER_2_RESEARCH_EVIDENCE,
        purpose="candidate evidence",
        never_delete=True,
        owner="StrategyEvolutionEngine",
    ),
    "experience_model_registry": RetentionRule(
        database="audit",
        table="experience_model_registry",
        tier=DataTier.TIER_3_MODEL_METADATA,
        purpose="model provenance metadata",
        never_delete=True,
        owner="ModelRegistry",
    ),
    "model_governance_events": RetentionRule(
        database="audit",
        table="model_governance_events",
        tier=DataTier.TIER_3_MODEL_METADATA,
        purpose="governance events",
        never_delete=True,
        owner="ModelGovernanceEngine",
    ),
    "model_governance_state": RetentionRule(
        database="audit",
        table="model_governance_state",
        tier=DataTier.TIER_3_MODEL_METADATA,
        purpose="governance state",
        never_delete=True,
        owner="ModelGovernanceEngine",
    ),
    "model_runtime_health": RetentionRule(
        database="audit",
        table="model_runtime_health",
        tier=DataTier.TIER_3_MODEL_METADATA,
        purpose="health snapshots",
        never_delete=True,
        owner="ModelGovernanceEngine",
    ),
    "model_shadow_comparisons": RetentionRule(
        database="audit",
        table="model_shadow_comparisons",
        tier=DataTier.TIER_2_RESEARCH_EVIDENCE,
        purpose="shadow evaluation evidence",
        never_delete=True,
        owner="GovernanceShadowRuntime",
    ),
    "model_comparisons": RetentionRule(
        database="audit",
        table="model_comparisons",
        tier=DataTier.TIER_2_RESEARCH_EVIDENCE,
        purpose="comparison evidence",
        never_delete=True,
        owner="ModelLifecycle",
    ),
    "training_runs": RetentionRule(
        database="audit",
        table="training_runs",
        tier=DataTier.TIER_3_MODEL_METADATA,
        purpose="training metadata",
        never_delete=True,
        owner="TrainingWorker",
    ),
    "research_runs": RetentionRule(
        database="audit",
        table="research_runs",
        tier=DataTier.TIER_2_RESEARCH_EVIDENCE,
        purpose="research runs (evidence even when failed)",
        never_delete=True,
        owner="ResearchWorker",
    ),
    "research_worker_state": RetentionRule(
        database="audit",
        table="research_worker_state",
        tier=DataTier.TIER_7_TEMPORARY_STATE,
        purpose="worker checkpoint",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="STALE_TEMP",
        owner="ResearchWorker",
        rebuildable=True,
    ),
    "intelligence_worker_state": RetentionRule(
        database="audit",
        table="intelligence_worker_state",
        tier=DataTier.TIER_7_TEMPORARY_STATE,
        purpose="worker checkpoint",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="STALE_TEMP",
        owner="IntelligenceWorker",
        rebuildable=True,
    ),
    "trading_rules_config": RetentionRule(
        database="audit",
        table="trading_rules_config",
        tier=DataTier.TIER_3_MODEL_METADATA,
        purpose="rule config",
        never_delete=True,
        owner="RuleMatrixEngine",
    ),
    "audit_broker_history_meta": RetentionRule(
        database="audit",
        table="audit_broker_history_meta",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="sync metadata",
        never_delete=True,
        owner="broker_history_sync",
    ),
    "audit_experience_corrections": RetentionRule(
        database="audit",
        table="audit_experience_corrections",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="additive correction log",
        never_delete=True,
        owner="ExperienceLedger",
    ),
}

NEWS_RETENTION: dict[str, RetentionRule] = {
    "news_articles": RetentionRule(
        database="news",
        table="news_articles",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="canonical article evidence (article_hash UNIQUE)",
        source_of_truth=True,
        never_delete=True,
        cleanup_class="DUPLICATE_WITH_CANONICAL",
        owner="NewsDatabase",
    ),
    "news_analysis": RetentionRule(
        database="news",
        table="news_analysis",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="per-article analysis evidence",
        never_delete=True,
        owner="NewsAnalysis",
    ),
    "news_analysis_runs": RetentionRule(
        database="news",
        table="news_analysis_runs",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="run manifests",
        never_delete=True,
        owner="NewsAnalysis",
    ),
    "news_entities": RetentionRule(
        database="news",
        table="news_entities",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="entity extraction evidence",
        never_delete=True,
        owner="NewsDatabase",
    ),
    "news_impacts": RetentionRule(
        database="news",
        table="news_impacts",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="impact evidence (trade/news link)",
        never_delete=True,
        owner="NewsGate/ImpactEngine",
    ),
    "news_topics": RetentionRule(
        database="news",
        table="news_topics",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="topic tags",
        never_delete=True,
        owner="NewsDatabase",
    ),
    "news_sources": RetentionRule(
        database="news",
        table="news_sources",
        tier=DataTier.TIER_3_MODEL_METADATA,
        purpose="source config",
        never_delete=True,
        owner="NewsIngest",
    ),
    "news_article_versions": RetentionRule(
        database="news",
        table="news_article_versions",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="version history",
        never_delete=True,
        owner="NewsDatabase",
    ),
    "news_consensus": RetentionRule(
        database="news",
        table="news_consensus",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="consensus evidence",
        never_delete=True,
        owner="NewsAnalysis",
    ),
    "news_event_links": RetentionRule(
        database="news",
        table="news_event_links",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="event links",
        never_delete=True,
        owner="NewsAnalysis",
    ),
    "news_trade_links": RetentionRule(
        database="news",
        table="news_trade_links",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="trade-context links",
        never_delete=True,
        owner="NewsGate",
    ),
    "news_post_event": RetentionRule(
        database="news",
        table="news_post_event",
        tier=DataTier.TIER_4_NEWS_INTELLIGENCE,
        purpose="post-event state",
        never_delete=True,
        owner="NewsAnalysis",
    ),
    "news_health": RetentionRule(
        database="news",
        table="news_health",
        tier=DataTier.TIER_5_DERIVED_ANALYTICS,
        purpose="health snapshots",
        minimum_retention_days=90.0,
        delete_after_days=90.0,
        cleanup_class="KEEP",
        owner="NewsWorker",
        rebuildable=True,
    ),
    "news_worker_state": RetentionRule(
        database="news",
        table="news_worker_state",
        tier=DataTier.TIER_7_TEMPORARY_STATE,
        purpose="worker checkpoint",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="STALE_TEMP",
        owner="NewsWorker",
        rebuildable=True,
    ),
}

CANDLE_RETENTION: dict[str, RetentionRule] = {
    "candles": RetentionRule(
        database="candle_intel",
        table="candles",
        tier=DataTier.TIER_5_DERIVED_ANALYTICS,
        purpose="derived candle snapshots (rebuildable from broker history)",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="KEEP",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "candle_closures": RetentionRule(
        database="candle_intel",
        table="candle_closures",
        tier=DataTier.TIER_5_DERIVED_ANALYTICS,
        purpose="derived closure classifications",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="KEEP",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "candle_patterns": RetentionRule(
        database="candle_intel",
        table="candle_patterns",
        tier=DataTier.TIER_5_DERIVED_ANALYTICS,
        purpose="derived pattern detections",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="KEEP",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "market_regimes": RetentionRule(
        database="candle_intel",
        table="market_regimes",
        tier=DataTier.TIER_5_DERIVED_ANALYTICS,
        purpose="derived regime history",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="KEEP",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "risk_evaluations": RetentionRule(
        database="candle_intel",
        table="risk_evaluations",
        tier=DataTier.TIER_5_DERIVED_ANALYTICS,
        purpose="derived risk evaluations",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="KEEP",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "trade_decisions": RetentionRule(
        database="candle_intel",
        table="trade_decisions",
        tier=DataTier.TIER_5_DERIVED_ANALYTICS,
        purpose="derived decision mirror (audit.db is authority)",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="KEEP",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "rule_vetoes": RetentionRule(
        database="candle_intel",
        table="rule_vetoes",
        tier=DataTier.TIER_5_DERIVED_ANALYTICS,
        purpose="derived veto log",
        minimum_retention_days=30.0,
        delete_after_days=30.0,
        cleanup_class="KEEP",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "feature_vectors": RetentionRule(
        database="candle_intel",
        table="feature_vectors",
        tier=DataTier.TIER_6_CACHE,
        purpose="cache",
        minimum_retention_days=7.0,
        delete_after_days=7.0,
        cleanup_class="EXPIRED_CACHE",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "trade_proposals": RetentionRule(
        database="candle_intel",
        table="trade_proposals",
        tier=DataTier.TIER_6_CACHE,
        purpose="cache",
        minimum_retention_days=7.0,
        delete_after_days=7.0,
        cleanup_class="EXPIRED_CACHE",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "open_positions": RetentionRule(
        database="candle_intel",
        table="open_positions",
        tier=DataTier.TIER_7_TEMPORARY_STATE,
        purpose="active-state mirror",
        minimum_retention_days=1.0,
        delete_after_days=1.0,
        cleanup_class="STALE_TEMP",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "exit_signals": RetentionRule(
        database="candle_intel",
        table="exit_signals",
        tier=DataTier.TIER_7_TEMPORARY_STATE,
        purpose="active-state mirror",
        minimum_retention_days=1.0,
        delete_after_days=1.0,
        cleanup_class="STALE_TEMP",
        owner="CandleIntelStore",
        rebuildable=True,
    ),
    "audit_log": RetentionRule(
        database="candle_intel",
        table="audit_log",
        tier=DataTier.TIER_1_CANONICAL_AUDIT,
        purpose="local audit log",
        never_delete=True,
        owner="CandleIntelStore",
    ),
}


class RetentionEngine:
    """Policy lookup + age evaluation. Never deletes by itself."""

    def __init__(self, rules: dict[str, RetentionRule] | None = None) -> None:
        self._rules: dict[str, RetentionRule] = {}
        if rules:
            for table, rule in rules.items():
                self._rules[table] = rule

    @classmethod
    def for_database(cls, db_key: str) -> RetentionEngine:
        if db_key == "audit":
            return cls(dict(AUDIT_RETENTION))
        if db_key == "news":
            return cls(dict(NEWS_RETENTION))
        if db_key == "candle_intel":
            return cls(dict(CANDLE_RETENTION))
        return cls()

    def rule_for(self, table: str) -> RetentionRule | None:
        return self._rules.get(table)

    def age_days(self, row_ts: str | None, now: datetime | None = None) -> float | None:
        """Converts an ISO/epoch row timestamp to age in days. None when unknown."""
        if not row_ts:
            return None
        now = now or datetime.now(UTC)
        try:
            raw = row_ts
            if isinstance(raw, (int, float)):
                dt = datetime.fromtimestamp(float(raw), tz=UTC)
            else:
                text = str(raw).strip()
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                if len(text) == 10:  # bare date
                    dt = datetime.fromisoformat(text + "T00:00:00+00:00")
                else:
                    dt = datetime.fromisoformat(text)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
            return max(0.0, (now - dt).total_seconds() / 86400.0)
        except Exception:
            return None

    def classify(self, table: str, row_age_days: float | None) -> str:
        """Returns the retention verdict for one row: KEEP / CANDIDATE / ARCHIVE."""
        rule = self.rule_for(table)
        if rule is None:
            return "KEEP"  # unknown table -> keep (spec §73)
        if rule.never_delete:
            return "KEEP"
        if row_age_days is None:
            return "KEEP"
        if rule.is_archive_candidate(row_age_days):
            return "ARCHIVE"
        if rule.is_age_candidate(row_age_days):
            return "CANDIDATE"
        return "KEEP"
