# src/nexus_scalp/reporting/__init__.py

- PURPOSE: Package facade for Performance Intelligence Reporting (Telegram Daily Intelligence) — re-exports the `PerformanceReportEngine`, the report-contract models, insight/health/anomaly functions, and the two Telegram formatters as one stable namespace.
- ARCHITECTURE LAYER: Application (deterministic multi-stage report generator built ON TOP of the canonical accounting core; READ-ONLY enrichment).
- RESPONSIBILITY: Declares the mandated architecture (MT5/canonical ledger → AccountingCore → PerformanceReportEngine → structured JSON contract + Telegram formatter), the deterministic stage pipeline (SNAPSHOT → OUTCOMES → PROFIT_DECOMPOSITION → DISTRIBUTION → R_MULTIPLE → EXCURSION → HOLDING/EXIT → STREAK → RISK → DRAWDOWN → STRATEGY → MODEL → EXECUTION → SESSION → REGIME → NEWS → BEHAVIORAL → LOSS/PROFIT DRIVERS → PERIOD_COMPARE → ANOMALY → HEALTH_SCORE → INSIGHTS), and the evidence-level policy (<5 DO_NOT_RANK, 5-19 LOW_EVIDENCE, 20-49 USABLE, 50+ STRONGER_EVIDENCE).
- DEPENDENCIES:
  - `reporting.engine` → `PerformanceReportEngine` (the generator).
  - `reporting.insights` → `classify_trend`, `compute_anomalies`, `compute_health_score`, `evidence_level`, `generate_insights`, `make_report_id`, `make_snapshot_id` (enrichment stages).
  - `reporting.models` → the full frozen report-contract dataclasses and enums (EvidenceLevel, TrendClassification, ReportContainer, all sections).
  - `reporting.telegram_format` → `format_telegram_daily` (MESSAGE 1 compact) and `format_deep_report` (MESSAGE 2/3 deep).
- CONNECTS TO: The Telegram notifier (daily report send), `/api/account/performance/intelligence` REST endpoint, and any consumer that builds the structured report. The module docstring's architectural note is the normative contract: this package NEVER writes financial truth, never opens trades, never modifies risk/model/news gates.
- KEY CONCEPTS:
  - The `__all__` list (lines 83-118) is the public contract.
  - Note: `classify_session` and `compare_periods` (defined in insights.py) are NOT re-exported here — engine.py imports them directly from insights. Public-surface drift between the two modules is minor but real.
- HOT PATH / PERFORMANCE: None — import surface only.
- EDGE CASES & PITFALLS:
  - Everything here is derived enrichment: consumers must treat `ReportContainer` as the final artifact (serializable via `to_dict()`), never recompute numbers from it inside string code.