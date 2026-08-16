"""Model Generation Migration — Domain Contracts (PHASE 13).

ARTIFACT-FIRST MODEL FACTORY.

The center of the ML system is the MODEL ARTIFACT, not `model.py`, not a
database row. A model artifact must be independently loadable and fully
self-describing.

This module defines the explicit contracts:

    * LabelSchema         — 3-class neural target contract (NO_TRADE / BUY /
                            SELL); WAIT is a POLICY state, NOT a training
                            target.
    * NewsContextSchema   — normalized, deterministic, versioned numerical
                            news context consumed by the model layer.
    * Sample / Setup / Strategy — three independently-versionable concepts.
    * ModelManifest       — "exactly what produced this model?"
    * DatasetManifest     — "exactly what produced this dataset?"
    * ExperimentConfig    — bounded, explainable experiment space.

All contracts are immutable (Pydantic frozen) and never silently reinterpret
historical schemas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# =============================================================================
# LABEL SCHEMA (spec 2 / 42)
# =============================================================================


class NeuralLabel(StrEnum):
    """The 3-class neural target space. WAIT is derived in policy, never
    a neural target (the legacy 4th logit is a policy bridge, not a label)."""

    NO_TRADE = "NO_TRADE"
    BUY_MARKET = "BUY_MARKET"
    SELL_MARKET = "SELL_MARKET"


LABEL_SCHEMA_3CLASS_V1: dict[str, Any] = {
    "label_schema_id": "triple_barrier_3class_v1",
    "version": "1.0.0",
    "class_count": 3,
    "class_names": ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"],
    "numeric_mapping": {"NO_TRADE": 0, "BUY_MARKET": 1, "SELL_MARKET": 2},
    "generation": {
        "method": "triple_barrier",
        "take_profit_atr_mult": 1.1,
        "stop_loss_atr_mult": 1.0,
        "max_holding_bars": 15,
        "friction_usd": 0.35,
        "embargo_bars": 3,
        "no_trade_stride_bars": 3,
        "max_allowed_mae_ratio": 0.75,
        "min_valid_atr": 0.20,
    },
}


class LabelSchema(BaseModel):
    """Explicit neural label contract (spec 2)."""

    model_config = ConfigDict(frozen=True)

    label_schema_id: str = Field(...)
    version: str = Field(default="1.0.0")
    class_count: int = Field(default=3, ge=2)
    class_names: list[str] = Field(default_factory=list)
    numeric_mapping: dict[str, int] = Field(default_factory=dict)
    generation: dict[str, Any] = Field(default_factory=dict)

    @field_validator("class_names")
    @classmethod
    def _names_len(cls, v: list[str]) -> list[str]:
        return v

    def encode(self, label: str) -> int:
        if label not in self.numeric_mapping:
            raise ValueError(
                f"Label {label!r} not in schema {self.label_schema_id} "
                f"(allowed: {list(self.numeric_mapping)})"
            )
        return self.numeric_mapping[label]

    def decode(self, value: int) -> str:
        for name, num in self.numeric_mapping.items():
            if num == value:
                return name
        raise ValueError(f"Label value {value} not in schema {self.label_schema_id}")

    def validate_labels(self, labels: list[int]) -> None:
        allowed = set(self.numeric_mapping.values())
        for lbl in labels:
            if lbl not in allowed:
                raise ValueError(
                    f"Label {lbl} violates schema {self.label_schema_id} "
                    f"(allowed values: {sorted(allowed)})"
                )


def default_label_schema() -> LabelSchema:
    """Returns the canonical 3-class label contract."""
    return LabelSchema(
        label_schema_id=str(LABEL_SCHEMA_3CLASS_V1["label_schema_id"]),
        version=str(LABEL_SCHEMA_3CLASS_V1["version"]),
        class_count=int(LABEL_SCHEMA_3CLASS_V1["class_count"]),
        class_names=list(LABEL_SCHEMA_3CLASS_V1["class_names"]),
        numeric_mapping=dict(LABEL_SCHEMA_3CLASS_V1["numeric_mapping"]),
        generation=dict(LABEL_SCHEMA_3CLASS_V1["generation"]),
    )


# =============================================================================
# NEWS CONTEXT SCHEMA (spec 10 / 11 / 39)
# =============================================================================


class NewsContextSchema(BaseModel):
    """Versioned, deterministic numerical news context for the model layer.

    Fields are the NORMALIZED context the model may consume — never the raw
    article body. Each sample retains its own news context snapshot so a
    current NewsState can never contaminate historical samples.
    """

    model_config = ConfigDict(frozen=True)

    news_context_schema_id: str = Field(default="news_context_v1")
    news_context_version: str = Field(default="1.0.0")
    fields: list[str] = Field(
        default_factory=lambda: [
            "active_high_impact_events",
            "xauusd_relevance",
            "usd_relevance",
            "bullish_pressure",
            "bearish_pressure",
            "conflict_score",
            "novelty",
            "freshness",
            "confidence",
            "source_consensus",
            "news_state",
            "time_since_event_sec",
        ]
    )
    dimension: int = Field(default=12, ge=0)

    def vectorize(self, context: dict[str, float]) -> list[float]:
        """Maps a news context dict to the fixed-order numeric vector.

        Unknown/missing fields default to 0.0 so a disabled news engine
        still yields a well-formed zero vector (news_enabled=false ablation).
        """
        out: list[float] = []
        for f in self.fields:
            v = context.get(f, 0.0)
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                out.append(0.0)
        return out


def default_news_context_schema() -> NewsContextSchema:
    return NewsContextSchema()


# =============================================================================
# SAMPLE / SETUP / STRATEGY (spec 9)
# =============================================================================


class SampleContract(BaseModel):
    """One market observation — observable state only (spec 9)."""

    model_config = ConfigDict(frozen=True)

    sample_id: str = Field(...)
    timestamp: datetime = Field(...)
    symbol: str = Field(default="XAUUSD")
    timeframe: str = Field(default="M1")
    feature_schema_id: str = Field(default="scalp_v1")
    feature_dimension: int = Field(default=50, gt=0)
    feature_vector: list[float] = Field(default_factory=list)
    price_context: dict[str, float] = Field(default_factory=dict)
    regime: str = Field(default="UNKNOWN")
    news_context: dict[str, float] = Field(default_factory=dict)
    news_context_schema_id: str = Field(default="news_context_v1")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    def validate_schema(self) -> None:
        if len(self.feature_vector) != self.feature_dimension:
            raise ValueError(
                f"Sample {self.sample_id} schema mismatch: "
                f"expected {self.feature_dimension} features, got {len(self.feature_vector)}"
            )


class SetupContract(BaseModel):
    """A structured market condition (spec 9)."""

    model_config = ConfigDict(frozen=True)

    setup_id: str = Field(...)
    setup_version: str = Field(default="1.0.0")
    setup_type: str = Field(default="")  # e.g. TREND, RANGE, BREAKOUT
    conditions: dict[str, Any] = Field(default_factory=dict)  # rule parameters
    compatible_strategies: list[str] = Field(default_factory=list)


class StrategyContract(BaseModel):
    """Decision logic + constraints, independent of the model (spec 9 / 32)."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(default="1.0.0")
    setup_compatibility: list[str] = Field(default_factory=list)
    entry_conditions: dict[str, Any] = Field(default_factory=dict)
    exit_assumptions: dict[str, Any] = Field(default_factory=dict)
    risk_assumptions: dict[str, Any] = Field(default_factory=dict)
    context_restrictions: dict[str, Any] = Field(default_factory=dict)
    lifecycle_status: str = Field(default="ACTIVE")


# =============================================================================
# ARCHITECTURE IDENTITIES
# =============================================================================


class ModelArchitecture(StrEnum):
    """Registered architecture identities (spec 19 / 44)."""

    LEGACY_SCALPNET_V1 = "LEGACY_SCALPNET_V1"  # baseline (control group)
    MLP_V2 = "MLP_V2"
    TCN_V2 = "TCN_V2"
    TCN_ATTENTION_V1 = "TCN_ATTENTION_V1"
    TRANSFORMER_V1 = "TRANSFORMER_V1"


# =============================================================================
# MODEL MANIFEST (spec 4)
# =============================================================================


class ModelManifest(BaseModel):
    """The artifact is self-describing: "exactly what produced this model?".

    Lives alongside the weights file (never inside JSON-only artifact).
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    model_id: str = Field(...)
    model_version: str = Field(...)
    role: str = Field(default="CANDIDATE")  # CANDIDATE / CHALLENGER / CHAMPION / LEGACY_BASELINE
    status: str = Field(default="TRAINED")

    # Architecture
    architecture_id: str = Field(default=ModelArchitecture.LEGACY_SCALPNET_V1.value)
    architecture_version: str = Field(default="1.0.0")
    architecture_parameters: dict[str, Any] = Field(default_factory=dict)

    # Input
    feature_schema_id: str = Field(default="scalp_v1")
    feature_schema_version: str = Field(default="1.0.0")
    feature_dimension: int = Field(default=50, gt=0)

    # Labels
    label_schema_id: str = Field(default="triple_barrier_3class_v1")
    label_schema_version: str = Field(default="1.0.0")
    class_count: int = Field(default=3, ge=2)
    classes: list[str] = Field(default_factory=lambda: ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"])

    # Training
    dataset_id: str = Field(default="")
    dataset_version: str = Field(default="")
    dataset_hash: str = Field(default="")
    training_run_id: str = Field(default="")
    random_seed: int = Field(default=42)
    training_config: dict[str, Any] = Field(default_factory=dict)
    optimizer: str = Field(default="")
    scheduler: str = Field(default="")
    loss_config: dict[str, Any] = Field(default_factory=dict)

    # Strategy context
    strategy_id: str = Field(default="")
    strategy_version: str = Field(default="")
    supported_symbols: list[str] = Field(default_factory=lambda: ["XAUUSD"])
    supported_timeframes: list[str] = Field(default_factory=lambda: ["M1", "M5"])
    setup_contract: dict[str, Any] = Field(default_factory=dict)

    # News
    news_schema_version: str = Field(default="")
    news_context_contract: dict[str, Any] = Field(default_factory=dict)
    news_enabled: bool = Field(default=False)
    news_feature_provenance: dict[str, Any] = Field(default_factory=dict)

    # Validation
    walk_forward_status: str = Field(default="")
    oos_status: str = Field(default="")
    robustness_status: str = Field(default="")
    regime_status: str = Field(default="")
    calibration_status: str = Field(default="")
    risk_status: str = Field(default="")
    final_validation_result: dict[str, Any] = Field(default_factory=dict)

    # Integrity
    artifact_hash: str = Field(default="")
    manifest_hash: str = Field(default="")
    scaler_hash: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    build_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    def digest(self) -> str:
        """Deterministic manifest identity (excludes mutable hash fields)."""
        import hashlib
        import json

        payload = self.model_dump(exclude={"artifact_hash", "manifest_hash", "created_at"})
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# =============================================================================
# DATASET MANIFEST (spec 8)
# =============================================================================


class DatasetManifest(BaseModel):
    """Self-describing dataset artifact identity."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(...)
    dataset_version: str = Field(default="1.0.0")
    source_identity_hash: str = Field(default="")
    row_counts: dict[str, int] = Field(default_factory=dict)  # train/val/test
    temporal_range: dict[str, str] = Field(default_factory=dict)
    symbol: str = Field(default="XAUUSD")
    timeframe: str = Field(default="M1")
    feature_schema_id: str = Field(default="scalp_v1")
    label_schema_id: str = Field(default="triple_barrier_3class_v1")
    label_config_hash: str = Field(default="")
    split_config_hash: str = Field(default="")
    purge_parameters: dict[str, Any] = Field(default_factory=dict)
    embargo_parameters: dict[str, Any] = Field(default_factory=dict)
    generation_version: str = Field(default="1.0.0")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_experience_range: dict[str, str] = Field(default_factory=dict)
    news_schema_id: str = Field(default="")
    news_data_range: dict[str, str] = Field(default_factory=dict)
    news_version: str = Field(default="")
    strategy_context_version: str = Field(default="")
    dataset_hash: str = Field(default="")

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


# =============================================================================
# EXPERIMENT CONFIG (spec 18 / 21)
# =============================================================================


class ExperimentConfig(BaseModel):
    """One bounded, explainable experiment (spec 18)."""

    model_config = ConfigDict(frozen=True)

    experiment_id: str = Field(...)
    dataset_id: str = Field(...)
    class_count: int = Field(default=3, ge=2)
    architecture: str = Field(default=ModelArchitecture.LEGACY_SCALPNET_V1.value)
    architecture_parameters: dict[str, Any] = Field(default_factory=dict)
    training: dict[str, Any] = Field(default_factory=dict)  # epochs/lr/batch/seed...
    strategy_id: str = Field(default="scalp_default")
    strategy_version: str = Field(default="1.0.0")
    news_enabled: bool = Field(default=False)
    news_schema_id: str = Field(default="news_context_v1")
    seed: int = Field(default=42)
    notes: str = Field(default="")


# =============================================================================
# VALIDATION / ABLATION RESULTS
# =============================================================================


class ValidationResults(BaseModel):
    """Aggregated validation outcome for one candidate (spec 23 / 26 / 27)."""

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(...)
    experiment_id: str = Field(...)
    overall: dict[str, Any] = Field(default_factory=dict)
    gates: list[dict[str, Any]] = Field(default_factory=list)
    regime_results: dict[str, Any] = Field(default_factory=dict)
    calibration: dict[str, Any] = Field(default_factory=dict)
    class_distribution: dict[str, int] = Field(default_factory=dict)
    class_collapse_detected: bool = Field(default=False)
    news_ablation: dict[str, Any] = Field(default_factory=dict)
    verdict: str = Field(default="REJECTED")  # REJECTED / CHALLENGER_ELIGIBLE
    passed: bool = Field(default=False)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
