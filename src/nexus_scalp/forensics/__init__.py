"""Forensic monitoring package (TASK-11 foundation + TASK-12 activation).

Import graph safety: __init__ does NOT eagerly import any submodule at
top-level — all names are exposed via __getattr__ (PEP 562, lazily bound
on first access). This prevents the unsafe parent<->child cycle where
forensics/__init__ imported engine and deploy_gate while those submodules
import from the *parent* package at their top level (forensics.trend ->
engine, forensics.deploy_gate -> engine). The runtime behavior is
identical for callers (from nexus_scalp.forensics import ForensicHealthEngine
still works), but import-time no longer enters a partially-initialized module.

The cycle was flagged by the unsafe-cyclic-import scan (cycle2.py / CodeQL
py/unsafe-cyclic-import): 8 alerts in the forensics package. Lazy __init__
removes all 8 with zero visible surface change, CRLF-safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

_LAZY: dict[str, tuple[str, str]] = {
    # deploy_gate
    "DEPLOY_POLICY": ("nexus_scalp.forensics.deploy_gate", "DEPLOY_POLICY"),
    "EXIT_ALLOW": ("nexus_scalp.forensics.deploy_gate", "EXIT_ALLOW"),
    "EXIT_BLOCK": ("nexus_scalp.forensics.deploy_gate", "EXIT_BLOCK"),
    "EXIT_ENGINE_UNAVAILABLE": ("nexus_scalp.forensics.deploy_gate", "EXIT_ENGINE_UNAVAILABLE"),
    "EXIT_REVIEW": ("nexus_scalp.forensics.deploy_gate", "EXIT_REVIEW"),
    "DeployGateResult": ("nexus_scalp.forensics.deploy_gate", "DeployGateResult"),
    "load_last_gate_result": ("nexus_scalp.forensics.deploy_gate", "load_last_gate_result"),
    "run_deploy_gate": ("nexus_scalp.forensics.deploy_gate", "run_deploy_gate"),
    # engine
    "ForensicHealthEngine": ("nexus_scalp.forensics.engine", "ForensicHealthEngine"),
    # experience_gap
    "GAP_CLASSES": ("nexus_scalp.forensics.experience_gap", "GAP_CLASSES"),
    "ExperienceGapReport": ("nexus_scalp.forensics.experience_gap", "ExperienceGapReport"),
    "analyze_experience_gap": ("nexus_scalp.forensics.experience_gap", "analyze_experience_gap"),
    "classify_missing_outcome": ("nexus_scalp.forensics.experience_gap", "classify_missing_outcome"),
    "load_gap_thresholds": ("nexus_scalp.forensics.experience_gap", "load_gap_thresholds"),
    "persist_gap_report": ("nexus_scalp.forensics.experience_gap", "persist_gap_report"),
    # models
    "CheckResult": ("nexus_scalp.forensics.models", "CheckResult"),
    "ForensicCheckError": ("nexus_scalp.forensics.models", "ForensicCheckError"),
    "HealthStatus": ("nexus_scalp.forensics.models", "HealthStatus"),
    "worst_status": ("nexus_scalp.forensics.models", "worst_status"),
    # references
    "FEATURE_REFERENCES": ("nexus_scalp.forensics.references", "FEATURE_REFERENCES"),
    "GOLDEN_BASELINE_PATH": ("nexus_scalp.forensics.references", "GOLDEN_BASELINE_PATH"),
    "LIQUIDITY_70D_FEATURE_NAMES": ("nexus_scalp.forensics.references", "LIQUIDITY_70D_FEATURE_NAMES"),
    "FeatureReferenceRegistry": ("nexus_scalp.forensics.references", "FeatureReferenceRegistry"),
    "FeatureReferenceStats": ("nexus_scalp.forensics.references", "FeatureReferenceStats"),
    "compute_reference_stats": ("nexus_scalp.forensics.references", "compute_reference_stats"),
    "freeze_liquidity_references_from_golden": (
        "nexus_scalp.forensics.references",
        "freeze_liquidity_references_from_golden",
    ),
    # telegram_report
    "DEFAULT_MIN_SEVERITY": ("nexus_scalp.forensics.telegram_report", "DEFAULT_MIN_SEVERITY"),
    "ForensicReportConfig": ("nexus_scalp.forensics.telegram_report", "ForensicReportConfig"),
    "TelegramReportScheduler": ("nexus_scalp.forensics.telegram_report", "TelegramReportScheduler"),
    "build_report_text": ("nexus_scalp.forensics.telegram_report", "build_report_text"),
    "load_report_config": ("nexus_scalp.forensics.telegram_report", "load_report_config"),
    # trend
    "compare_snapshots": ("nexus_scalp.forensics.trend", "compare_snapshots"),
    "latest_trend": ("nexus_scalp.forensics.trend", "latest_trend"),
    "load_history": ("nexus_scalp.forensics.trend", "load_history"),
}

if TYPE_CHECKING:
    # preserve static-analyzer surface (no runtime effect)
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


def __getattr__(name: str) -> Any:
    spec = _LAZY.get(name)
    if spec is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = spec
    import importlib

    mod = importlib.import_module(mod_name)
    val = getattr(mod, attr)
    globals()[name] = val
    return val


def __dir__() -> list[str]:
    return sorted([*globals().keys(), *_LAZY.keys()])


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
