"""
Experience Intelligence Domain Models
=====================================
Phase 08 immutable memory contracts.

Design invariants (see agents/skill.md section 19):

1. MODEL-AGNOSTIC MEMORY
   Experience records never embed model weights and never require the model
   artifact that produced them to still exist. Provenance is descriptive
   metadata only (`ModelProvenance`).

2. SCHEMA-VERSIONED FEATURES
   The canonical live contract is `scalp_v1` / 50 dimensions, but the memory
   layer stores `feature_schema_id` + `feature_dimension` alongside every
   snapshot and validates length against the *declared* dimension. A 50D
   experience is never silently reinterpreted under a wider schema.

3. IMMUTABILITY
   Every model here is `frozen=True`. Outcomes are appended as a separate
   `ExperienceOutcome` row (see `ledger.py`), never by mutating the decision
   record. Corrections are additive `ExperienceCorrection` events.

4. MEASURABLE BEHAVIOR ONLY
   `BehavioralFlag` contains objectively computable trading behaviours. No
   psychological labels ("greed"/"fear") are represented anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Canonical live feature schema identity. The current production contract is
#: exactly 50 dimensions; future schemas (scalp_v2/60D, scalp_v3/350D) are
#: additive and MUST NOT rewrite historical records.
CANONICAL_FEATURE_SCHEMA_ID: str = "scalp_v1"
CANONICAL_FEATURE_DIMENSION: int = 50

#: Hard upper bound on strategy confidence. Confidence may never reach 1.0
#: regardless of sample size (Phase 08 rule 10).
MAX_STRATEGY_CONFIDENCE: float = 0.95


class StrategyLifecycle(StrEnum):
    """
    Data-driven lifecycle states for strategy/context families.

    DISCOVERED  -> Observed pattern, below the evaluation sample floor.
    EVALUATING  -> Accumulating samples toward statistical significance.
    VALIDATED   -> Sample floor reached with non-negative risk-adjusted edge.
    ACTIVE      -> Validated and currently trusted enough to boost confidence.
    DEGRADED    -> Recent decay / adverse variance. Influence reduced.
    RETIRED     -> Statistically significant negative expectancy. Ineligible.
    QUARANTINED -> Anomalous or unsafe execution evidence. Isolated.

    Retirement and quarantine block NEW live decisions only; raw history for
    the strategy is always preserved.
    """

    DISCOVERED = "DISCOVERED"
    EVALUATING = "EVALUATING"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    RETIRED = "RETIRED"
    QUARANTINED = "QUARANTINED"


#: Lifecycle states that may never qualify a new live proposal.
INELIGIBLE_LIFECYCLES: frozenset[StrategyLifecycle] = frozenset(
    {StrategyLifecycle.RETIRED, StrategyLifecycle.QUARANTINED}
)


class ExperienceAction(StrEnum):
    """Pre-trade decisions emitted by the Experience Intelligence boundary."""

    ALLOW = "ALLOW"
    ALLOW_WITH_CONTEXT = "ALLOW_WITH_CONTEXT"
    #: Evidence is insufficient to make any claim. Proposal passes through
    #: completely unchanged - never a fabricated endorsement.
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    PENALIZE = "PENALIZE"
    REJECT = "REJECT"


class BehavioralFlag(StrEnum):
    """
    Objectively measurable decision-quality failures.

    Every flag has a deterministic definition in `quality.py`. Flags are
    derived from recorded numbers only - never from sentiment or intent.
    """

    #: Fill drifted materially against us versus the proposed entry.
    ENTRY_CHASE = "ENTRY_CHASE"
    #: Position went straight into adverse excursion with no favourable phase.
    PREMATURE_ENTRY = "PREMATURE_ENTRY"
    #: High stated confidence paired with a materially negative outcome.
    CONFIDENCE_OVERSHOOT = "CONFIDENCE_OVERSHOOT"
    #: Adverse excursion breached the invalidation band yet the trade was held.
    THESIS_INVALIDATION_IGNORED = "THESIS_INVALIDATION_IGNORED"
    #: Held far beyond the strategy's expected horizon while edge decayed.
    EXCESSIVE_HOLD_DURATION = "EXCESSIVE_HOLD_DURATION"
    #: Executed risk deviated materially from the planned stop distance.
    RISK_DEVIATION = "RISK_DEVIATION"
    #: Repeated entries in the same context inside a short window.
    REENTRY_OVERTRADING = "REENTRY_OVERTRADING"
    #: Thesis stayed valid and MFE was large, but the exit banked far less.
    EARLY_EXIT = "EARLY_EXIT"
    #: Stop sat inside normal noise for the instrument's volatility.
    POOR_STOP_PLACEMENT = "POOR_STOP_PLACEMENT"
    #: Accepted reward/risk below the configured policy floor.
    WEAK_SETUP_ACCEPTED = "WEAK_SETUP_ACCEPTED"
    #: Slippage exceeded the tolerated fraction of planned risk.
    EXECUTION_SLIPPAGE_ANOMALY = "EXECUTION_SLIPPAGE_ANOMALY"


class QualityVerdict(StrEnum):
    """Coarse verdict used by the outcome decomposition."""

    GOOD = "GOOD"
    ACCEPTABLE = "ACCEPTABLE"
    POOR = "POOR"
    UNKNOWN = "UNKNOWN"


class FeatureSnapshot(BaseModel):
    """
    Schema-versioned feature snapshot.

    The length of `values` is validated against `feature_dimension`, NOT against
    the hard-coded 50D live contract, so historical experiences stay readable
    after the production schema widens.
    """

    model_config = ConfigDict(frozen=True)

    feature_schema_id: str = Field(
        default=CANONICAL_FEATURE_SCHEMA_ID, description="Feature schema identity"
    )
    feature_dimension: int = Field(
        default=CANONICAL_FEATURE_DIMENSION, gt=0, description="Declared dimensionality"
    )
    values: list[float] = Field(default_factory=list, description="Ordered feature values")
    feature_hash: str = Field(default="", description="Deterministic fingerprint of values")

    @model_validator(mode="after")
    def validate_dimension(self) -> FeatureSnapshot:
        if self.values and len(self.values) != self.feature_dimension:
            raise ValueError(
                f"Feature snapshot dimension mismatch: declared {self.feature_dimension}, "
                f"got {len(self.values)} values (schema={self.feature_schema_id})"
            )
        return self

    @property
    def is_canonical_live_schema(self) -> bool:
        """True when this snapshot matches the current production 50D contract."""
        return (
            self.feature_schema_id == CANONICAL_FEATURE_SCHEMA_ID
            and self.feature_dimension == CANONICAL_FEATURE_DIMENSION
        )


class ModelProvenance(BaseModel):
    """
    Descriptive identity of the artifacts that produced a decision.

    Deliberately contains no tensors and no file handles: the referenced model
    artifact may be deleted, retrained or hot-swapped without invalidating any
    historical experience.
    """

    model_config = ConfigDict(frozen=True)

    model_id: str = Field(default="unregistered", description="Stable model identity")
    model_version: str = Field(default="0.0.0", description="Model version string")
    model_role: str = Field(default="PRIMARY_SCALP", description="Role in the decision path")
    artifact_fingerprint: str = Field(default="", description="Weight-file fingerprint if known")
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)
    config_version: str = Field(default="0.0.0", description="Algo/config version")
    build_identity: str = Field(default="", description="Release/build identity if available")
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExecutionContext(BaseModel):
    """Broker-side execution quality evidence for one experience."""

    model_config = ConfigDict(frozen=True)

    expected_entry: float = Field(default=0.0, ge=0.0, description="Entry price we planned on")
    actual_entry: float = Field(default=0.0, ge=0.0, description="Entry price we received")
    #: Signed against the trade direction: positive means adverse fill.
    slippage_points: float = Field(default=0.0, description="Adverse fill displacement in price")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Dispatch-to-fill latency")
    spread_at_execution: float = Field(default=0.0, ge=0.0, description="Spread observed at fill")
    broker_retcode: int = Field(default=0, description="Raw broker return code")
    rejection_reason: str = Field(default="", description="Broker or gate rejection reason")
    executed_volume: float = Field(default=0.0, ge=0.0, description="Filled volume in lots")


class PositionBehavior(BaseModel):
    """How the position actually behaved between fill and final exit."""

    model_config = ConfigDict(frozen=True)

    mae_points: float = Field(default=0.0, description="Maximum adverse excursion in price")
    mfe_points: float = Field(default=0.0, description="Maximum favourable excursion in price")
    mae_usd: float = Field(default=0.0, description="Maximum adverse excursion in USD")
    mfe_usd: float = Field(default=0.0, description="Maximum favourable excursion in USD")
    mae_r: float = Field(default=0.0, ge=0.0, description="MAE normalised by planned risk")
    mfe_r: float = Field(default=0.0, ge=0.0, description="MFE normalised by planned risk")
    time_to_mae_sec: float = Field(default=0.0, ge=0.0, description="Seconds from open to MAE")
    time_to_mfe_sec: float = Field(default=0.0, ge=0.0, description="Seconds from open to MFE")
    duration_sec: float = Field(default=0.0, ge=0.0, description="Total holding time")
    expected_duration_sec: float = Field(
        default=0.0, ge=0.0, description="Strategy horizon expectation, 0 when unknown"
    )
    initial_sl_distance: float = Field(default=0.0, ge=0.0, description="Planned stop distance")
    sl_moved: bool = Field(default=False, description="Protective stop was modified in-flight")
    tp_moved: bool = Field(default=False, description="Target was modified in-flight")
    partial_closed: bool = Field(default=False, description="Position was partially closed")
    atr_at_entry: float = Field(default=0.0, ge=0.0, description="ATR observed at entry")


class OutcomeDecomposition(BaseModel):
    """
    Attribution of one closed position across independent quality dimensions.

    This exists so that "trade won" can never collapse into "strategy good".
    Every score is bounded [-1.0, +1.0]; `UNKNOWN` verdicts are used whenever
    the evidence required for a dimension was not captured.
    """

    model_config = ConfigDict(frozen=True)

    signal_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    strategy_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    regime_fit: float = Field(default=0.0, ge=-1.0, le=1.0)
    entry_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    risk_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    position_management_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    exit_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    execution_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    final_outcome_r: float = Field(default=0.0, description="Realised R multiple")

    strategy_verdict: QualityVerdict = Field(default=QualityVerdict.UNKNOWN)
    execution_verdict: QualityVerdict = Field(default=QualityVerdict.UNKNOWN)
    management_verdict: QualityVerdict = Field(default=QualityVerdict.UNKNOWN)
    #: True when PnL was positive while strategy/entry evidence was poor.
    profitable_for_wrong_reason: bool = Field(default=False)
    #: True when PnL was negative but the decision and risk were sound.
    acceptable_loss: bool = Field(default=False)


class StrategyContext(BaseModel):
    """
    Bounded, hierarchical context fingerprint identifying a strategy family.

    Context is intentionally coarse (regime / volatility bucket / trend state /
    session / confluence tokens) so that experiences aggregate into families
    instead of producing one strategy per unique float vector.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(..., description="Deterministic strategy family fingerprint")
    strategy_version: str = Field(default="1.0.0", description="Context/strategy logic version")
    symbol: str = Field(default="XAUUSD")
    timeframe: str = Field(default="M1")
    session: str = Field(default="ALL", description="TOKYO, LONDON, NY, OVERLAP, OFF_SESSION, ALL")
    regime: str = Field(default="UNKNOWN", description="Market regime classification")
    volatility_regime: str = Field(default="NORMAL", description="LOW, NORMAL, HIGH, EXTREME")
    trend_state: str = Field(default="NEUTRAL", description="BULLISH, BEARISH, NEUTRAL")
    setup_type: str = Field(default="UNCLASSIFIED", description="Canonical entry reason family")
    confluence_fingerprint: str = Field(default="", description="Sorted confluence token digest")
    parameter_hash: str = Field(default="", description="Strategy parameter fingerprint")


class ExperienceOutcome(BaseModel):
    """
    Append-only outcome event linked to exactly one decision experience.

    Stored in its own table keyed by `idempotency_key`, which is what makes the
    decision row immutable and duplicate close callbacks harmless.
    """

    model_config = ConfigDict(frozen=True)

    idempotency_key: str = Field(..., description="Key of the decision experience")
    execution_id: str = Field(default="", description="Broker ticket / order id")
    outcome_timestamp: datetime = Field(..., description="UTC time the outcome became known")
    is_executed: bool = Field(default=False)
    is_closed: bool = Field(default=False)
    exit_reason: str = Field(default="")
    realized_pnl_usd: float = Field(default=0.0)
    realized_r_multiple: float = Field(default=0.0)
    approved_volume: float = Field(default=0.0, ge=0.0)
    behavior: PositionBehavior = Field(default_factory=PositionBehavior)
    execution: ExecutionContext = Field(default_factory=ExecutionContext)
    decomposition: OutcomeDecomposition = Field(default_factory=OutcomeDecomposition)
    behavioral_flags: list[BehavioralFlag] = Field(default_factory=list)

    @field_validator("outcome_timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class ExperienceCorrection(BaseModel):
    """Additive correction event. Historical truth is never destroyed."""

    model_config = ConfigDict(frozen=True)

    correction_id: str = Field(...)
    idempotency_key: str = Field(..., description="Experience being corrected")
    corrected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str = Field(..., description="Why the correction was required")
    field_name: str = Field(..., description="Logical field being corrected")
    old_value: str = Field(default="")
    new_value: str = Field(default="")


class ExperienceRecord(BaseModel):
    """
    Immutable decision experience.

    Hard invariants:
      - Frozen. Outcomes arrive as separate `ExperienceOutcome` events.
      - `outcome_timestamp >= decision_timestamp` when an outcome is attached.
      - The feature snapshot is stored under its own schema identity and is
        never reinterpreted under a different dimension.

    Backward compatibility: payloads persisted by the first Phase 08 revision
    carried a flat `feature_vector_50d` list. `_migrate_legacy_payload` lifts
    those into a canonical `scalp_v1`/50D `FeatureSnapshot` on read so no
    historical row is lost when the schema evolves.
    """

    model_config = ConfigDict(frozen=True)

    record_version: int = Field(default=2, description="Experience record schema revision")

    experience_id: str = Field(..., description="Human-traceable experience identity")
    request_id: str = Field(..., description="Proposal request id")
    execution_id: str = Field(default="", description="Broker ticket / order id")
    decision_id: str = Field(default="", description="Pre-trade decision id")
    idempotency_key: str = Field(..., description="Deterministic dedup key")
    correction_of: str = Field(default="", description="Key of a superseded experience, if any")

    symbol: str = Field(...)
    timeframe: str = Field(default="M1")
    decision_timestamp: datetime = Field(..., description="UTC decision time")
    outcome_timestamp: datetime | None = Field(default=None, description="UTC outcome time")

    strategy_id: str = Field(...)
    strategy_version: str = Field(default="1.0.0")
    context: StrategyContext = Field(...)

    feature_snapshot: FeatureSnapshot = Field(default_factory=FeatureSnapshot)
    provenance: ModelProvenance = Field(default_factory=ModelProvenance)

    action: str = Field(...)
    entry_reason: str = Field(...)
    model_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    signal_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    proposed_entry: float = Field(..., gt=0.0)
    stop_loss: float = Field(..., gt=0.0)
    take_profit: float = Field(..., gt=0.0)
    risk_reward_ratio: float = Field(default=1.0)
    approved_volume: float = Field(default=0.0, ge=0.0)
    #: Reward/risk floor active when the decision was taken (policy provenance).
    min_rr_policy: float = Field(default=0.0, ge=0.0)

    is_executed: bool = Field(default=False)
    is_closed: bool = Field(default=False)
    exit_reason: str = Field(default="")
    realized_pnl_usd: float = Field(default=0.0)
    realized_r_multiple: float = Field(default=0.0)

    behavior: PositionBehavior = Field(default_factory=PositionBehavior)
    execution: ExecutionContext = Field(default_factory=ExecutionContext)
    decomposition: OutcomeDecomposition = Field(default_factory=OutcomeDecomposition)
    behavioral_flags: list[BehavioralFlag] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_payload(cls, data: Any) -> Any:
        """Lifts revision-1 payloads (flat `feature_vector_50d`) into a snapshot."""
        if not isinstance(data, dict):
            return data
        if data.get("feature_snapshot"):
            return data

        legacy_values = data.get("feature_vector_50d")
        if legacy_values is None:
            return data

        values = [float(v) for v in legacy_values]
        data = dict(data)
        data["feature_snapshot"] = {
            "feature_schema_id": data.get("feature_schema_id", CANONICAL_FEATURE_SCHEMA_ID),
            "feature_dimension": len(values) or CANONICAL_FEATURE_DIMENSION,
            "values": values,
            "feature_hash": data.get("feature_hash", ""),
        }
        data.setdefault("record_version", 1)
        data.pop("feature_vector_50d", None)
        return data

    @field_validator("decision_timestamp", "outcome_timestamp")
    @classmethod
    def validate_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @model_validator(mode="after")
    def validate_causality(self) -> ExperienceRecord:
        if self.outcome_timestamp is not None and self.outcome_timestamp < self.decision_timestamp:
            raise ValueError(
                "Causality violation: outcome_timestamp precedes decision_timestamp "
                f"({self.outcome_timestamp.isoformat()} < {self.decision_timestamp.isoformat()})"
            )
        return self

    @property
    def feature_dimension(self) -> int:
        """Dimensionality this experience was recorded under."""
        return self.feature_snapshot.feature_dimension

    @property
    def feature_schema_id(self) -> str:
        """Schema identity this experience was recorded under."""
        return self.feature_snapshot.feature_schema_id

    @property
    def feature_hash(self) -> str:
        """Feature fingerprint recorded with the snapshot."""
        return self.feature_snapshot.feature_hash

    @property
    def planned_risk_distance(self) -> float:
        """Planned stop distance in price units (0.0 when unavailable)."""
        return abs(self.proposed_entry - self.stop_loss)

    def with_outcome(self, outcome: ExperienceOutcome) -> ExperienceRecord:
        """
        Returns a NEW record carrying the outcome event.

        The stored decision row is never mutated; this projection exists purely
        so evaluators can reason over a merged view.
        """
        return self.model_copy(
            update={
                "execution_id": outcome.execution_id or self.execution_id,
                "outcome_timestamp": outcome.outcome_timestamp,
                "is_executed": outcome.is_executed,
                "is_closed": outcome.is_closed,
                "exit_reason": outcome.exit_reason,
                "realized_pnl_usd": outcome.realized_pnl_usd,
                "realized_r_multiple": outcome.realized_r_multiple,
                "approved_volume": outcome.approved_volume or self.approved_volume,
                "behavior": outcome.behavior,
                "execution": outcome.execution,
                "decomposition": outcome.decomposition,
                "behavioral_flags": list(outcome.behavioral_flags),
            }
        )


class StrategyScore(BaseModel):
    """
    Derived, rebuildable statistical evidence for one strategy family.

    Every field here can be recomputed from the immutable ledger; nothing in
    this object is a source of truth.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    sample_count: int = Field(default=0, ge=0, description="Closed executed experiences")
    win_count: int = Field(default=0, ge=0)
    loss_count: int = Field(default=0, ge=0)
    breakeven_count: int = Field(default=0, ge=0)

    win_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    expectancy_usd: float = Field(default=0.0)
    expectancy_r: float = Field(default=0.0)
    profit_factor: float = Field(default=1.0, ge=0.0)

    median_r: float = Field(default=0.0)
    max_drawdown_r: float = Field(default=0.0, ge=0.0)
    #: Drawdown normalised by sqrt(n) so long histories are not punished for
    #: simply having had more opportunities to draw down.
    normalized_drawdown_r: float = Field(default=0.0, ge=0.0)
    downside_tail_risk_r: float = Field(default=0.0)
    avg_mae_r: float = Field(default=0.0)
    avg_mfe_r: float = Field(default=0.0)
    r_variance: float = Field(default=0.0, ge=0.0)
    #: Student-t style significance of expectancy_r against zero.
    expectancy_t_stat: float = Field(default=0.0)

    recency_weighted_expectancy_r: float = Field(default=0.0)
    recent_window_expectancy_r: float = Field(default=0.0, description="Last-N trades expectancy")
    recent_window_size: int = Field(default=0, ge=0)

    in_sample_expectancy_r: float = Field(default=0.0, description="Older split expectancy")
    out_of_sample_expectancy_r: float = Field(default=0.0, description="Newer split expectancy")
    replay_sample_count: int = Field(default=0, ge=0, description="Samples used by replay split")
    replay_validated: bool = Field(default=False, description="OOS split confirmed a positive edge")

    avg_execution_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    avg_management_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    avg_entry_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    avg_strategy_quality: float = Field(default=0.0, ge=-1.0, le=1.0)
    flag_counts: dict[str, int] = Field(default_factory=dict)

    confidence_score: float = Field(default=0.0, ge=0.0, le=MAX_STRATEGY_CONFIDENCE)
    evidence_quality: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Sample sufficiency and stability of the evidence"
    )
    lifecycle_state: StrategyLifecycle = Field(default=StrategyLifecycle.DISCOVERED)
    #: Samples observed while RETIRED/QUARANTINED, gating any recovery.
    probation_samples: int = Field(default=0, ge=0)
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_eligible_for_new_trades(self) -> bool:
        """False for RETIRED / QUARANTINED families."""
        return self.lifecycle_state not in INELIGIBLE_LIFECYCLES


class PreTradeExperienceDecision(BaseModel):
    """Explainable pre-trade verdict from the Experience Intelligence gate."""

    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(...)
    request_id: str = Field(...)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    action: ExperienceAction = Field(...)
    qualifies_trade: bool = Field(...)
    adjusted_confidence: float = Field(..., ge=0.0, le=1.0)

    strategy_id: str = Field(...)
    strategy_lifecycle: StrategyLifecycle = Field(...)
    strategy_score: StrategyScore | None = Field(default=None)

    retrieved_sample_count: int = Field(default=0, ge=0)
    similarity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_quality: float = Field(default=0.0, ge=0.0, le=1.0)
    expectancy_r: float = Field(default=0.0)
    recent_expectancy_r: float = Field(default=0.0)
    drawdown_r: float = Field(default=0.0, ge=0.0)
    penalty_reason: str = Field(default="")
    provenance: ModelProvenance | None = Field(default=None)
