"""
Performance Intelligence Reporting (Telegram Daily Intelligence)
=================================================================
Deterministic multi-stage report generator built ON TOP of the canonical
accounting core. It consumes `AccountingCore` / `PeriodReport` / `TradeRecord`
as a READ-ONLY consumer — it NEVER writes financial truth, never opens trades,
never modifies risk/model/news gates.

Architecture (mandated by the task spec):

    MT5 / canonical ledger
            │
    AccountingCore  (canonical, unchanged)
            │
    PerformanceReportEngine  (this package — read-only enrichment)
            │
    Structured JSON report contract  +  Telegram formatter

Stages implemented (each deterministic and testable):

    SNAPSHOT -> OUTCOMES -> PROFIT_DECOMPOSITION -> DISTRIBUTION
    -> R_MULTIPLE -> EXCURSION -> HOLDING/EXIT -> STREAK -> RISK
    -> DRAWDOWN -> STRATEGY -> MODEL -> EXECUTION -> SESSION
    -> REGIME -> NEWS -> BEHAVIORAL -> LOSS/PROFIT DRIVERS
    -> PERIOD_COMPARE -> ANOMALY -> HEALTH_SCORE -> INSIGHTS

Every advanced conclusion carries an evidence level (sample-size policy):

    <5    : DO_NOT_RANK
    5-19  : LOW_EVIDENCE
    20-49 : USABLE
    50+   : STRONGER_EVIDENCE

Module map:

    models.py         report JSON contract dataclasses
    engine.py         PerformanceReportEngine (metric stages)
    insights.py       period compare / anomalies / health score / summary
    telegram_format.py  compact + deep Telegram formatting (consumes contract)
"""

from nexus_scalp.reporting.engine import PerformanceReportEngine
from nexus_scalp.reporting.insights import (
    classify_trend,
    compute_anomalies,
    compute_health_score,
    evidence_level,
    generate_insights,
    make_report_id,
    make_snapshot_id,
)
from nexus_scalp.reporting.models import (
    AnomalyItem,
    AnomalyStateSection,
    BehavioralSection,
    DistributionSection,
    DrawdownSection,
    EvidenceLevel,
    ExecutionSection,
    ExitGroup,
    HoldingSection,
    InsightItem,
    LossDriversSection,
    ModelSection,
    NewsSection,
    PerformanceSection,
    PeriodCompareSection,
    ProfitDriversSection,
    RegimeGroup,
    ReportContainer,
    RiskSection,
    RSection,
    SessionGroup,
    SnapshotBlock,
    StrategyGroup,
    TrendClassification,
)
from nexus_scalp.reporting.telegram_format import (
    format_deep_report,
    format_telegram_daily,
)

__all__ = [
    "AnomalyItem",
    "AnomalyStateSection",
    "BehavioralSection",
    "DistributionSection",
    "DrawdownSection",
    "EvidenceLevel",
    "ExecutionSection",
    "ExitGroup",
    "HoldingSection",
    "InsightItem",
    "LossDriversSection",
    "ModelSection",
    "NewsSection",
    "PerformanceReportEngine",
    "PerformanceSection",
    "PeriodCompareSection",
    "ProfitDriversSection",
    "RSection",
    "RegimeGroup",
    "ReportContainer",
    "RiskSection",
    "SessionGroup",
    "SnapshotBlock",
    "StrategyGroup",
    "TrendClassification",
    "classify_trend",
    "compute_anomalies",
    "compute_health_score",
    "evidence_level",
    "format_deep_report",
    "format_telegram_daily",
    "generate_insights",
    "make_report_id",
    "make_snapshot_id",
]
