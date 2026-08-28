"""
Strategy Research Domain Models
===============================
PHASE 09B immutable contracts for the research / backtest / validation layer.

These models live BELOW the Phase 08 experience ledger in authority: every
research artifact is DERIVED from the immutable experience store and is
rebuildable from it. Nothing here can place, modify or close an order.

Versioning / schema safety: every candidate and result carries
`feature_schema_id` + `feature_dimension` so a strategy discovered under
`scalp_v1/50D` is never silently compared under a wider schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nexus_scalp.experience.models import CANONICAL_FEATURE_DIMENSION, CANONICAL_FEATURE_SCHEMA_ID

#: Minimum samples before a candidate may be scored with any confidence.
MIN_EVIDENCE_SAMPLES: int = 20
#: Hard floor below which a candidate is always LOW EVEDENCE regardless of win
#: rate or expectancy (small-sample protection, spec 18).
SMALL_SAMPLE_FLOOR: int = 8


class CandidateLifecycle(StrEnum):
    """Research lifecycle of a strategy candidate (spec 19).

    PHASE 25 evidence lifecycle (2026-08-25): DISCOVERED may route into the
    evidence-building track instead of a hard rejection when a candidate
    fails ONLY on sample size (INSUFFICIENT_TRADES / small-sample floors).
    The evidence states are strictly PRE-validation: they never satisfy the
    live-trade eligibility gate (require_validation_gate) and never weaken
    any WF/OOS/robustness threshold.
    """

    DISCOVERED = "DISCOVERED"
    INITIAL_TESTING = "INITIAL_TESTING"
    EVIDENCE_BUILDING = "EVIDENCE_BUILDING"
    WALK_FORWARD_READY = "WALK_FORWARD_READY"
    OOS_READY = "OOS_READY"
    ROBUSTNESS_READY = "ROBUSTNESS_READY"
    BACKTESTING = "BACKTESTING"
    VALIDATING = "VALIDATING"
    OOS_TESTING = "OOS_TESTING"
    ROBUSTNESS_TESTING = "ROBUSTNESS_TESTING"
    VALIDATED = "VALIDATED"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    DEGRADED = "DEGRADED"
    RETIRED = "RETIRED"


#: Lifecycle states that may never become live.
_INELIGIBLE: frozenset[CandidateLifecycle] = frozenset(
    {
        CandidateLifecycle.REJECTED,
        CandidateLifecycle.RETIRED,
        CandidateLifecycle.DEGRADED,
        CandidateLifecycle.INITIAL_TESTING,
        CandidateLifecycle.EVIDENCE_BUILDING,
        CandidateLifecycle.WALK_FORWARD_READY,
        CandidateLifecycle.OOS_READY,
        CandidateLifecycle.ROBUSTNESS_READY,
    }
)


class ExecutionAssumptions(BaseModel):
    """Friction assumptions used by the backtest (spec 12 / 13)."""

    model_config = ConfigDict(frozen=True)

    spread_ticks: float = Field(default=0.0, ge=0.0, description="Spread added in ticks")
    slippage_ticks: float = Field(default=0.0, ge=0.0, description="Adverse slippage in ticks")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Assumed order latency")
    #: Price step (tick size) used to convert ticks to points.
    price_tick: float = Field(default=0.01, gt=0.0)
    #: Whether entry is executed at the adverse edge of the spread (realistic).
    pay_spread: bool = Field(default=True)
    max_slippage_ticks: float = Field(default=5.0, ge=0.0)  # guard against runaway

    def with_perturbation(
        self, spread: float = 0.0, sl: float = 0.0, latency: float = 0.0
    ) -> ExecutionAssumptions:
        """Returns a NEW assumptions bundle with added stress (spec 16)."""
        return self.model_copy(
            update={
                "spread_ticks": self.spread_ticks + spread,
                "slippage_ticks": self.slippage_ticks + sl,
                "latency_ms": self.latency_ms + latency,
            }
        )


class ResearchSample(BaseModel):
    """
    One causally-safe training observation derived from an executed, closed
    experience. Carries full provenance back to the immutable ledger.
    """

    model_config = ConfigDict(frozen=True)

    sample_id: str = Field(...)
    experience_id: str = Field(...)
    idempotency_key: str = Field(...)

    decision_timestamp: datetime = Field(...)
    outcome_timestamp: datetime = Field(...)
    symbol: str = Field(...)
    timeframe: str = Field(default="M1")

    strategy_id: str = Field(...)
    strategy_version: str = Field(default="1.0.0")
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)

    regime: str = Field(default="UNKNOWN")
    session: str = Field(default="ALL")
    volatility_regime: str = Field(default="NORMAL")
    trend_state: str = Field(default="NEUTRAL")

    #: Minimal feature/value reference for research (provenance, not the live
    #: full 50D input which stays in the ledger).
    feature_hash: str = Field(default="")
    context_fingerprint: str = Field(default="")

    entry_price: float = Field(default=0.0, ge=0.0)
    stop_loss: float = Field(default=0.0, ge=0.0)
    take_profit: float = Field(default=0.0, ge=0.0)
    direction: str = Field(default="")

    realized_r: float = Field(default=0.0)
    realized_pnl_usd: float = Field(default=0.0)
    risk_distance: float = Field(default=0.0, ge=0.0)
    holding_duration_sec: float = Field(default=0.0, ge=0.0)
    mae_r: float = Field(default=0.0, ge=0.0)
    mfe_r: float = Field(default=0.0, ge=0.0)
    exit_reason: str = Field(default="")

    @field_validator("decision_timestamp", "outcome_timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    def effective_expectancy(self) -> float:
        return self.realized_r

    def outcome_horizon_seconds(self) -> float:
        delta = (self.outcome_timestamp - self.decision_timestamp).total_seconds()
        return max(0.0, delta)


class ResearchDataset(BaseModel):
    """
    Deterministic research dataset built from the immutable ledger.

    Samples preserve causal ordering (decision_timestamp ascending) so temporal
    splits are meaningful. Provenance and feature-schema are preserved per
    sample, satisfying research data versioning (spec 26).

    P0-E (BUG-140): `provenance_extra` carries the explicit dataset contract —
    a deterministic evidence census (total decisions, valid research samples,
    terminal non-trades, recovery-queue counts, eligibility rules) so no
    consumer can mistake a filtered dataset for the full population.
    """

    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(...)
    source: str = Field(default="experience_ledger")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    samples: list[ResearchSample] = Field(default_factory=list)
    source_range: dict[str, str] = Field(default_factory=dict)
    schema_ids: list[str] = Field(default_factory=list)
    #: P0-E: explicit eligibility/contract metadata (deterministic census).
    provenance_extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    def ordered(self) -> list[ResearchSample]:
        return sorted(self.samples, key=lambda s: s.decision_timestamp)

    def __len__(self) -> int:
        return len(self.samples)


class BacktestResult(BaseModel):
    """Deterministic backtest output (spec 13)."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(...)
    dataset_id: str = Field(...)
    assumptions: ExecutionAssumptions = Field(default_factory=ExecutionAssumptions)
    #: BUG-140 Phase 5: explicit evaluation semantics. EMPIRICAL_REPLAY
    #: = expectancy recomputed over RECORDED experiences (what the engine
    #: does today); HISTORICAL_SIMULATION = strategy logic executed against
    #: raw market data producing synthetic trades. The two are NOT
    #: scientifically equivalent; consumers must never conflate them.
    evaluation_mode: str = Field(
        default="EMPIRICAL_REPLAY",
        description="EMPIRICAL_REPLAY | HISTORICAL_SIMULATION",
    )

    total_trades: int = Field(default=0, ge=0)
    wins: int = Field(default=0, ge=0)
    losses: int = Field(default=0, ge=0)
    breakeven: int = Field(default=0, ge=0)
    net_pnl_usd: float = Field(default=0.0)
    expectancy_r: float = Field(default=0.0)
    expectancy_usd: float = Field(default=0.0)
    avg_win_r: float = Field(default=0.0)
    avg_loss_r: float = Field(default=0.0)
    profit_factor: float = Field(default=0.0)
    max_drawdown_usd: float = Field(default=0.0)
    max_drawdown_r: float = Field(default=0.0)
    recovery_duration_trades: int = Field(default=0, ge=0)
    return_variance: float = Field(default=0.0, ge=0.0)
    worst_trade_r: float = Field(default=0.0)
    largest_loss_r: float = Field(default=0.0)
    tail_loss_count: int = Field(default=0, ge=0)
    max_consecutive_losses: int = Field(default=0, ge=0)
    avg_mae_r: float = Field(default=0.0)
    avg_mfe_r: float = Field(default=0.0)
    avg_holding_duration_sec: float = Field(default=0.0, ge=0.0)
    spread_sensitivity_r: float = Field(default=0.0)
    slippage_sensitivity_r: float = Field(default=0.0)
    latency_sensitivity_r: float = Field(default=0.0)
    equity_curve_r: list[float] = Field(default_factory=list)

    @property
    def has_positive_expectancy(self) -> bool:
        return self.expectancy_r > 0.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades


class WalkForwardFold(BaseModel):
    model_config = ConfigDict(frozen=True)

    fold: int = Field(...)
    train_start: datetime = Field(...)
    train_end: datetime = Field(...)
    val_start: datetime = Field(...)
    val_end: datetime = Field(...)
    oos_start: datetime | None = Field(default=None)
    oos_end: datetime | None = Field(default=None)
    train_samples: int = Field(default=0, ge=0)
    val_samples: int = Field(default=0, ge=0)
    oos_samples: int = Field(default=0, ge=0)
    val_expectancy_r: float = Field(default=0.0)
    oos_expectancy_r: float = Field(default=0.0)
    oos_drawdown_r: float = Field(default=0.0)
    status: str = Field(default="INCONCLUSIVE")  # PASS | FAIL | INCONCLUSIVE


class WalkForwardResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(...)
    dataset_id: str = Field(...)
    folds: list[WalkForwardFold] = Field(default_factory=list)
    passed: bool = Field(default=False)
    avg_val_expectancy_r: float = Field(default=0.0)
    avg_oos_expectancy_r: float = Field(default=0.0)
    degradation: float = Field(default=0.0)
    #: PHASE 26 strategy-aware validation: transparency block describing the
    #: context contract applied to the fold population (None = global eval).
    context_diagnostics: dict[str, Any] | None = Field(default=None)
    #: PHASE 29: explicit reason when WF could not form folds (family too
    #: small) instead of a silent passed=False with zeroed metrics.
    insufficient_reason: str | None = Field(default=None)

    @property
    def fold_count(self) -> int:
        return len(self.folds)


class OOSResult(BaseModel):
    """Hard out-of-sample gate result (spec 15 / 34)."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(...)
    dataset_id: str = Field(...)
    in_sample_expectancy_r: float = Field(default=0.0)
    oos_expectancy_r: float = Field(default=0.0)
    oos_samples: int = Field(default=0, ge=0)
    oos_win_rate: float = Field(default=0.0)
    status: str = Field(...)  # PASS | FAIL
    reason: str = Field(default="")
    #: PHASE 26 strategy-aware validation: context contract diagnostics.
    context_diagnostics: dict[str, Any] | None = Field(default=None)


class RobustnessResult(BaseModel):
    """Robustness stress result (spec 16 / 35)."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(...)
    baseline_expectancy_r: float = Field(default=0.0)
    stress_expectancies: dict[str, float] = Field(default_factory=dict)
    max_degradation: float = Field(default=0.0)  # absolute drop from baseline
    status: str = Field(...)  # PASS | FAIL
    reason: str = Field(default="")


class StrategyScore(BaseModel):
    """
    Decomposable, explainable Strategy Validation Score (spec 17).

    Each dimension is bounded and independently inspectable; no single win rate.
    """

    model_config = ConfigDict(frozen=True)

    performance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    stability_score: float = Field(default=0.0, ge=0.0, le=1.0)
    oos_score: float = Field(default=0.0, ge=0.0, le=1.0)
    robustness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    sample_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    regime_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    recency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_resilience: float = Field(default=0.0, ge=0.0, le=1.0)
    degradation_score: float = Field(default=0.0, ge=0.0, le=1.0)
    final_score: float = Field(default=0.0, ge=0.0, le=1.0)
    verdict: str = Field(default="INCONCLUSIVE")  # VALIDATED | REJECTED | INCONCLUSIVE
    reasons: list[str] = Field(default_factory=list)


class StrategyRegistryEntry(BaseModel):
    """
    One row in the enduring strategy registry (spec 20).

    Registry is INDEPENDENT of any model file: it preserves identity, version,
    schema, discovery source, validation lineage, all result summaries, score,
    confidence, lifecycle, timestamps and retirement reason.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(...)
    feature_schema_id: str = Field(default=CANONICAL_FEATURE_SCHEMA_ID)
    feature_dimension: int = Field(default=CANONICAL_FEATURE_DIMENSION, gt=0)
    discovery_source: str = Field(default="")
    discovery_window: str = Field(default="")
    context_definition: dict[str, Any] = Field(default_factory=dict)
    parent_strategy_ids: list[str] = Field(default_factory=list)

    lifecycle: CandidateLifecycle = Field(default=CandidateLifecycle.DISCOVERED)
    backtest: BacktestResult | None = Field(default=None)
    walkforward: WalkForwardResult | None = Field(default=None)
    oos: OOSResult | None = Field(default=None)
    robustness: RobustnessResult | None = Field(default=None)
    score: StrategyScore | None = Field(default=None)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sample_count: int = Field(default=0, ge=0)
    validation_lineage: list[str] = Field(default_factory=list)
    retirement_reason: str = Field(default="")
    #: PHASE 25 (2026-08-25): per-candidate context matrices
    #: {session_matrix, hourly_matrix, weekday_matrix, regime_matrix} from
    #: research.context_analysis.compute_context_matrices — discovery-quality
    #: evidence keyed by market condition (persisted as JSON TEXT).
    context_matrices: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at", "updated_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @property
    def is_eligible_for_new_trades(self) -> bool:
        return self.lifecycle not in _INELIGIBLE


class ResearchRun(BaseModel):
    """Metadata for one reproducible validation run (spec 26)."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(...)
    dataset_id: str = Field(...)
    strategy_id: str = Field(...)
    strategy_version: str = Field(...)
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    config: dict[str, Any] = Field(default_factory=dict)
    build_identity: str = Field(default="")
    result_summary: dict[str, Any] = Field(default_factory=dict)
    completed_at: datetime | None = Field(default=None)
    status: str = Field(default="QUEUED")  # QUEUED|RUNNING|COMPLETED|FAILED|CANCELLED|BLOCKED
    run_outcome: str = Field(default="INCONCLUSIVE")  # VALIDATED|REJECTED|INCONCLUSIVE
    snapshot_id: str = Field(default="")
    gates: list[str] = Field(default_factory=list)  # gate_ids of this run

    @field_validator("executed_at", "completed_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
