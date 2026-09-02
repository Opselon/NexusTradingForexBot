"""
Strategy Factory — public package surface
=========================================
STRATEGY FACTORY (2026-08-20).

Autonomous strategy evolution, research, validation, ranking & strategy
factory. Orchestrates candidate generation (template / diversity / regime /
exploration / optional LLM-provider), structural validation, deterministic
backtest orchestration through the authoritative Phase 09B research pipeline,
scoring, ranking, elite selection, failure analysis and evolution memory.

SAFETY BOUNDARY (mirrors research/): the factory never places/modifies/closes
an order, never holds an adapter or risk engine, never modifies the backtest
engine, never allows an LLM candidate to bypass deterministic validation and
never promotes a strategy to ACTIVE automatically.
"""

from nexus_scalp.strategies.factory.benchmark import (
    benchmark_subset_for_candidate,
    build_benchmark_artifact,
    candidate_coverage_stats,
)
from nexus_scalp.strategies.factory.dsl import (
    DSL_SCHEMA_VERSION,
    SUPPORTED_TIMEFRAMES,
    build_feature_catalog,
    canonicalize_dsl,
    dsl_hash,
    feature_catalog_index,
    feature_ids,
    generate_generation_zero,
)
from nexus_scalp.strategies.factory.evolution import (
    adapt_probabilities,
    crossover,
    explore,
    mutate,
    mutate_with_action,
)
from nexus_scalp.strategies.factory.models import (
    CandidateResult,
    CandidateSource,
    EliteEntry,
    EvolutionConfig,
    EvolutionMemory,
    EvolutionOperator,
    FactoryCandidate,
    FactoryGeneration,
    FactoryStage,
    FailureReason,
    FeatureCatalogEntry,
    GenerationMode,
    GenerationSummary,
    LoopState,
    RankDimension,
    StrategyDsl,
    StrategyFamily,
    ValidationVerdict,
)
from nexus_scalp.strategies.factory.orchestrator import StrategyFactory
from nexus_scalp.strategies.factory.provider import (
    LLM_API_KEY_SECRET,
    LLMGenerationProvider,
    ProviderUsage,
)
from nexus_scalp.strategies.factory.ranking import (
    dimension_score,
    explain_rank,
    family_diversity,
    population_diversity,
    rank_strategies,
    score_components,
    selection_score,
)
from nexus_scalp.strategies.factory.store import (
    emit_event,
    get_generation,
    get_loop_state,
    list_candidates,
    list_events,
    list_failures,
    list_generations,
    list_runs,
    provider_usage_total,
    record_failure,
    record_provider_usage,
    record_run,
    set_loop_state,
    upsert_candidate,
    upsert_generation,
)
from nexus_scalp.strategies.factory.summarizer import (
    build_summary,
    format_summary_for_prompt,
    memory_summary,
)
from nexus_scalp.strategies.factory.telegram import send_factory_event
from nexus_scalp.strategies.factory.validators import (
    validate_candidate,
    validate_causality,
    validate_complexity,
    validate_features,
    validate_schema,
)
from nexus_scalp.strategies.factory.worker import AutonomousLoopWorker

__all__ = [
    "DSL_SCHEMA_VERSION",
    "LLM_API_KEY_SECRET",
    "SUPPORTED_TIMEFRAMES",
    "AutonomousLoopWorker",
    "CandidateResult",
    "CandidateSource",
    "EliteEntry",
    "EvolutionConfig",
    "EvolutionMemory",
    "EvolutionOperator",
    "FactoryCandidate",
    "FactoryGeneration",
    "FactoryStage",
    "FailureReason",
    "FeatureCatalogEntry",
    "GenerationMode",
    "GenerationSummary",
    "LLMGenerationProvider",
    "LoopState",
    "ProviderUsage",
    "RankDimension",
    "StrategyDsl",
    "StrategyFactory",
    "StrategyFamily",
    "ValidationVerdict",
    "adapt_probabilities",
    "benchmark_subset_for_candidate",
    "build_benchmark_artifact",
    "build_feature_catalog",
    "build_summary",
    "candidate_coverage_stats",
    "canonicalize_dsl",
    "crossover",
    "dimension_score",
    "dsl_hash",
    "emit_event",
    "explain_rank",
    "explore",
    "family_diversity",
    "feature_catalog_index",
    "feature_ids",
    "format_summary_for_prompt",
    "generate_generation_zero",
    "get_generation",
    "get_loop_state",
    "list_candidates",
    "list_events",
    "list_failures",
    "list_generations",
    "list_runs",
    "memory_summary",
    "mutate",
    "mutate_with_action",
    "population_diversity",
    "provider_usage_total",
    "rank_strategies",
    "record_failure",
    "record_provider_usage",
    "record_run",
    "score_components",
    "selection_score",
    "send_factory_event",
    "set_loop_state",
    "upsert_candidate",
    "upsert_generation",
    "validate_candidate",
    "validate_causality",
    "validate_complexity",
    "validate_features",
    "validate_schema",
]
