"""
Model Lifecycle / Challenger Engine Domain Models
=================================================
PHASE 10 immutable contracts for controlled model training, Champion/Challenger
management, and the training run lineage.

Design invariants (spec 4 / 5 / 12 / 27):
1. MODEL-SEPARATED MEMORY - the model artifact is NOT the experience memory.
   Historical learning survives model deletion/rebuilds.
2. IMMUTABLE TRAINING RUNS - every training execution is one immutable record
   capturing dataset, schema, hyperparameters, ranges, parent champion,
   artifact, metrics and final status. No anonymous model files.
3. CHAMPION / CHALLENGER - the Champion is the production-authorized model; a
   Challenger is a trained candidate with NO production authority. Challengers
   can never place orders or reach production inference by accident.
4. FEATURE-SCHEMA AWARE - every record carries `feature_schema_id` +
   `feature_dimension` (50D today; 60D/350D forward-compatible). A schema
   mismatch must fail explicitly, never silently reshape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexus_scalp.experience.models import CANONICAL_FEATURE_DIMENSION, CANONICAL_FEATURE_SCHEMA_ID


class ModelStatus(StrEnum):
    """Lifecycle states of a model registry entry (spec 27)."""

    CANDIDATE = "CANDIDATE"  # trained, not yet validated
    CHALLENGER = "CHALLENGER"  # validated, shadow-eligible, no production authority
    CHAMPION = "CHAMPION"  # production-authorized
    REJECTED = "REJECTED"  # failed a gate
    ARCHIVED = "ARCHIVED"  # superseded, history preserved
    INVALID = "INVALID"  # integrity failure / corrupted artifact


class TrainingRunStatus(StrEnum):
    """Status of one training execution."""

    STARTED = "STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INCOMPLETE = "INCOMPLETE"  # interrupted; never VALIDATED


class TrainingDatasetRow(BaseModel):
    """
    One deterministic training sample.

    Preserves sample identity, decision timestamp, feature schema + vector,
    label, strategy context, regime, symbol, timeframe, session and provenance
    back to the source experience (spec 7).
    """

    model_config = ConfigDict(frozen=True)

    sample_id: str = Field(...)
    experience_id: str = Field(...)
    idempotency_key: str = Field(...)
    decision_timestamp: datetime = Field(...)
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)
    feature_vector: list[float] = Field(default_factory=list)
    label: int = Field(..., ge=0, description="Encoded training label (0=NO_TRADE,1=BUY,2=SELL)")
    label_str: str = Field(default="")
    strategy_id: str = Field(default="")
    strategy_version: str = Field(default="")
    regime: str = Field(default="UNKNOWN")
    symbol: str = Field(default="")
    timeframe: str = Field(default="M1")
    session: str = Field(default="ALL")
    sample_weight: float = Field(default=1.0, ge=0.0)
    outcome_r: float = Field(default=0.0)
    is_executed: bool = Field(default=False)
    is_closed: bool = Field(default=False)
    exit_reason: str = Field(default="")

    @field_validator("decision_timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    def validate_schema(self) -> None:
        """Fails loudly when the feature vector width mismatches the schema."""
        if len(self.feature_vector) != self.feature_dimension:
            raise ValueError(
                f"Training sample {self.sample_id} schema mismatch: "
                f"{self.feature_schema_id} expects {self.feature_dimension}, "
                f"got {len(self.feature_vector)} features"
            )


class TrainingDataset(BaseModel):
    """Deterministic training dataset artifact (spec 7 / 13)."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(...)
    dataset_version: str = Field(default="1.0.0")
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    rows: list[TrainingDatasetRow] = Field(default_factory=list)
    source_experience_ids: list[str] = Field(default_factory=list)
    source_range: dict[str, str] = Field(default_factory=dict)
    config_hash: str = Field(default="", description="Deterministic identity input")

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @property
    def sample_count(self) -> int:
        return len(self.rows)

    def label_distribution(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows:
            out[str(r.label)] = out.get(str(r.label), 0) + 1
        return out

    def ordered_rows(self) -> list[TrainingDatasetRow]:
        return sorted(self.rows, key=lambda r: r.decision_timestamp)


class GateResult(BaseModel):
    """One validation-gate outcome (spec 20). A mandatory gate failure rejects."""

    model_config = ConfigDict(frozen=True)

    gate: str = Field(...)
    passed: bool = Field(...)
    details: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="")


class ModelArtifactInfo(BaseModel):
    """Artifact integrity + provenance (spec 28)."""

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(...)
    model_version: str = Field(...)
    artifact_path: str = Field(...)
    artifact_hash: str = Field(default="")
    artifact_bytes: int = Field(default=0)
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)
    num_classes: int = Field(default=4, gt=0)
    architecture: str = Field(default="scalp_net")
    scaler_path: str = Field(default="")
    scaler_hash: str = Field(default="")
    integrity_ok: bool = Field(default=False)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # --- AI Hub tensor diagnostics (populated by the integrity inspector) ---
    actual_input_dimension: int | None = Field(default=None)
    actual_output_classes: int | None = Field(default=None)
    actual_hidden_dimension: int | None = Field(default=None)
    class_head_name: str = Field(default="classifier.weight")
    scaler_dimension: int | None = Field(default=None)
    integrity_reason: str = Field(default="")

    @field_validator("checked_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class TrainingRun(BaseModel):
    """
    Immutable record of one controlled training execution (spec 12 / 34).

    Captures full lineage: dataset, schema, hyperparameters, seed, training /
    validation / OOS ranges, embargo parameters, parent champion, artifact,
    metrics and final status. Rebuildable derived summaries never modify this.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(...)
    dataset_id: str = Field(...)
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)

    model_id: str = Field(default="")
    model_version: str = Field(default="")
    parent_champion_id: str = Field(default="")
    parent_champion_version: str = Field(default="")

    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    random_seed: int = Field(default=42)
    architecture: str = Field(default="scalp_net")

    train_range: dict[str, str] = Field(default_factory=dict)
    validation_range: dict[str, str] = Field(default_factory=dict)
    oos_range: dict[str, str] = Field(default_factory=dict)
    embargo_bars: int = Field(default=15)
    purge_bars: int = Field(default=15)

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = Field(default=None)

    artifacts: list[ModelArtifactInfo] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    gates: list[GateResult] = Field(default_factory=list)

    status: TrainingRunStatus = Field(default=TrainingRunStatus.STARTED)
    failure_reason: str = Field(default="")
    build_identity: str = Field(default="")

    @field_validator("started_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @property
    def all_gates_passed(self) -> bool:
        return bool(self.gates) and all(g.passed for g in self.gates)

    @property
    def eligible_as_challenger(self) -> bool:
        """A run is Challenger-eligible only when COMPLETED and every gate passed."""
        return self.status == TrainingRunStatus.COMPLETED and self.all_gates_passed


class ChampionChallengerComparison(BaseModel):
    """Structured Champion vs Challenger comparison (spec 19)."""

    model_config = ConfigDict(frozen=True)

    candidate_model_id: str = Field(...)
    candidate_version: str = Field(...)
    champion_model_id: str = Field(...)
    champion_version: str = Field(...)
    run_id: str = Field(...)

    # Per-metric champion / challenger / delta
    expectancy_r: dict[str, float] = Field(default_factory=dict)
    max_drawdown_r: dict[str, float] = Field(default_factory=dict)
    oos_expectancy_r: dict[str, float] = Field(default_factory=dict)
    calibration_score: dict[str, float] = Field(default_factory=dict)
    robustness_status: dict[str, str] = Field(default_factory=dict)
    stability: dict[str, float] = Field(default_factory=dict)

    improvement_score: float = Field(default=0.0, ge=0.0, le=1.0)
    eligible: bool = Field(default=False)
    reasons: list[str] = Field(default_factory=list)

    compared_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("compared_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
