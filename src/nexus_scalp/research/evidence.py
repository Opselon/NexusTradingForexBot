"""
Research Observability & Evidence Domain Models
================================================
TASK-21-RESEARCH-OBSERVABILITY (2026-08-20).

The Strategy Research & Validation Engine must be forensically understandable:
for every strategy, every gate, every research run and every artifact, the
system must answer WHERE it is stuck, WHY, WHAT evidence exists, WHICH
configuration produced it, and whether the whole run can be replayed.

This module defines the first-class observability entities:

    ResearchGate           one evaluation step of one run (STATIC_VALIDATION,
                           BACKTEST, WALK_FORWARD, OOS, ROBUSTNESS, SCORING)
                           with explicit status + reason + evidence link.
    ResearchRunSnapshot    immutable reproducibility fingerprint captured at
                           run start (hashes, versions, seed, config).
    ResearchEvent          one entry in the persisted gate timeline.
    ResearchAudit          one evidence-vault record (immutable artifact).
    OutcomeLineage         per-outcome source attribution (NONE vs BROKER_DEALS
                           vs derived) so "NONE=44" is never ambiguous.

Gate statuses are explicit: PENDING / QUEUED / RUNNING / PASSED / FAILED /
SKIPPED / BLOCKED / ERROR / CANCELLED. Run statuses: QUEUED / RUNNING /
COMPLETED / FAILED / CANCELLED / BLOCKED.

Invariant discipline:
  * `VALIDATED` requires BACKTEST, WALK_FORWARD, OOS, ROBUSTNESS all PASSED
    plus SCORING PASSED plus a closed evidence artifact per gate.
  * `REJECTED` requires at least one gate FAILED or a terminal research failure
    (never the default for unprocessed candidates).
  * A research run is IMMUTABLE once completed: new runs never overwrite prior
    runs (research_runs stays append-only).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class GateType(StrEnum):
    """The supported research gates (spec 5)."""

    STATIC_VALIDATION = "STATIC_VALIDATION"
    BACKTEST = "BACKTEST"
    WALK_FORWARD = "WALK_FORWARD"
    OOS = "OOS"
    ROBUSTNESS = "ROBUSTNESS"
    SCORING = "SCORING"


class GateStatus(StrEnum):
    """Explicit state for every gate (spec 6)."""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class RunStatus(StrEnum):
    """One validation attempt's status (spec 8 / 61)."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class RunOutcome(StrEnum):
    """Research outcome of a completed run (validated / rejected / inconclusive)."""

    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class WorkerHealth(StrEnum):
    """Worker heartbeat classification (spec 30)."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STUCK = "STUCK"
    FAILED = "FAILED"
    IDLE = "IDLE"
    UNKNOWN = "UNKNOWN"


class FailureClass(StrEnum):
    """Distinguishes transient/technical from statistical/research failures (spec 60)."""

    TECHNICAL = "TECHNICAL"  # infra: timeout, provider failure, restart
    RESEARCH = "RESEARCH"  # statistical: gate FAIL, threshold not met
    DATA = "DATA"  # missing dataset/outcome/schema -> BLOCKED
    UNKNOWN = "UNKNOWN"


class EvidenceKind(StrEnum):
    """Immutable evidence-vault artifact kinds (spec 44)."""

    BACKTEST_RESULT = "BACKTEST_RESULT"
    WALK_FORWARD_RESULT = "WALK_FORWARD_RESULT"
    OOS_RESULT = "OOS_RESULT"
    ROBUSTNESS_RESULT = "ROBUSTNESS_RESULT"
    SCORE_RESULT = "SCORE_RESULT"
    SNAPSHOT = "SNAPSHOT"
    EVENT = "EVENT"


GATE_CHAIN: tuple[GateType, ...] = (
    GateType.STATIC_VALIDATION,
    GateType.BACKTEST,
    GateType.WALK_FORWARD,
    GateType.OOS,
    GateType.ROBUSTNESS,
    GateType.SCORING,
)

#: Gates required before a strategy may be marked VALIDATED (spec 56).
REQUIRED_GATES_FOR_VALIDATION: frozenset[GateType] = frozenset(
    {
        GateType.BACKTEST,
        GateType.WALK_FORWARD,
        GateType.OOS,
        GateType.ROBUSTNESS,
        GateType.SCORING,
    }
)

#: Terminal (non-retryable) gate statuses for a run.
_TERMINAL_GATE: frozenset[GateStatus] = frozenset(
    {GateStatus.PASSED, GateStatus.FAILED, GateStatus.CANCELLED}
)

#: Gate statuses that count as a hard research failure (statistical).
_RESEARCH_FAIL: frozenset[GateStatus] = frozenset({GateStatus.FAILED})


def is_research_failure(status: GateStatus) -> bool:
    """True when the gate failed for statistical reasons (not infra)."""
    return status in _RESEARCH_FAIL


def is_terminal_gate(status: GateStatus) -> bool:
    return status in _TERMINAL_GATE


def next_gate(current: GateType) -> GateType | None:
    """Returns the gate after `current` in the canonical chain, or None."""
    try:
        idx = GATE_CHAIN.index(current)
    except ValueError:
        return None
    return GATE_CHAIN[idx + 1] if idx + 1 < len(GATE_CHAIN) else None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


def stable_digest(payload: Any) -> str:
    """Deterministic sha256 of a JSON-safe payload (research hash, spec 64)."""
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class ResearchGate(BaseModel):
    """First-class gate entity (spec 5 / 6)."""

    model_config = ConfigDict(frozen=True)

    gate_id: str = Field(...)
    strategy_id: str = Field(...)
    research_run_id: str = Field(...)
    gate_type: GateType = Field(...)
    status: GateStatus = Field(default=GateStatus.PENDING)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    duration_ms: float = Field(default=0.0, ge=0.0)
    configuration_version: str = Field(default="")
    dataset_version: str = Field(default="")
    engine_version: str = Field(default="")
    result: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str = Field(default="")
    failure_class: FailureClass = Field(default=FailureClass.UNKNOWN)
    evidence_id: str = Field(default="")
    retryable: bool = Field(default=False)
    order_index: int = Field(default=0)

    @field_validator("started_at", "completed_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @property
    def is_terminal(self) -> bool:
        return is_terminal_gate(self.status)

    @property
    def is_failed(self) -> bool:
        return self.status in (GateStatus.FAILED, GateStatus.ERROR)


class ResearchRunSnapshot(BaseModel):
    """Immutable reproducibility fingerprint captured at run start (spec 9 / 45)."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(...)
    strategy_definition_hash: str = Field(...)
    strategy_configuration: dict[str, Any] = Field(default_factory=dict)
    dataset_version: str = Field(default="")
    dataset_hash: str = Field(default="")
    feature_schema_version: str = Field(default="")
    model_version: str = Field(default="")
    model_hash: str = Field(default="")
    rule_matrix_version: str = Field(default="")
    runtime_configuration_version: str = Field(default="")
    backtest_engine_version: str = Field(default="")
    validation_engine_version: str = Field(default="")
    random_seed: int | None = Field(default=None)
    research_prompt_version: str = Field(default="")
    engine_version: str = Field(default="")
    configuration_hash: str = Field(default="")
    captured_at: datetime = Field(default_factory=_utc_now)

    @field_validator("captured_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    def fingerprint(self) -> str:
        """Deterministic research hash over the whole snapshot (spec 64)."""
        return stable_digest(
            {
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "strategy_definition_hash": self.strategy_definition_hash,
                "dataset_version": self.dataset_version,
                "dataset_hash": self.dataset_hash,
                "feature_schema_version": self.feature_schema_version,
                "model_version": self.model_version,
                "model_hash": self.model_hash,
                "random_seed": self.random_seed,
                "backtest_engine_version": self.backtest_engine_version,
                "validation_engine_version": self.validation_engine_version,
                "configuration_hash": self.configuration_hash,
            }
        )


def build_run_snapshot(
    strategy_id: str,
    strategy_version: str,
    strategy_definition: dict[str, Any],
    dataset: Any,
    *,
    engine_version: str = "nexus-research-observability-v1",
    configuration: dict[str, Any] | None = None,
    random_seed: int | None = None,
) -> ResearchRunSnapshot:
    """
    Captures the reproducibility snapshot for one run from the live candidate
    definition + the actual dataset artifact used. Never fabricated: fields
    absent from the inputs stay empty strings (consumers render NOT_RECORDED).
    """
    dataset_version = getattr(dataset, "dataset_id", "") or ""
    dataset_hash = ""
    if dataset is not None:
        try:
            samples = getattr(dataset, "samples", []) or []
            keys = sorted(getattr(s, "idempotency_key", "") for s in samples)
            dataset_hash = stable_digest({"dataset_id": dataset_version, "sample_keys": keys})
        except Exception:
            dataset_hash = ""

    config = configuration or {}
    return ResearchRunSnapshot(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        strategy_definition_hash=stable_digest(strategy_definition or {}),
        strategy_configuration=_sort(config.get("strategy_configuration", {})),
        dataset_version=dataset_version,
        dataset_hash=dataset_hash,
        feature_schema_version=str(config.get("feature_schema_version", "")),
        model_version=str(config.get("model_version", "")),
        model_hash=str(config.get("model_hash", "")),
        rule_matrix_version=str(config.get("rule_matrix_version", "")),
        runtime_configuration_version=str(config.get("runtime_configuration_version", "")),
        backtest_engine_version=str(config.get("backtest_engine_version", engine_version)),
        validation_engine_version=str(config.get("validation_engine_version", engine_version)),
        random_seed=random_seed,
        research_prompt_version=str(config.get("research_prompt_version", "")),
        engine_version=engine_version,
        configuration_hash=stable_digest(config or {}),
    )


def _sort(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sort(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort(x) for x in obj]
    return obj


class ResearchEvent(BaseModel):
    """One entry in the persisted gate timeline (spec 11, real persisted events)."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(...)
    strategy_id: str = Field(...)
    research_run_id: str = Field(...)
    gate_id: str = Field(default="")
    event_type: str = Field(...)  # RESEARCH_RUN_STARTED / GATE_STARTED / ...
    message: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_utc_now)

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class EvidenceArtifact(BaseModel):
    """One immutable evidence-vault record (spec 44)."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(...)
    strategy_id: str = Field(...)
    research_run_id: str = Field(...)
    gate_id: str = Field(default="")
    kind: EvidenceKind = Field(...)
    content: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = Field(...)
    dataset_version: str = Field(default="")
    engine_version: str = Field(default="")
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @classmethod
    def create(
        cls,
        strategy_id: str,
        research_run_id: str,
        kind: EvidenceKind,
        content: dict[str, Any],
        *,
        gate_id: str = "",
        dataset_version: str = "",
        engine_version: str = "",
        evidence_id: str | None = None,
    ) -> EvidenceArtifact:
        """Creates an artifact with a deterministic content hash."""
        cid = evidence_id or f"EV-{stable_digest(content)[:12].upper()}"
        return cls(
            evidence_id=cid,
            strategy_id=strategy_id,
            research_run_id=research_run_id,
            gate_id=gate_id,
            kind=kind,
            content=_sort(content),
            content_hash=stable_digest(content),
            dataset_version=dataset_version,
            engine_version=engine_version,
        )


class OutcomeLineage(BaseModel):
    """
    Per-outcome evidence attribution (spec 34/35): where did the R multiple
    come from? NONE means the outcome row carries NO reconstruction source —
    an unattributed/legacy row, never a derived one. Derived rows carry
    `BROKER_DEALS` / `BROKER_DEALS_AGGREGATED` / `RECONSTRUCTED` explicitly.
    """

    model_config = ConfigDict(frozen=True)

    outcome_key: str = Field(...)
    source: str = Field(default="NONE")
    evidence_ref: str = Field(default="")
    broker_trade_id: str = Field(default="")
    reconstructed: bool = Field(default=False)
    repair_state: str = Field(
        default="UNTOUCHED"
    )  # UNTOUCHED/REPAIRED/ALREADY_VALID/NO_BROKER/AMBIGUOUS/FAILED
