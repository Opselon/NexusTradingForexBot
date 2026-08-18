"""
Model Governance Domain Models
===============================
TASK-6 / CHG-0003: canonical, versioned contracts for the live model
governance boundary:

  * GovernanceEvent     — append-only lifecycle/error audit row
  * LoadGateResult      — deterministic 10-step model load gate verdict
  * RegistryModel       — one truthful registry answer (CURRENT_CHAMPION /
                          CURRENT_CHALLENGER / SHADOW / PENDING_APPROVAL /
                          RETIRED / FAILED)
  * PromotionState      — the explicit promotion state machine
  * PromotionTransition — audited state transition (actor/reason/evidence)
  * CalibrationBucket   — live calibration buckets (0.0-1.0, width 0.1)
  * DriftAlert          — bounded distribution-drift signal
  * ShadowParity        — same-input parity evidence (feature/news/schema)

SAFETY CONTRACT
---------------
This package imports NO adapter, NO order manager, NO risk engine and NO
execution object. It is observability + governance only. A governance bug
can never place, modify or close a trade and can never bypass RiskEngine or
OrderManager (INV-002 / INV-003 / INV-004).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class GovernanceErrorCode(StrEnum):
    """Bounded taxonomy of governance failures (spec 30)."""

    MODEL_LOAD_REJECTED = "MODEL_LOAD_REJECTED"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    SCALER_MISMATCH = "SCALER_MISMATCH"
    FEATURE_PARITY_FAILURE = "FEATURE_PARITY_FAILURE"
    NEWS_PARITY_FAILURE = "NEWS_PARITY_FAILURE"
    SHADOW_TIMEOUT = "SHADOW_TIMEOUT"
    SHADOW_QUEUE_FULL = "SHADOW_QUEUE_FULL"
    PREDICTION_INVALID = "PREDICTION_INVALID"
    ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"
    LIVE_DRIFT = "LIVE_DRIFT"
    PROMOTION_BLOCKED = "PROMOTION_BLOCKED"
    ROLLBACK_EXECUTED = "ROLLBACK_EXECUTED"
    PROMOTION_EXECUTED = "PROMOTION_EXECUTED"
    REGISTRY_RECONCILED = "REGISTRY_RECONCILED"


class GovernanceStage(StrEnum):
    """Stage labels used in every governance event row."""

    LOAD_GATE = "LOAD_GATE"
    REGISTRY = "REGISTRY"
    SHADOW = "SHADOW"
    PARITY = "PARITY"
    OUTCOME = "OUTCOME"
    CALIBRATION = "CALIBRATION"
    DRIFT = "DRIFT"
    PROMOTION = "PROMOTION"
    ROLLBACK = "ROLLBACK"
    HEALTH = "HEALTH"
    TELEGRAM = "TELEGRAM"


class GovernanceEvent(BaseModel):
    """One append-only governance record (spec 30 / 31)."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(...)
    event: str = Field(...)  # e.g. MODEL_LOAD_REJECTED, ROLLBACK_EXECUTED
    stage: GovernanceStage = Field(...)
    model_id: str = Field(default="")
    model_version: str = Field(default="")
    schema_id: str = Field(default="")
    correlation_id: str = Field(default="")
    error_code: str = Field(default="")
    error_type: str = Field(default="")
    duration_ms: float = Field(default=0.0, ge=0.0)
    actor: str = Field(default="system")  # operator / system / api:<endpoint>
    previous_state: str = Field(default="")
    new_state: str = Field(default="")
    reason: str = Field(default="")
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _utc(v)


class LoadGateStep(StrEnum):
    """The 10 deterministic gates of the model load gate (spec 4)."""

    ARTIFACT_EXISTS = "ARTIFACT_EXISTS"
    HASH_VALID = "HASH_VALID"
    MANIFEST_VALID = "MANIFEST_VALID"
    SCHEMA_VALID = "SCHEMA_VALID"
    INPUT_DIMENSION_VALID = "INPUT_DIMENSION_VALID"
    SCALER_VALID = "SCALER_VALID"
    LABEL_SCHEMA_VALID = "LABEL_SCHEMA_VALID"
    VALIDATION_STATUS_VALID = "VALIDATION_STATUS_VALID"
    LIFECYCLE_ALLOWS_SHADOW = "LIFECYCLE_ALLOWS_SHADOW"
    LOAD = "LOAD"


class LoadGateResult(BaseModel):
    """Deterministic load-gate verdict; never silently falls back."""

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(...)
    model_version: str = Field(default="")
    passed: bool = Field(default=False)
    failing_gate: LoadGateStep | None = Field(default=None)
    details: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_code: GovernanceErrorCode = Field(default=GovernanceErrorCode.MODEL_LOAD_REJECTED)

    @field_validator("checked_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _utc(v)


class RegistryCategory(StrEnum):
    """The six questions the runtime registry MUST answer (spec 3)."""

    CURRENT_CHAMPION = "CURRENT_CHAMPION"
    CURRENT_CHALLENGER = "CURRENT_CHALLENGER"
    SHADOW = "SHADOW"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


class RegistryModel(BaseModel):
    """Full metadata for one registered model (spec 3)."""

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(...)
    version: str = Field(default="")
    architecture: str = Field(default="")
    schema_id: str = Field(default="")
    input_dimension: int = Field(default=0, ge=0)
    scaler_hash: str = Field(default="")
    artifact_hash: str = Field(default="")
    manifest_hash: str = Field(default="")
    validation_result: str = Field(default="")
    oos_result: str = Field(default="")
    robustness_result: str = Field(default="")
    registration_timestamp: str = Field(default="")
    source_commit: str = Field(default="")
    lifecycle_state: str = Field(default="")
    artifact_path: str = Field(default="")
    category: RegistryCategory = Field(default=RegistryCategory.SHADOW)


class PromotionState(StrEnum):
    """Explicit state machine (spec 21). Never auto-transitions."""

    RESEARCH = "RESEARCH"
    VALIDATED = "VALIDATED"
    CHALLENGER = "CHALLENGER"
    SHADOW = "SHADOW"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    CHAMPION = "CHAMPION"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


#: Legal transitions (spec 21). ANY state may go REJECTED / RETIRED.
PROMOTION_TRANSITIONS: dict[PromotionState, set[PromotionState]] = {
    PromotionState.RESEARCH: {
        PromotionState.VALIDATED,
        PromotionState.REJECTED,
        PromotionState.RETIRED,
    },
    PromotionState.VALIDATED: {
        PromotionState.CHALLENGER,
        PromotionState.REJECTED,
        PromotionState.RETIRED,
    },
    PromotionState.CHALLENGER: {
        PromotionState.SHADOW,
        PromotionState.REJECTED,
        PromotionState.RETIRED,
    },
    PromotionState.SHADOW: {
        PromotionState.READY_FOR_REVIEW,
        PromotionState.REJECTED,
        PromotionState.RETIRED,
    },
    PromotionState.READY_FOR_REVIEW: {
        PromotionState.APPROVED,
        PromotionState.SHADOW,
        PromotionState.REJECTED,
        PromotionState.RETIRED,
    },
    PromotionState.APPROVED: {
        PromotionState.CHAMPION,
        PromotionState.REJECTED,
        PromotionState.RETIRED,
    },
    PromotionState.CHAMPION: {
        PromotionState.RETIRED
    },  # champions retire; they are never demoted silently
    PromotionState.REJECTED: {PromotionState.RESEARCH},  # explicit re-entry only
    PromotionState.RETIRED: set(),
}

#: The promotion checklist (spec 22): a Challenger can only reach
#: READY_FOR_REVIEW when EVERY item passes.
PROMOTION_CHECKLIST: tuple[str, ...] = (
    "valid artifact",
    "valid manifest",
    "valid schema",
    "valid scaler",
    "OOS pass",
    "robustness pass",
    "calibration acceptable",
    "no severe class collapse",
    "no severe feature drift",
    "live shadow sample floor reached",
    "live shadow evidence acceptable",
    "latency acceptable",
    "no critical anomalies",
    "rollback target exists",
)


class PromotionTransition(BaseModel):
    """Audited lifecycle transition (spec 31). Immutable."""

    model_config = ConfigDict(frozen=True)

    transition_id: str = Field(...)
    model_id: str = Field(...)
    model_version: str = Field(default="")
    previous_state: PromotionState = Field(...)
    new_state: PromotionState = Field(...)
    actor: str = Field(default="system")
    reason: str = Field(default="")
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)
    source_commit: str = Field(default="")
    artifact_hash: str = Field(default="")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _utc(v)


class CalibrationBucket(BaseModel):
    """One live calibration bucket [lo, hi) (spec 19)."""

    model_config = ConfigDict(frozen=True)

    lo: float = Field(..., ge=0.0, le=1.0)
    hi: float = Field(..., ge=0.0, le=1.0)
    predictions: int = Field(default=0, ge=0)
    correct: int = Field(default=0, ge=0)
    incorrect: int = Field(default=0, ge=0)
    accuracy: float = Field(default=0.0, ge=0.0, le=1.0)
    mean_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def label(self) -> str:
        return f"{self.lo:0.1f}-{self.hi:0.1f}"


class DriftAlert(BaseModel):
    """One drift signal (spec 18). Alerts only; never auto-retrains."""

    model_config = ConfigDict(frozen=True)

    alert_id: str = Field(...)
    model_id: str = Field(default="")
    kind: str = Field(...)  # PROBABILITY | ACTION | FEATURE | NEWS
    metric: str = Field(default="")
    value: float = Field(default=0.0)
    threshold: float = Field(default=0.0)
    severity: str = Field(default="WARN")  # WARN | CRITICAL
    window_samples: int = Field(default=0, ge=0)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("detected_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _utc(v)


class ShadowParity(BaseModel):
    """Same-input parity evidence for one comparison (spec 6 / 7 / 8)."""

    model_config = ConfigDict(frozen=True)

    comparison_id: str = Field(...)
    timestamp: datetime = Field(...)
    feature_context_id: str = Field(default="")
    news_context_id: str = Field(default="")
    feature_schema_id: str = Field(default="")
    champion_input_dim: int = Field(default=0, ge=0)
    challenger_input_dim: int = Field(default=0, ge=0)
    # Feature parity vs the offline/replay reference (spec 6)
    max_abs_diff: float = Field(default=0.0, ge=0.0)
    mean_abs_diff: float = Field(default=0.0, ge=0.0)
    mismatch_count: int = Field(default=0, ge=0)
    parity_ok: bool = Field(default=True)
    alignment: str = Field(default="IDENTICAL")  # IDENTICAL | NEWS_EXTENDED | NONE
    latency_champion_ms: float = Field(default=0.0, ge=0.0)
    latency_challenger_ms: float = Field(default=0.0, ge=0.0)

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _utc(v)
