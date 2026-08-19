"""Incident Response Core — domain models, enums, and canonical contracts (TASK-12).

One canonical incident structure per docs/70D_INCIDENT_RESPONSE_MODEL.md:

    incident_id | detected_at | severity | category | status | first_seen_at |
    last_seen_at | component | operation | correlation_id | root_cause_status |
    root_cause | evidence | impact | affected_records | affected_models |
    affected_runtime | affected_users | recovery_status | recommended_action

Statuses: OPEN / INVESTIGATING / ROOT_CAUSE_IDENTIFIED / CONTAINED /
RECOVERY_READY / RECOVERED / CLOSED / FALSE_POSITIVE

Severities: INFO / LOW / MEDIUM / HIGH / CRITICAL

Root-cause confidence: PROVEN / HIGH_CONFIDENCE / PLAUSIBLE / UNKNOWN

Recovery plan state: RECOMMENDED / APPROVED / EXECUTING / COMPLETED / FAILED

SAFETY: TASK-12 is diagnostic-only. Nothing in this package can change
trading behavior, RiskEngine, lot sizing, SL/TP, execution rules, models or
accounting history (spec 0). Containment/recovery actions are records with
governance states — never auto-executed destructive repairs.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class IncidentSeverity(StrEnum):
    """Evidence-driven severity (spec 3)."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return {
            IncidentSeverity.INFO: 0,
            IncidentSeverity.LOW: 1,
            IncidentSeverity.MEDIUM: 2,
            IncidentSeverity.HIGH: 3,
            IncidentSeverity.CRITICAL: 4,
        }[self]


class IncidentStatus(StrEnum):
    """Canonical lifecycle (spec 2)."""

    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    ROOT_CAUSE_IDENTIFIED = "ROOT_CAUSE_IDENTIFIED"
    CONTAINED = "CONTAINED"
    RECOVERY_READY = "RECOVERY_READY"
    RECOVERED = "RECOVERED"
    CLOSED = "CLOSED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class RootCauseConfidence(StrEnum):
    """Never label a hypothesis as proven (spec 32)."""

    PROVEN = "PROVEN"
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    PLAUSIBLE = "PLAUSIBLE"
    UNKNOWN = "UNKNOWN"


class RecoveryState(StrEnum):
    """Recovery must be explicit + governed (spec 29)."""

    RECOMMENDED = "RECOMMENDED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IncidentCategory(StrEnum):
    """Incident categories (from the historical failure classes)."""

    MT5 = "MT5"
    LEDGER = "LEDGER"
    ACCOUNTING = "ACCOUNTING"
    DATA = "DATA"
    LEARNING = "LEARNING"
    RESEARCH = "RESEARCH"
    MODEL = "MODEL"
    FEATURE = "FEATURE"
    NEWS = "NEWS"
    UI = "UI"
    API = "API"
    WORKER = "WORKER"
    EXECUTION = "EXECUTION"
    GOVERNANCE = "GOVERNANCE"
    MIGRATION = "MIGRATION"
    VERSION = "VERSION"
    TELEGRAM = "TELEGRAM"
    EXPOSURE = "EXPOSURE"
    SECURITY = "SECURITY"
    OTHER = "OTHER"


class BlastRadius(StrEnum):
    """Localized-to-system impact classification (spec 26)."""

    LOCAL = "LOCAL"
    COMPONENT = "COMPONENT"
    CROSS_COMPONENT = "CROSS_COMPONENT"
    SYSTEM_WIDE = "SYSTEM_WIDE"


class EventSource(StrEnum):
    """Where an incident event/timeline entry came from (spec 10/11/12)."""

    LOG = "LOG"
    DATABASE = "DATABASE"
    TELEMETRY = "TELEMETRY"
    BROKER = "BROKER"
    API = "API"
    RUNTIME = "RUNTIME"
    TEST = "TEST"
    MANUAL = "MANUAL"


class CorrelationPattern(StrEnum):
    """Causal chain shapes the correlator recognizes (spec 4/5)."""

    SINGLE = "SINGLE"
    CHAIN = "CHAIN"
    FAN_OUT = "FAN_OUT"
    LOOP = "LOOP"


# ---------------------------------------------------------------------------
# Value lineage (spec 8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageStep:
    """One transformation/cache/persistence hop in a value's life."""

    stage: str  # SOURCE_OF_TRUTH | TRANSFORMATION | CACHE | PERSISTENCE | API | UI
    name: str
    detail: str = ""
    timestamp: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "name": self.name,
            "detail": self.detail,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass(frozen=True)
class ValueTrace:
    """Reusable lineage abstraction (spec 8).

    Example — PnL: MT5 deal -> broker adapter -> deal snapshot ->
    reconciliation -> accounting core -> API -> UI.
    """

    field: str
    source: str
    source_timestamp: datetime | None = None
    transformations: tuple[LineageStep, ...] = ()
    cache_layers: tuple[LineageStep, ...] = ()
    persistence: tuple[LineageStep, ...] = ()
    consumers: tuple[LineageStep, ...] = ()

    def hops(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [
            {
                "stage": "SOURCE_OF_TRUTH",
                "name": self.source,
                "detail": "",
                "timestamp": self.source_timestamp.isoformat() if self.source_timestamp else None,
            }
        ]
        for grp in (self.transformations, self.cache_layers, self.persistence, self.consumers):
            out.extend(s.as_dict() for s in grp)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "source": self.source,
            "source_timestamp": self.source_timestamp.isoformat()
            if self.source_timestamp
            else None,
            "hops": self.hops(),
        }


# ---------------------------------------------------------------------------
# Timelines & events
# ---------------------------------------------------------------------------


@dataclass
class TimelineEvent:
    """One dated event on an incident timeline (spec 10; real timestamps only)."""

    timestamp: datetime
    event_type: str
    source: EventSource
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "source": self.source.value,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
        }


# ---------------------------------------------------------------------------
# Evidence / impact / quarantine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of root-cause evidence (spec 33, independent corroboration)."""

    kind: str  # LOG | DATABASE | BROKER | RUNTIME_TRACE | TEST | OBSERVATION
    source: str  # e.g. artifacts/logs/nse_live.log line, audit_ledger row id
    detail: str
    observed: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "detail": self.detail,
            "observed": self.observed,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass
class IncidentImpact:
    """Estimated impact (spec 25). Never fabricated — only observed counts."""

    affected_time_range: tuple[datetime, datetime] | None = None
    affected_records: int = 0
    affected_trades: int = 0
    affected_models: int = 0
    affected_research_runs: int = 0
    affected_ui_endpoints: list[str] = field(default_factory=list)
    affected_users: int = 0
    blast_radius: BlastRadius = BlastRadius.LOCAL
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "affected_time_range": (
                [self.affected_time_range[0].isoformat(), self.affected_time_range[1].isoformat()]
                if self.affected_time_range
                else None
            ),
            "affected_records": self.affected_records,
            "affected_trades": self.affected_trades,
            "affected_models": self.affected_models,
            "affected_research_runs": self.affected_research_runs,
            "affected_ui_endpoints": list(self.affected_ui_endpoints),
            "affected_users": self.affected_users,
            "blast_radius": self.blast_radius.value,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class QuarantineEntry:
    """Non-destructive data quarantine mark (spec 30).

    Keeps: original record + reason + incident_id + timestamp. Never deletes.
    """

    target_table: str
    record_key: str  # canonical identity of the row (e.g. ticket / idempotency_key)
    status: str  # SUSPECT | INVALIDATED | QUARANTINED
    reason: str
    incident_id: str
    quarantined_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    evidence: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_table": self.target_table,
            "record_key": self.record_key,
            "status": self.status,
            "reason": self.reason,
            "incident_id": self.incident_id,
            "quarantined_at": self.quarantined_at.isoformat(),
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryAction:
    """One step of a generated recovery plan (spec 28/29).

    Every recovery step is a PROPOSAL. Execution requires operator approval
    (governed state transition RECOMMENDED -> APPROVED -> EXECUTING).
    Destructive steps (delete/rewrite) are never auto-executed.
    """

    step_id: str
    action: str  # human-readable step
    kind: str  # RECONCILE | REBUILD | REVALIDATE | QUARANTINE | BLOCK | NOTIFY | MANUAL
    destructive: bool = False
    required_tests: list[str] = field(default_factory=list)
    approval_required: bool = True
    status: RecoveryState = RecoveryState.RECOMMENDED

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "kind": self.kind,
            "destructive": self.destructive,
            "required_tests": list(self.required_tests),
            "approval_required": self.approval_required,
            "status": self.status.value,
        }


@dataclass
class RecoveryPlan:
    """Generated recovery plan (spec 28). Explicit, never silently executed."""

    what_failed: str = ""
    why: str = ""
    affected: str = ""
    trustworthy: list[str] = field(default_factory=list)
    suspect: list[str] = field(default_factory=list)
    must_not_change: list[str] = field(default_factory=list)
    required_tests: list[str] = field(default_factory=list)
    options: list[RecoveryAction] = field(default_factory=list)
    status: RecoveryState = RecoveryState.RECOMMENDED

    def as_dict(self) -> dict[str, Any]:
        return {
            "what_failed": self.what_failed,
            "why": self.why,
            "affected": self.affected,
            "trustworthy": list(self.trustworthy),
            "suspect": list(self.suspect),
            "must_not_change": list(self.must_not_change),
            "required_tests": list(self.required_tests),
            "options": [o.as_dict() for o in self.options],
            "status": self.status.value,
        }


# ---------------------------------------------------------------------------
# The canonical incident record
# ---------------------------------------------------------------------------


def new_incident_id() -> str:
    """Deterministic-ish unique incident id: INC-YYYY-<hex8>."""
    now = datetime.now(UTC)
    return f"INC-{now.year}-{uuid.uuid4().hex[:8].upper()}"


def incident_fingerprint(*, category: str, component: str, error_code: str) -> str:
    """Stable root fingerprint for deduplication (spec 31).

    55 identical exceptions -> ONE incident with repeated_count=55.
    """
    raw = f"{category}|{component}|{error_code}".lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Incident:
    """The canonical incident record (docs/70D_INCIDENT_RESPONSE_MODEL.md)."""

    incident_id: str = field(default_factory=new_incident_id)
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    severity: IncidentSeverity = IncidentSeverity.LOW
    category: IncidentCategory = IncidentCategory.OTHER
    status: IncidentStatus = IncidentStatus.OPEN
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    component: str = ""
    operation: str = ""
    correlation_id: str = ""
    root_cause_status: RootCauseConfidence = RootCauseConfidence.UNKNOWN
    root_cause: str = ""
    evidence: list[EvidenceItem] = field(default_factory=list)
    impact: IncidentImpact = field(default_factory=IncidentImpact)
    affected_records: list[str] = field(default_factory=list)
    affected_models: list[str] = field(default_factory=list)
    affected_runtime: list[str] = field(default_factory=list)
    affected_users: list[str] = field(default_factory=list)
    recovery_status: RecoveryState = RecoveryState.RECOMMENDED
    recommended_action: str = ""
    # Deduplication / correlation (spec 31/32/52/53)
    fingerprint: str = ""
    repeated_count: int = 1
    related_bug_id: str = ""
    fix_commit: str = ""
    regression_test: str = ""
    is_regression: bool = False
    previous_bug_id: str = ""
    resolved_without_evidence: bool = False
    # Extra structure
    timeline: list[TimelineEvent] = field(default_factory=list)
    value_traces: list[ValueTrace] = field(default_factory=list)
    quarantine_entries: list[QuarantineEntry] = field(default_factory=list)
    recovery_plan: RecoveryPlan = field(default_factory=RecoveryPlan)
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # -- lifecycle helpers --------------------------------------------------

    def mark_seen(self, at: datetime | None = None) -> None:
        now = at or datetime.now(UTC)
        self.last_seen_at = max(self.last_seen_at, now) if self.last_seen_at else now
        if not self.first_seen_at or now < self.first_seen_at:
            self.first_seen_at = now

    def add_evidence(self, item: EvidenceItem) -> None:
        self.evidence.append(item)

    def add_timeline_event(self, event: TimelineEvent) -> None:
        self.timeline.append(event)
        self.mark_seen(event.timestamp)

    def add_value_trace(self, trace: ValueTrace) -> None:
        self.value_traces.append(trace)

    def add_quarantine(self, entry: QuarantineEntry) -> None:
        self.quarantine_entries.append(entry)

    # -- serialization ------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "detected_at": self.detected_at.isoformat(),
            "severity": self.severity.value,
            "category": self.category.value,
            "status": self.status.value,
            "first_seen_at": self.first_seen_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "component": self.component,
            "operation": self.operation,
            "correlation_id": self.correlation_id,
            "root_cause_status": self.root_cause_status.value,
            "root_cause": self.root_cause,
            "evidence": [e.as_dict() for e in self.evidence],
            "impact": self.impact.as_dict(),
            "affected_records": list(self.affected_records),
            "affected_models": list(self.affected_models),
            "affected_runtime": list(self.affected_runtime),
            "affected_users": list(self.affected_users),
            "recovery_status": self.recovery_status.value,
            "recommended_action": self.recommended_action,
            "fingerprint": self.fingerprint,
            "repeated_count": self.repeated_count,
            "related_bug_id": self.related_bug_id,
            "fix_commit": self.fix_commit,
            "regression_test": self.regression_test,
            "is_regression": self.is_regression,
            "previous_bug_id": self.previous_bug_id,
            "resolved_without_evidence": self.resolved_without_evidence,
            "timeline": [t.as_dict() for t in sorted(self.timeline, key=lambda t: t.timestamp)],
            "value_traces": [t.as_dict() for t in self.value_traces],
            "quarantine_entries": [q.as_dict() for q in self.quarantine_entries],
            "recovery_plan": self.recovery_plan.as_dict(),
            "tags": list(self.tags),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# BUG ledger linkage (spec 54)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BugLinkage:
    """incident_id <-> BUG-NNN <-> fix_commit <-> regression_test."""

    incident_id: str
    bug_id: str
    fix_commit: str = ""
    regression_test: str = ""
    linked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "bug_id": self.bug_id,
            "fix_commit": self.fix_commit,
            "regression_test": self.regression_test,
            "linked_at": self.linked_at.isoformat(),
        }


__all__ = [
    "BlastRadius",
    "BugLinkage",
    "CorrelationPattern",
    "EventSource",
    "EvidenceItem",
    "Incident",
    "IncidentCategory",
    "IncidentImpact",
    "IncidentSeverity",
    "IncidentStatus",
    "LineageStep",
    "QuarantineEntry",
    "RecoveryAction",
    "RecoveryPlan",
    "RecoveryState",
    "RootCauseConfidence",
    "TimelineEvent",
    "ValueTrace",
    "incident_fingerprint",
    "new_incident_id",
]
