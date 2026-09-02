# src/nexus_scalp/incidents/__init__.py

- PURPOSE: Package facade + layer-stack documentation for the Incident
  Response layer (TASK-12) built on TASK-11 permanent monitoring.
- ARCHITECTURE LAYER: Application (incident runtime package public API).
- RESPONSIBILITY: re-export the public surface: correlator (IncidentCorrelator,
  TelemetryEvent, CorrelationResult, KNOWN_FAILURE_CLASSES, SEVERITY_BY_CODE,
  DEFAULT_WINDOWS_SEC), impact (ImpactAnalyzer, QuarantineManager,
  RecoveryPlanner), lineage (LineageEngine, PRODUCERS, TRANSFORMATIONS,
  build_simple_trace), accounting (AccountingForensicsEngine,
  RECONSTRUCTION_ALGORITHM_VERSION, ROOT_CAUSE_CLASSES, ZERO_OUTCOME_CLASSES,
  build_accounting_divergence_artifact), models, reports (incident_json/
  markdown, mask_secrets, export_zip_bundle, write_incident_reports), store
  (INCIDENT_DDL, IncidentStore), telegram (ALERT_SEVERITIES, cooldowns,
  IncidentTelegramNotifier), telemetry (ENGINE_EVENT_MAP,
  IncidentTelemetryCollector, engine_event_to_telemetry), timebase
  (TimebaseProbe, build_timebase_probe), the WHY-trace query functions
  (why_blocked, why_closed, why_no_learning, why_no_strategy, why_ui_empty,
  news_incidents, version_consistency, learning_pipeline_rates,
  outcome_forensics, split_fill_groups, broker_ledger_divergence, clock_skew)
  and the worker (IncidentWorker, format_incident_worker_status,
  CYCLE_BUDGET_SEC, DEFAULT_INTERVAL_SEC, MAX_SAVES_PER_CYCLE).
- DEPENDENCIES: all sibling modules (import-time only; the heavy ones are
  imported eagerly — store/reports/worker get pulled in on package import).
- CONNECTS TO: web API diagnostics /api/diagnostics/*, LiveEngine startup,
  tests.
- KEY CONCEPTS:
  - Layer stack encoded in the docstring: HEALTH MONITORING (TASK-11) →
    INCIDENT DETECTION (correlator) → CORRELATION (fingerprint +
    correlation_id + ticket grouping) → ROOT-CAUSE TRACE (lineage/first-
    divergence walk/WHY workflows) → IMPACT ANALYSIS (observed evidence
    only) → SAFE RECOVERY PLAN (RECOMMENDED, approval-gated) → HUMAN /
    GOVERNED ACTION (never automatic).
  - SAFETY (spec 0, line 21-23): no trading mutation, no RiskEngine change,
    no accounting rewrite, no automatic recovery execution, no automatic
    code mutation; containment limited to explicitly safe advisory states.
- HOT PATH / PERFORMANCE: import-time only; all re-exports are plain
  references (no lazy loading) — package import cost is the sum of module
  imports, acceptable for a runtime loaded once at startup.
- EDGE CASES & PITFALLS: because __all__ is comprehensive, module-level
  names used internally (e.g. sqlite3 imports in siblings) are not leaked;
  adding a new public symbol requires updating both the import block and
  __all__.