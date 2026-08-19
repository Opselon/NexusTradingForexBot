"""Forensic monitoring package (TASK-11 foundation + TASK-12 activation)."""

from nexus_scalp.forensics.deploy_gate import (
    DEPLOY_POLICY,
    EXIT_ALLOW,
    EXIT_BLOCK,
    EXIT_ENGINE_UNAVAILABLE,
    EXIT_REVIEW,
    DeployGateResult,
    load_last_gate_result,
    run_deploy_gate,
)
from nexus_scalp.forensics.engine import ForensicHealthEngine
from nexus_scalp.forensics.experience_gap import (
    GAP_CLASSES,
    ExperienceGapReport,
    analyze_experience_gap,
    classify_missing_outcome,
    load_gap_thresholds,
    persist_gap_report,
)
from nexus_scalp.forensics.models import (
    CheckResult,
    ForensicCheckError,
    HealthStatus,
    worst_status,
)
from nexus_scalp.forensics.references import (
    FEATURE_REFERENCES,
    GOLDEN_BASELINE_PATH,
    LIQUIDITY_70D_FEATURE_NAMES,
    FeatureReferenceRegistry,
    FeatureReferenceStats,
    compute_reference_stats,
    freeze_liquidity_references_from_golden,
)
from nexus_scalp.forensics.telegram_report import (
    DEFAULT_MIN_SEVERITY,
    ForensicReportConfig,
    TelegramReportScheduler,
    build_report_text,
    load_report_config,
)
from nexus_scalp.forensics.trend import (
    compare_snapshots,
    latest_trend,
    load_history,
)

__all__ = [
    "DEFAULT_MIN_SEVERITY",
    # deploy gate
    "DEPLOY_POLICY",
    "EXIT_ALLOW",
    "EXIT_BLOCK",
    "EXIT_ENGINE_UNAVAILABLE",
    "EXIT_REVIEW",
    "FEATURE_REFERENCES",
    # experience gap
    "GAP_CLASSES",
    "GOLDEN_BASELINE_PATH",
    "LIQUIDITY_70D_FEATURE_NAMES",
    "CheckResult",
    "DeployGateResult",
    "ExperienceGapReport",
    "FeatureReferenceRegistry",
    "FeatureReferenceStats",
    "ForensicCheckError",
    "ForensicHealthEngine",
    # telegram report
    "ForensicReportConfig",
    "HealthStatus",
    "TelegramReportScheduler",
    "analyze_experience_gap",
    "build_report_text",
    "classify_missing_outcome",
    # trend
    "compare_snapshots",
    "compute_reference_stats",
    "freeze_liquidity_references_from_golden",
    "latest_trend",
    "load_gap_thresholds",
    "load_history",
    "load_last_gate_result",
    "load_report_config",
    "persist_gap_report",
    "run_deploy_gate",
    "worst_status",
]
