"""Incident correlation engine (TASK-12 spec 4/5/6/9/10/31).

Given a stream of telemetry events (log lines, DB anomalies, runtime
symptoms), the correlator:

1. derives a stable root fingerprint per event (category|component|error_code),
2. groups repeated identical events into ONE incident (dedup, spec 31),
3. correlates events sharing correlation_id / ticket / execution identity
   into one timeline (spec 4/10),
4. recognizes causal chains (ROOT EVENT -> PRIMARY FAILURE -> STATE
   CORRUPTION -> DOWNSTREAM EFFECT -> USER-VISIBLE SYMPTOM),
5. estimates the incident's first divergence point (first-failure
   identification, spec 6).

The correlator NEVER mutates trading state. It only groups evidence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nexus_scalp.incidents.models import (
    CorrelationPattern,
    EventSource,
    Incident,
    IncidentCategory,
    IncidentSeverity,
    TimelineEvent,
    incident_fingerprint,
)

#: Default correlation windows per incident class (spec 11).
DEFAULT_WINDOWS_SEC: dict[str, float] = {
    "MT5": 30.0,
    "LEDGER": 300.0,
    "ACCOUNTING": 300.0,
    "DATA": 900.0,
    "LEARNING": 900.0,
    "RESEARCH": 900.0,
    "MODEL": 60.0,
    "FEATURE": 60.0,
    "NEWS": 900.0,
    "UI": 60.0,
    "API": 60.0,
    "WORKER": 600.0,
    "GOVERNANCE": 300.0,
    "MIGRATION": 300.0,
    "VERSION": 60.0,
    "TELEGRAM": 60.0,
    "EXPOSURE": 30.0,
    "SECURITY": 300.0,
    "OTHER": 300.0,
}

#: Causal chain hints — how symptom types map to chain positions.
CHAIN_HINTS: dict[str, str] = {
    "CALL_FAILED": "ROOT_EVENT",
    "EXCEPTION": "PRIMARY_FAILURE",
    "STATE_STALE": "STATE_CORRUPTION",
    "CACHE_STALE": "STATE_CORRUPTION",
    "POLICY_REJECTION": "DOWNSTREAM_EFFECT",
    "ORDER_REJECTED": "DOWNSTREAM_EFFECT",
    "SYMPTOM": "USER_VISIBLE_SYMPTOM",
    "UI_EMPTY": "USER_VISIBLE_SYMPTOM",
    "EMPTY": "USER_VISIBLE_SYMPTOM",
}

#: Known error codes per historical failure class -> (category, severity).
KNOWN_FAILURE_CLASSES: dict[str, tuple[str, str]] = {
    "MT5_CALL_FAILED": ("MT5", "HIGH"),
    "DEAL_LOOKUP_FAILED": ("LEDGER", "HIGH"),
    "DEAL_NOT_FOUND": ("LEDGER", "HIGH"),
    "REALIZED_PNL_ZERO": ("LEDGER", "MEDIUM"),
    "REALIZED_R_ZERO": ("LEDGER", "MEDIUM"),
    "ZERO_R_UPSTREAM": ("LEDGER", "MEDIUM"),
    "RESEARCH_SAMPLE_INVALID": ("RESEARCH", "MEDIUM"),
    "CANDIDATE_DISCOVERY_ZERO": ("RESEARCH", "MEDIUM"),
    "REGISTRY_EMPTY": ("RESEARCH", "LOW"),
    "MAX_EXPOSURE_FALSE_BLOCK": ("EXPOSURE", "HIGH"),
    "EXPOSURE_CACHE_STALE": ("EXPOSURE", "MEDIUM"),
    "ORDER_REJECTED": ("EXECUTION", "MEDIUM"),
    "REQUEST_ID_MISSING": ("LEARNING", "MEDIUM"),
    "OUTCOME_DISCARDED": ("LEARNING", "HIGH"),
    "LEARNING_DATA_LOSS": ("LEARNING", "HIGH"),
    "EXPERIENCE_TO_OUTCOME_DROP": ("LEARNING", "MEDIUM"),
    "OUTCOME_TO_RESEARCH_DROP": ("RESEARCH", "MEDIUM"),
    "RESEARCH_TO_CANDIDATE_DROP": ("RESEARCH", "MEDIUM"),
    "UI_STALE_BUNDLE": ("UI", "MEDIUM"),
    "API_BACKEND_MISMATCH": ("API", "MEDIUM"),
    "UI_BACKEND_MISMATCH": ("UI", "MEDIUM"),
    "SILENT_EXCEPTION": ("WORKER", "HIGH"),
    "WORKER_RUNNING_ZERO_PROGRESS": ("WORKER", "MEDIUM"),
    "WORKER_STALLED": ("WORKER", "MEDIUM"),
    "NEWS_ALL_NEUTRAL": ("NEWS", "LOW"),
    "NEWS_SOURCE_EMPTY": ("NEWS", "LOW"),
    "NEWS_PARSER_FAILED": ("NEWS", "MEDIUM"),
    "MODEL_CONTRACT_MISMATCH": ("MODEL", "HIGH"),
    "SCHEMA_MISMATCH": ("VERSION", "CRITICAL"),
    "VERSION_INCONSISTENCY": ("VERSION", "HIGH"),
    "MIGRATION_FAILED": ("MIGRATION", "HIGH"),
    "ACCOUNTING_DIVERGENCE": ("ACCOUNTING", "CRITICAL"),
    "DUPLICATE_ECONOMIC_OUTCOME": ("DATA", "CRITICAL"),
    "IMPOSSIBLE_EXCURSION": ("DATA", "LOW"),
    "DATA_CORRUPTION": ("DATA", "CRITICAL"),
    "FUTURE_LEAKAGE": ("DATA", "CRITICAL"),
    "CHAMPION_ARTIFACT_MISMATCH": ("MODEL", "CRITICAL"),
    "SILENT_FINANCIAL_CORRUPTION": ("ACCOUNTING", "CRITICAL"),
    "TIMEBASE_DIVERGENCE": ("MT5", "HIGH"),
    "CONTEXT_PROPAGATION_FAILURE": ("LEARNING", "MEDIUM"),
    "SPLIT_FILL_GROUPING": ("EXECUTION", "LOW"),
    "TELEGRAM_SILENT_FAILURE": ("TELEGRAM", "MEDIUM"),
    "TELEGRAM_SEND_FAILED": ("TELEGRAM", "LOW"),
    "SHADOW_ISOLATION_FAILURE": ("MODEL", "HIGH"),
    "BLOCKED_INFERENCE": ("MODEL", "MEDIUM"),
    "FEATURE_ALL_ZERO": ("FEATURE", "MEDIUM"),
    "FEATURE_ALL_MISSING": ("FEATURE", "MEDIUM"),
    "FEATURE_SATURATED": ("FEATURE", "LOW"),
    "FEATURE_STALE": ("FEATURE", "LOW"),
    "FEATURE_SCHEMA_SHIFTED": ("FEATURE", "HIGH"),
    "FEATURE_WRONG_INDEX": ("FEATURE", "HIGH"),
    "LEARNING_PIPELINE_LOSS": ("LEARNING", "HIGH"),
    "SHADOW_OUTCOME_ZERO": ("MODEL", "LOW"),
    "OUTCOME_SUSPECT": ("LEDGER", "LOW"),
    "SPLIT_FILL_CONTEXT_MISSING": ("LEARNING", "MEDIUM"),
    # OBS-PERF-RESILIENCE: hot-path fault visibility.
    "SLOW_INFERENCE": ("MODEL", "MEDIUM"),
}

#: Incidents that are terminal for data integrity — severity must be evidence-driven.
SEVERITY_BY_CODE: dict[str, str] = {
    "SCHEMA_MISMATCH": "CRITICAL",
    "ACCOUNTING_DIVERGENCE": "CRITICAL",
    "DUPLICATE_ECONOMIC_OUTCOME": "CRITICAL",
    "DATA_CORRUPTION": "CRITICAL",
    "FUTURE_LEAKAGE": "CRITICAL",
    "CHAMPION_ARTIFACT_MISMATCH": "CRITICAL",
    "SILENT_FINANCIAL_CORRUPTION": "CRITICAL",
    "LEARNING_PIPELINE_LOSS": "HIGH",
    "SHADOW_ISOLATION_FAILURE": "HIGH",
    "MIGRATION_FAILED": "HIGH",
    "MODEL_CONTRACT_MISMATCH": "HIGH",
    "VERSION_INCONSISTENCY": "HIGH",
    "TIMEBASE_DIVERGENCE": "HIGH",
    "DEAL_LOOKUP_FAILED": "HIGH",
    "MT5_CALL_FAILED": "HIGH",
    "OUTCOME_DISCARDED": "HIGH",
    "SILENT_EXCEPTION": "HIGH",
    "MAX_EXPOSURE_FALSE_BLOCK": "HIGH",
    "ACCOUNTING_IMBALANCE": "CRITICAL",
    "WORKER_STALLED": "MEDIUM",
    "FEATURE_SCHEMA_SHIFTED": "HIGH",
    "FEATURE_WRONG_INDEX": "HIGH",
}


@dataclass
class TelemetryEvent:
    """One raw observation to be correlated (spec 11/12)."""

    timestamp: datetime
    event_type: str  # e.g. MT5_CALL_FAILED, EXCEPTION, CACHE_STALE, UI_SYMPTOM
    component: str
    error_code: str = ""
    correlation_id: str = ""
    ticket: str = ""
    execution_id: str = ""
    severity: str | None = None  # explicit severity beats inference
    payload: dict[str, Any] = field(default_factory=dict)
    source: EventSource = EventSource.TELEMETRY


def _infer_severity(error_code: str, component: str) -> IncidentSeverity:
    sev = SEVERITY_BY_CODE.get(error_code.upper())
    if sev:
        return IncidentSeverity(sev)
    return IncidentSeverity.MEDIUM


def _infer_category(error_code: str, component: str) -> IncidentCategory:
    known = KNOWN_FAILURE_CLASSES.get(error_code.upper())
    if known:
        return IncidentCategory(known[0])
    # Infer from component name when it maps to a category; else OTHER.
    comp = (component or "").upper()
    mapping = {
        "MT5": IncidentCategory.MT5,
        "BROKER": IncidentCategory.MT5,
        "LEDGER": IncidentCategory.LEDGER,
        "ACCOUNT": IncidentCategory.ACCOUNTING,
        "EXPERIENCE": IncidentCategory.LEARNING,
        "LEARNING": IncidentCategory.LEARNING,
        "RESEARCH": IncidentCategory.RESEARCH,
        "MODEL": IncidentCategory.MODEL,
        "FEATURE": IncidentCategory.FEATURE,
        "NEWS": IncidentCategory.NEWS,
        "UI": IncidentCategory.UI,
        "WEB": IncidentCategory.UI,
        "API": IncidentCategory.API,
        "WORKER": IncidentCategory.WORKER,
        "GOVERNANCE": IncidentCategory.GOVERNANCE,
        "MIGRATION": IncidentCategory.MIGRATION,
        "UPDATE": IncidentCategory.VERSION,
        "VERSION": IncidentCategory.VERSION,
        "TELEGRAM": IncidentCategory.TELEGRAM,
        "EXPOSURE": IncidentCategory.EXPOSURE,
    }
    for key, cat in mapping.items():
        if key in comp:
            return cat
    return IncidentCategory.OTHER


def _chain_position(event_type: str) -> str:
    et = event_type.upper()
    for k, v in CHAIN_HINTS.items():
        if k in et:
            return v
    return "OBSERVATION"


@dataclass
class CorrelationResult:
    """Outcome of correlating a batch of telemetry events (spec 4)."""

    incidents: list[Incident] = field(default_factory=list)
    merged: int = 0
    new: int = 0
    unchanged: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "incidents": [i.as_dict() for i in self.incidents],
            "merged": self.merged,
            "new": self.new,
            "unchanged": self.unchanged,
        }


class IncidentCorrelator:
    """Groups raw events into canonical incidents.

    Usage (idempotent): ``correlate(events, existing)``; existing incidents
    are updated in place (mutated copies — the caller decides persistence).
    """

    def __init__(self, windows_sec: dict[str, float] | None = None) -> None:
        self.windows_sec = dict(windows_sec or DEFAULT_WINDOWS_SEC)
        # in-memory dedup ring: event_key -> last timestamp (bounded)
        self._recent_keys: dict[str, float] = {}
        self._ring_capacity = 20000

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def correlate(
        self,
        events: list[TelemetryEvent],
        existing: list[Incident] | None = None,
    ) -> CorrelationResult:
        """Correlates a batch of events against (optionally) existing incidents.

        Matching rules (in priority order):
          1. same fingerprint + window overlap  -> merge (dedupe, spec 31)
          2. same correlation_id (+ any window) -> correlate into one incident
          3. same ticket/execution identity     -> correlate
          4. otherwise                          -> new incident
        """
        existing_by_id = {i.incident_id: i for i in (existing or [])}
        existing_by_fp: dict[str, list[Incident]] = {}
        existing_by_corr: dict[str, list[Incident]] = {}
        existing_by_ticket: dict[str, list[Incident]] = {}
        for i in existing_by_id.values():
            if i.fingerprint:
                existing_by_fp.setdefault(i.fingerprint, []).append(i)
            if i.correlation_id:
                existing_by_corr.setdefault(i.correlation_id, []).append(i)
            for rec in i.affected_records:
                existing_by_ticket.setdefault(str(rec), []).append(i)

        result = CorrelationResult()
        touched: set[str] = set()

        for ev in sorted(events, key=lambda e: e.timestamp):
            fingerprint = incident_fingerprint(
                category=_infer_category(ev.error_code, ev.component).value,
                component=ev.component or "unknown",
                error_code=ev.error_code or ev.event_type,
            )
            target: Incident | None = None

            # 1. same correlation_id (strongest identity for user-visible errors)
            if ev.correlation_id and ev.correlation_id in existing_by_corr:
                candidates = sorted(
                    existing_by_corr[ev.correlation_id],
                    key=lambda i: i.first_seen_at,
                )
                target = candidates[0]

            # 2. same ticket/execution identity
            if target is None and ev.ticket and ev.ticket in existing_by_ticket:
                target = existing_by_ticket[ev.ticket][0]

            # 3. fingerprint + window overlap
            if target is None and fingerprint and fingerprint in existing_by_fp:
                for cand in existing_by_fp[fingerprint]:
                    window = self.windows_sec.get(cand.category.value, 300.0)
                    if (ev.timestamp - cand.last_seen_at).total_seconds() <= window:
                        target = cand
                        break

            if target is None:
                target = self._new_incident(ev, fingerprint)
                result.new += 1
                existing_by_id[target.incident_id] = target
                existing_by_fp.setdefault(fingerprint, []).append(target)
                if target.correlation_id:
                    existing_by_corr.setdefault(target.correlation_id, []).append(target)
                for rec in target.affected_records:
                    existing_by_ticket.setdefault(str(rec), []).append(target)
                result.incidents.append(target)
                touched.add(target.incident_id)
                continue  # creation already appended the first timeline event

            # dedupe check: identical event key within a short window
            # Uses EVENT time (spec 10: timelines are built from actual
            # timestamps, never wall-clock processing order).
            key = f"{target.incident_id}:{ev.event_type}:{ev.error_code}"
            ev_epoch = ev.timestamp.timestamp()
            if key in self._recent_keys and ev_epoch - self._recent_keys[key] < 5.0:
                result.unchanged += 1
                continue
            self._recent_keys[key] = ev_epoch
            self._trim_ring()

            self._merge_event(target, ev)
            touched.add(target.incident_id)

        # count merged (existing incidents that received new events)
        result.merged = len(touched & set(existing_by_id.keys()))
        return result

    def classify_chain(self, incident: Incident) -> tuple[CorrelationPattern, list[dict[str, str]]]:
        """Reconstructs the causal chain from the incident's timeline (spec 5).

        Returns (pattern, chain) where chain order follows: ROOT_EVENT ->
        PRIMARY_FAILURE -> STATE_CORRUPTION -> DOWNSTREAM_EFFECT ->
        USER_VISIBLE_SYMPTOM. Based on ACTUAL timeline timestamps only —
        no invented sequence (spec 10).
        """
        if not incident.timeline:
            return CorrelationPattern.SINGLE, []
        chain: dict[str, dict[str, Any]] = {}
        for ev in sorted(incident.timeline, key=lambda t: t.timestamp):
            pos = _chain_position(ev.event_type)
            chain.setdefault(
                pos,
                {
                    "position": pos,
                    "first_event_type": ev.event_type,
                    "first_timestamp": ev.timestamp.isoformat(),
                    "count": 0,
                    "correlation_ids": [],
                },
            )
            chain[pos]["count"] += 1
            if ev.correlation_id and ev.correlation_id not in chain[pos]["correlation_ids"]:
                chain[pos]["correlation_ids"].append(ev.correlation_id)
        ordered = [
            "ROOT_EVENT",
            "PRIMARY_FAILURE",
            "STATE_CORRUPTION",
            "DOWNSTREAM_EFFECT",
            "USER_VISIBLE_SYMPTOM",
        ]
        present = [chain[p] for p in ordered if p in chain]
        remaining = [v for k, v in chain.items() if k not in ordered]
        chain_list = present + remaining
        if len(chain_list) >= 4:
            pattern = CorrelationPattern.CHAIN
        elif len(chain_list) >= 2:
            pattern = CorrelationPattern.FAN_OUT
        else:
            pattern = CorrelationPattern.SINGLE
        return pattern, chain_list

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _trim_ring(self) -> None:
        if len(self._recent_keys) > self._ring_capacity:
            cutoff = time.time() - 600.0
            self._recent_keys = {k: t for k, t in self._recent_keys.items() if t >= cutoff}

    def _new_incident(self, ev: TelemetryEvent, fingerprint: str) -> Incident:
        category = _infer_category(ev.error_code, ev.component)
        severity = (
            IncidentSeverity(str(ev.severity).upper())
            if ev.severity and str(ev.severity).upper() in IncidentSeverity._value2member_map_
            else _infer_severity(ev.error_code, ev.component)
        )
        inc = Incident(
            severity=severity,
            category=category,
            component=ev.component or "unknown",
            operation=ev.event_type,
            correlation_id=ev.correlation_id,
            fingerprint=fingerprint,
            first_seen_at=ev.timestamp,
            last_seen_at=ev.timestamp,
            detected_at=ev.timestamp,
        )
        if ev.ticket:
            inc.affected_records.append(str(ev.ticket))
        inc.add_timeline_event(
            TimelineEvent(
                timestamp=ev.timestamp,
                event_type=ev.event_type,
                source=ev.source,
                payload=dict(ev.payload),
                correlation_id=ev.correlation_id,
            )
        )
        return inc

    def _merge_event(self, inc: Incident, ev: TelemetryEvent) -> None:
        inc.mark_seen(ev.timestamp)
        inc.repeated_count += 1
        if ev.ticket and str(ev.ticket) not in inc.affected_records:
            inc.affected_records.append(str(ev.ticket))
        if ev.correlation_id and not inc.correlation_id:
            inc.correlation_id = ev.correlation_id
        # severity can only escalate (evidence-driven)
        sev = (
            IncidentSeverity(str(ev.severity).upper())
            if ev.severity and str(ev.severity).upper() in IncidentSeverity._value2member_map_
            else _infer_severity(ev.error_code, ev.component)
        )
        if sev.rank > inc.severity.rank:
            inc.severity = sev
        inc.add_timeline_event(
            TimelineEvent(
                timestamp=ev.timestamp,
                event_type=ev.event_type,
                source=ev.source,
                payload=dict(ev.payload),
                correlation_id=ev.correlation_id,
            )
        )


__all__ = [
    "DEFAULT_WINDOWS_SEC",
    "KNOWN_FAILURE_CLASSES",
    "SEVERITY_BY_CODE",
    "CorrelationResult",
    "IncidentCorrelator",
    "TelemetryEvent",
    "incident_fingerprint",
]
