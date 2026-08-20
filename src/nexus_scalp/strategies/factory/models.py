"""
Strategy Factory — Domain Models
================================
STRATEGY FACTORY (2026-08-20).

Domain contracts for the autonomous strategy research loop. The factory is an
ORCHESTRATION layer over the authoritative Phase 09B research evidence
pipeline: it generates candidate strategy DSLs, validates them structurally,
runs them through the existing deterministic gates (backtest -> walk-forward ->
OOS -> robustness -> score) and persists the complete research memory.

SAFETY CONTRACT (mirrors research/):
  * The factory never places, modifies or closes an order.
  * The factory never holds an adapter / risk engine.
  * The LLM provider is UNTRUSTED INPUT: every candidate it produces passes
    the same deterministic DSL validation as template-generated ones, and all
    performance numbers come exclusively from the research pipeline.
  * No generated strategy is ever promoted to ACTIVE automatically.
  * The 70D feature contract (scalp_v3) is never modified by a generated
    strategy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def _coerce_utc(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GenerationMode(StrEnum):
    """How a generation is produced."""

    MANUAL = "MANUAL"
    AUTONOMOUS = "AUTONOMOUS"


class CandidateSource(StrEnum):
    """Where a candidate definition came from."""

    TEMPLATE = "TEMPLATE"  # deterministic template exploration
    DIVERSITY = "DIVERSITY"  # feature-combination exploration
    REGIME = "REGIME_SPECIALIST"  # regime-specific deterministic construction
    RANDOM = "RANDOM_EXPLORATION"  # controlled random exploration
    LLM = "LLM"  # external model provider (assisted mode)
    MUTATION = "MUTATION"
    CROSSOVER = "CROSSOVER"
    REPAIR = "REPAIR"
    SIMPLIFICATION = "SIMPLIFICATION"


class EvolutionOperator(StrEnum):
    """Operators that produce a new candidate from existing ones."""

    NONE = "NONE"  # initial / exploration candidates
    MUTATION = "MUTATION"
    CROSSOVER = "CROSSOVER"
    REPAIR = "REPAIR"
    REGIME_SPECIALIZATION = "REGIME_SPECIALIZATION"
    SIMPLIFICATION = "SIMPLIFICATION"


class StrategyFamily(StrEnum):
    """Normalized strategy family classification (spec 37)."""

    TREND_FOLLOWING = "TREND_FOLLOWING"
    MEAN_REVERSION = "MEAN_REVERSION"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"
    MOMENTUM = "MOMENTUM"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    VOLATILITY_CONTRACTION = "VOLATILITY_CONTRACTION"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    SESSION = "SESSION"
    MULTI_TIMEFRAME = "MULTI_TIMEFRAME"
    HYBRID = "HYBRID"


class FactoryStage(StrEnum):
    """Structured failure / lifecycle stages (spec 23)."""

    DSL_VALIDATION = "DSL_VALIDATION"
    FEATURE_VALIDATION = "FEATURE_VALIDATION"
    CAUSALITY_VALIDATION = "CAUSALITY_VALIDATION"
    COMPLEXITY_VALIDATION = "COMPLEXITY_VALIDATION"
    DEDUPLICATION = "DEDUPLICATION"
    BACKTEST = "BACKTEST"
    WALK_FORWARD = "WALK_FORWARD"
    OOS = "OOS"
    ROBUSTNESS = "ROBUSTNESS"
    SCORING = "SCORING"
    ELITE_SELECTION = "ELITE_SELECTION"
    EVOLUTION = "EVOLUTION"
    REGISTRATION = "REGISTRATION"


class FailureReason(StrEnum):
    """Structured rejection taxonomy (spec 23)."""

    INVALID_SCHEMA = "INVALID_SCHEMA"
    UNSUPPORTED_FEATURE = "UNSUPPORTED_FEATURE"
    UNSUPPORTED_SYMBOL = "UNSUPPORTED_SYMBOL"
    UNSUPPORTED_TIMEFRAME = "UNSUPPORTED_TIMEFRAME"
    LOOKAHEAD_RISK = "LOOKAHEAD_RISK"
    INSUFFICIENT_TRADES = "INSUFFICIENT_TRADES"
    NEGATIVE_EXPECTANCY = "NEGATIVE_EXPECTANCY"
    EXCESSIVE_DRAWDOWN = "EXCESSIVE_DRAWDOWN"
    LOW_PROFIT_FACTOR = "LOW_PROFIT_FACTOR"
    OOS_FAILURE = "OOS_FAILURE"
    WALK_FORWARD_FAILURE = "WALK_FORWARD_FAILURE"
    ROBUSTNESS_FAILURE = "ROBUSTNESS_FAILURE"
    OVERFIT_RISK = "OVERFIT_RISK"
    EXCESSIVE_COMPLEXITY = "EXCESSIVE_COMPLEXITY"
    DUPLICATE = "DUPLICATE"
    UNSTABLE_PARAMETERS = "UNSTABLE_PARAMETERS"
    REGIME_FRAGILITY = "REGIME_FRAGILITY"
    EXECUTION_SENSITIVITY = "EXECUTION_SENSITIVITY"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


class RankDimension(StrEnum):
    """Ranking dimensions (spec 53)."""

    OVERALL = "OVERALL"
    OOS = "OOS"
    ROBUSTNESS = "ROBUSTNESS"
    RISK_ADJUSTED = "RISK_ADJUSTED"
    CONSISTENCY = "CONSISTENCY"
    REGIME = "REGIME"
    LOW_DRAWDOWN = "LOW_DRAWDOWN"
    HIGH_EXPECTANCY = "HIGH_EXPECTANCY"
    DIVERSITY = "DIVERSITY"


class LoopState(StrEnum):
    """Autonomous loop control-plane state (spec 73)."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    FAILED = "FAILED"
    RECOVERING = "RECOVERING"


# ---------------------------------------------------------------------------
# Feature catalog entry (spec 9)
# ---------------------------------------------------------------------------


class FeatureCatalogEntry(BaseModel):
    """One machine-readable feature in the approved catalog.

    The catalog is DERIVED from the canonical 70D schema contract
    (features/schema_contract.py) — the factory never invents features.
    """

    model_config = ConfigDict(frozen=True)

    feature_id: str = Field(...)
    index: int = Field(..., ge=0)
    family: str = Field(...)
    description: str = Field(default="")
    datatype: str = Field(default="continuous")
    range_min: float = Field(default=-3.0)
    range_max: float = Field(default=3.0)
    causal: bool = Field(default=True)  # computed from completed bars only
    lookahead_safe: bool = Field(default=True)
    available: bool = Field(default=True)
    category: str = Field(default="base")


# ---------------------------------------------------------------------------
# Strategy DSL (spec 8)
# ---------------------------------------------------------------------------


class StrategyDsl(BaseModel):
    """The machine-readable strategy representation.

    The LLM (and the deterministic generator) produce ONLY this structured
    form. Never executable code. Every field is validated by `validators.py`
    before any backtest is scheduled.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0")
    hypothesis: dict[str, Any] = Field(default_factory=dict)
    family: StrategyFamily = Field(default=StrategyFamily.HYBRID)
    market: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    setup: dict[str, Any] = Field(default_factory=dict)
    entry: dict[str, Any] = Field(default_factory=dict)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    exit: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------


class FactoryCandidate(BaseModel):
    """One generated candidate before/after validation.

    Immutable definition; a definition change produces a NEW candidate row
    (content-addressed identity, mirroring StrategyCandidate).
    """

    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(...)  # SF-<hash>
    definition_hash: str = Field(...)  # canonical DSL hash (dedup key)
    generation_id: str = Field(...)
    source: CandidateSource = Field(default=CandidateSource.TEMPLATE)
    operator: EvolutionOperator = Field(default=EvolutionOperator.NONE)
    parent_ids: list[str] = Field(default_factory=list)
    dsl: StrategyDsl = Field(...)
    family: StrategyFamily = Field(default=StrategyFamily.HYBRID)
    population_index: int = Field(default=0, ge=0)
    llm_response_id: str = Field(default="")
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _coerce_utc(v)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


class FactoryGeneration(BaseModel):
    """One population of candidates (spec 25)."""

    model_config = ConfigDict(frozen=True)

    generation_id: str = Field(...)
    number: int = Field(..., ge=1)
    mode: GenerationMode = Field(default=GenerationMode.MANUAL)
    parent_generation: str = Field(default="")
    population_target: int = Field(..., ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = Field(default=None)
    status: str = Field(default="PENDING")  # PENDING|RUNNING|COMPLETED|CANCELLED|FAILED
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "completed_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return _coerce_utc(v) if v is not None else None


# ---------------------------------------------------------------------------
# Validation outcome
# ---------------------------------------------------------------------------


class ValidationVerdict(BaseModel):
    """Result of the structural/factory-side validation (pre-backtest)."""

    model_config = ConfigDict(frozen=True)

    passed: bool = Field(...)
    stage: FactoryStage = Field(default=FactoryStage.DSL_VALIDATION)
    reasons: list[str] = Field(default_factory=list)
    failure_reason: FailureReason | None = Field(default=None)
    details: dict[str, Any] = Field(default_factory=dict)


class CandidateResult(BaseModel):
    """Full lifecycle result of one candidate (spec 27 / 39)."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(...)
    strategy_id: str = Field(default="")
    strategy_version: str = Field(default="")
    structural: ValidationVerdict | None = Field(default=None)
    lifecycle: str = Field(default="GENERATED")  # GENERATED | REJECTED | RANKED | ELITE | ...
    failure_reasons: list[str] = Field(default_factory=list)
    backtest: dict[str, Any] = Field(default_factory=dict)
    walkforward: dict[str, Any] = Field(default_factory=dict)
    oos: dict[str, Any] = Field(default_factory=dict)
    robustness: dict[str, Any] = Field(default_factory=dict)
    score: dict[str, Any] = Field(default_factory=dict)
    rank: dict[str, Any] = Field(default_factory=dict)
    registry: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime | None = Field(default=None)
    duration_ms: float = Field(default=0.0, ge=0.0)

    @field_validator("evaluated_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return _coerce_utc(v) if v is not None else None


class EliteEntry(BaseModel):
    """An elite-preserved strategy (spec 7 / 58)."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(...)
    strategy_version: str = Field(...)
    generation_id: str = Field(...)
    candidate_id: str = Field(...)
    family: StrategyFamily = Field(default=StrategyFamily.HYBRID)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    rank: int = Field(default=0, ge=1)
    promoted_at: datetime = Field(default_factory=utc_now)

    @field_validator("promoted_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _coerce_utc(v)


# ---------------------------------------------------------------------------
# Evolution memory (spec 25)
# ---------------------------------------------------------------------------


class GenerationSummary(BaseModel):
    """Compact per-generation research summary fed to the next generation."""

    model_config = ConfigDict(frozen=True)

    generation_id: str = Field(...)
    number: int = Field(..., ge=1)
    population: int = Field(default=0, ge=0)
    structurally_valid: int = Field(default=0, ge=0)
    evaluated: int = Field(default=0, ge=0)
    validated: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    elite: int = Field(default=0, ge=0)
    avg_score: float = Field(default=0.0)
    best_score: float = Field(default=0.0)
    median_score: float = Field(default=0.0)
    diversity: float = Field(default=0.0)
    failure_distribution: dict[str, int] = Field(default_factory=dict)
    feature_distribution: dict[str, int] = Field(default_factory=dict)
    family_distribution: dict[str, int] = Field(default_factory=dict)
    operator_survival: dict[str, dict[str, int]] = Field(default_factory=dict)
    cost: dict[str, Any] = Field(default_factory=dict)
    runtime_ms: float = Field(default=0.0, ge=0.0)


class EvolutionMemory(BaseModel):
    """Structured learning context for the next generation (spec 24 / 81)."""

    model_config = ConfigDict(frozen=True)

    current_generation: int = Field(default=0, ge=0)
    generations: list[GenerationSummary] = Field(default_factory=list)
    elite: list[dict[str, Any]] = Field(default_factory=list)  # bounded, compact
    worst: list[dict[str, Any]] = Field(default_factory=list)
    common_failures: list[dict[str, Any]] = Field(default_factory=list)
    successful_features: dict[str, int] = Field(default_factory=dict)
    failed_features: dict[str, int] = Field(default_factory=dict)
    stagnation_count: int = Field(default=0, ge=0)
    operator_success: dict[str, float] = Field(default_factory=dict)


class EvolutionConfig(BaseModel):
    """Evolution operator budgets (spec 5 / 7 / 107)."""

    model_config = ConfigDict(frozen=True)

    generation_size: int = Field(default=400, ge=1, le=2000)
    elite_size: int = Field(default=20, ge=1, le=500)
    mutation_rate: float = Field(default=0.30, ge=0.0, le=1.0)
    crossover_rate: float = Field(default=0.30, ge=0.0, le=1.0)
    exploration_rate: float = Field(default=0.25, ge=0.0, le=1.0)
    elite_preservation_rate: float = Field(default=0.15, ge=0.0, le=1.0)
    max_generations: int = Field(default=20, ge=1, le=10000)
    parallel_workers: int = Field(default=2, ge=1, le=16)
    max_candidates_per_generation: int = Field(default=400, ge=1, le=2000)

    # hard gates (shared with validators)
    min_trades: int = Field(default=20, ge=1)
    max_drawdown_r: float = Field(default=4.0, ge=0.5)
    min_profit_factor: float = Field(default=1.2, ge=1.0)
    min_expectancy_r: float = Field(default=0.05, ge=0.0)
    max_oos_degradation: float = Field(default=0.65, ge=0.0, le=1.0)

    # complexity budget (spec 12)
    max_conditions: int = Field(default=9, ge=1)
    max_features: int = Field(default=6, ge=1)
    max_timeframes: int = Field(default=2, ge=1)
    max_entry_clauses: int = Field(default=4, ge=1)
    max_exit_clauses: int = Field(default=4, ge=1)

    # stopping conditions (spec 55)
    max_runtime_sec: float = Field(default=3600.0, ge=60.0)
    max_generation_cost: float = Field(default=50.0, ge=0.0)
    max_llm_requests: int = Field(default=2000, ge=0)
    target_elite_count: int = Field(default=8, ge=0)
    no_improvement_generations: int = Field(default=4, ge=1)

    # stagnation / diversification (spec 56 / 57)
    stagnation_diversity_floor: float = Field(default=0.25, ge=0.0, le=1.0)
    exploration_boost: float = Field(default=0.15, ge=0.0, le=0.5)
