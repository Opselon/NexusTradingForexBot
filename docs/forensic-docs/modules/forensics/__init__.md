# src/nexus_scalp/forensics/__init__.py

- PURPOSE: Forensic monitoring package entry (TASK-11 foundation +
  TASK-12 activation) — facade re-exporting the whole forensic API:
  deploy gate, health engine, experience gap, models, references,
  telegram report, trend.
- ARCHITECTURE LAYER: Application/observability (monitoring plane).
- RESPONSIBILITY: Public API surface of forensics; __all__ is the
  governed contract.
- DEPENDENCIES: sibling modules only.
- CONNECTS TO: LiveEngine periodic wiring (TelegramReportScheduler,
  ForensicHealthEngine, deploy gate), Web dashboard, operators.
- KEY CONCEPTS:
  - Exports grouped by feature: deploy gate (DEPLOY_POLICY,
    EXIT_ALLOW/BLOCK/REVIEW/ENGINE_UNAVAILABLE, DeployGateResult,
    load_last_gate_result, run_deploy_gate), engine (ForensicHealthEngine),
    experience gap (GAP_CLASSES, ExperienceGapReport,
    analyze_experience_gap, classify_missing_outcome, load_gap_thresholds,
    persist_gap_report), models (CheckResult, ForensicCheckError,
    HealthStatus, worst_status), references (FEATURE_REFERENCES,
    GOLDEN_BASELINE_PATH, LIQUIDITY_70D_FEATURE_NAMES,
    FeatureReferenceRegistry, FeatureReferenceStats, compute_reference_stats,
    freeze_liquidity_references_from_golden), telegram report
    (DEFAULT_MIN_SEVERITY, ForensicReportConfig, build_report_text,
    load_report_config, TelegramReportScheduler), trend (compare_snapshots,
    load_history, latest_trend).
- HOT PATH / PERFORMANCE: import-time only.
- EDGE CASES & PITFALLS: no logic; the __all__ list is the diff-able
  public contract (adding/removing exports is a governance-visible
  change).