"""Incident Response layer (TASK-12) — incident correlation, root-cause
tracing, impact analysis and safe recovery planning on top of TASK-11's
permanent monitoring.

Layer stack:

    HEALTH MONITORING (TASK-11 forensics/)
        |
    INCIDENT DETECTION (correlator)
        |
    INCIDENT CORRELATION (fingerprint + correlation_id + ticket grouping)
        |
    ROOT-CAUSE TRACE (lineage / first-divergence walk / WHY workflows)
        |
    IMPACT ANALYSIS (observed evidence only)
        |
    SAFE RECOVERY PLAN (RECOMMENDED, approval-gated)
        |
    HUMAN / GOVERNED ACTION (never automatic)

SAFETY (spec 0): this package performs NO trading mutation, NO RiskEngine
change, NO accounting rewrite, NO automatic recovery execution, NO automatic
code mutation. Containment is limited to explicitly safe advisory states.
"""

from __future__ import annotations

from nexus_scalp.incidents.accounting import (
    RECONSTRUCTION_ALGORITHM_VERSION,
    ROOT_CAUSE_CLASSES,
    ZERO_OUTCOME_CLASSES,
    AccountingForensicsEngine,
    build_accounting_divergence_artifact,
)
from nexus_scalp.incidents.correlator import (
    DEFAULT_WINDOWS_SEC,
    KNOWN_FAILURE_CLASSES,
    SEVERITY_BY_CODE,
    CorrelationResult,
    IncidentCorrelator,
    TelemetryEvent,
)
from nexus_scalp.incidents.impact import (
    ImpactAnalyzer,
    QuarantineManager,
    RecoveryPlanner,
)
from nexus_scalp.incidents.lineage import (
    PRODUCERS,
    TRANSFORMATIONS,
    LineageEngine,
    build_simple_trace,
)
from nexus_scalp.incidents.models import (
    BlastRadius,
    BugLinkage,
    CorrelationPattern,
    EventSource,
    EvidenceItem,
    Incident,
    IncidentCategory,
    IncidentImpact,
    IncidentSeverity,
    IncidentStatus,
    LineageStep,
    QuarantineEntry,
    RecoveryAction,
    RecoveryPlan,
    RootCauseConfidence,
    TimelineEvent,
    ValueTrace,
    incident_fingerprint,
    new_incident_id,
)
from nexus_scalp.incidents.reports import (
    export_zip_bundle,
    incident_json,
    incident_markdown,
    mask_secrets,
    write_incident_reports,
)
from nexus_scalp.incidents.store import INCIDENT_DDL, IncidentStore
from nexus_scalp.incidents.telegram import (
    ALERT_SEVERITIES,
    DEFAULT_COOLDOWN_SEC,
    DEFAULT_REPEAT_COOLDOWN_SEC,
    IncidentTelegramNotifier,
)
from nexus_scalp.incidents.telemetry import (
    ENGINE_EVENT_MAP,
    IncidentTelemetryCollector,
    engine_event_to_telemetry,
)
from nexus_scalp.incidents.timebase import (
    TimebaseProbe,
    build_timebase_probe,
)
from nexus_scalp.incidents.trace import (
    broker_ledger_divergence,
    clock_skew,
    learning_pipeline_rates,
    news_incidents,
    outcome_forensics,
    split_fill_groups,
    version_consistency,
    why_blocked,
    why_closed,
    why_no_learning,
    why_no_strategy,
    why_ui_empty,
)
from nexus_scalp.incidents.worker import (
    CYCLE_BUDGET_SEC,
    DEFAULT_INTERVAL_SEC,
    MAX_SAVES_PER_CYCLE,
    IncidentWorker,
    format_incident_worker_status,
)

__all__ = [
    "ALERT_SEVERITIES",
    "CYCLE_BUDGET_SEC",
    "DEFAULT_COOLDOWN_SEC",
    "DEFAULT_INTERVAL_SEC",
    "DEFAULT_REPEAT_COOLDOWN_SEC",
    "DEFAULT_WINDOWS_SEC",
    "ENGINE_EVENT_MAP",
    "INCIDENT_DDL",
    "KNOWN_FAILURE_CLASSES",
    "MAX_SAVES_PER_CYCLE",
    "PRODUCERS",
    "RECONSTRUCTION_ALGORITHM_VERSION",
    "ROOT_CAUSE_CLASSES",
    "SEVERITY_BY_CODE",
    "TRANSFORMATIONS",
    "ZERO_OUTCOME_CLASSES",
    "AccountingForensicsEngine",
    "BlastRadius",
    "BugLinkage",
    "CorrelationPattern",
    "CorrelationResult",
    "EventSource",
    "EvidenceItem",
    "ImpactAnalyzer",
    "Incident",
    "IncidentCategory",
    "IncidentCorrelator",
    "IncidentImpact",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentStore",
    "IncidentTelegramNotifier",
    "IncidentTelemetryCollector",
    "IncidentWorker",
    "LineageEngine",
    "LineageStep",
    "QuarantineEntry",
    "QuarantineManager",
    "RecoveryAction",
    "RecoveryPlan",
    "RecoveryPlanner",
    "RootCauseConfidence",
    "TelemetryEvent",
    "TimebaseProbe",
    "TimelineEvent",
    "ValueTrace",
    "broker_ledger_divergence",
    "build_accounting_divergence_artifact",
    "build_simple_trace",
    "build_timebase_probe",
    "clock_skew",
    "engine_event_to_telemetry",
    "export_zip_bundle",
    "format_incident_worker_status",
    "incident_fingerprint",
    "incident_json",
    "incident_markdown",
    "learning_pipeline_rates",
    "mask_secrets",
    "new_incident_id",
    "news_incidents",
    "outcome_forensics",
    "split_fill_groups",
    "version_consistency",
    "why_blocked",
    "why_closed",
    "why_no_learning",
    "why_no_strategy",
    "why_ui_empty",
    "write_incident_reports",
]
