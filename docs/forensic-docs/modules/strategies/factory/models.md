# src/nexus_scalp/strategies/factory/models.py

- PURPOSE: STRATEGY FACTORY domain contracts (2026-08-20) — the machine-
  readable vocabulary for the autonomous strategy research loop: DSL,
  candidates, generations, validation verdicts, results, elite entries,
  evolution memory/config. The factory is an ORCHESTRATION layer over the
  authoritative Phase 09B research evidence pipeline; SAFETY CONTRACT
  mirrors research/: never places/modifies/closes an order, holds no
  adapter/risk engine, treats the LLM provider as UNTRUSTED INPUT (all its
  output passes the same deterministic DSL validation), never promotes to
  ACTIVE automatically, never modifies the 70D scalp_v3 feature contract.
- ARCHITECTURE LAYER: Domain (frozen pydantic; no I/O; no order authority).
- RESPONSIBILITY: typed contracts for generators, evolution operators,
  validation and persistence consumers.
- DEPENDENCIES: pydantic; stdlib datetime. (DSL/feature enums feed from
  `features/schema_contract` via dsl.py at runtime, not here.)
- CONNECTS TO: dsl.py (generators), validators.py (verdicts), evolution.py,
  orchestrator.py, store.py (persistence), ranking/summarizer/worker.

- KEY CONCEPTS:
  - Enums: GenerationMode (MANUAL/AUTONOMOUS); CandidateSource (TEMPLATE /
    DIVERSITY / REGIME (REGIME_SPECIALIST) / RANDOM (RANDOM_EXPLORATION) /
    LLM / MUTATION / CROSSOVER / REPAIR / SIMPLIFICATION — provenance of a
    definition); EvolutionOperator (NONE/MUTATION/CROSSOVER/REPAIR/
    REGIME_SPECIALIZATION/SIMPLIFICATION); StrategyFamily (11 normalized
    families incl. LIQUIDITY_SWEEP, SESSION, MULTI_TIMEFRAME, HYBRID);
    FactoryStage (structured gate/failure stages DSL_VALIDATION ... ELITE_
    SELECTION, EVOLUTION, REGISTRATION); FailureReason (20-value structured
    rejection taxonomy INVALID_SCHEMA ... PROVIDER_FAILURE); RankDimension
    (OVERALL/OOS/ROBUSTNESS/RISK_ADJUSTED/CONSISTENCY/REGIME/LOW_DRAWDOWN/
    HIGH_EXPECTANCY/DIVERSITY); LoopState (STOPPED/STARTING/RUNNING/PAUSED/
    STOPPING/FAILED/RECOVERING — control plane, spec 73).
  - `FeatureCatalogEntry` (167-186): one approved feature — derived from the
    canonical 70D schema contract (the factory never invents features), with
    index/family/datatype/range, causal + lookahead_safe flags, available,
    category.
  - `StrategyDsl` (194-214): the ONLY strategy representation the factory
    (and LLM) may produce — NEVER executable code; extra="forbid" rejects
    unknown keys at construction; fields hypothesis/family/market/context/
    setup/entry/filters/exit/risk/constraints + schema_version.
  - `FactoryCandidate` (222-246): frozen; candidate_id SF-<hash>,
    definition_hash (canonical DSL hash — the DEDUP key), generation_id,
    source/operator/parent_ids, dsl, family, population_index,
    llm_response_id, created_at. Definition change ⇒ NEW row (content-
    addressed identity mirroring StrategyCandidate).
  - `FactoryGeneration` (254-272): one population — number >= 1,
    parent_generation, population_target, status
    PENDING|RUNNING|COMPLETED|CANCELLED|FAILED, config.
  - `ValidationVerdict` (280-289): structural pre-backtest result —
    passed + stage + reasons + failure_reason + details.
  - `CandidateResult` (292-316): full lifecycle record of one candidate —
    structural verdict, lifecycle (GENERATED|REJECTED|RANKED|ELITE|...),
    failure_reasons, embedded backtest/walkforward/oos/robustness/score/
    rank/registry summaries, evaluated_at, duration_ms.
  - `EliteEntry` (319-336): elite-preserved strategy with score/rank/
    promoted_at.
  - `GenerationSummary` (344-366): compact per-generation research summary —
    population/valid/rejected/elite counts, avg/best/median score,
    diversity, failure/feature/family distributions, operator_survival,
    cost, runtime_ms. `EvolutionMemory` (369-382): learning context for the
    next generation — generations (bounded), elite/worst (bounded),
    common_failures, successful/failed features, stagnation_count,
    operator_success. `EvolutionConfig` (385-423): the full operator budget
    + hard gates + complexity budget + stopping conditions +
    stagnation/diversification floors (generation_size 400 default,
    elite_size 20, mutation/crossover/exploration/elite_preservation rates,
    max_generations 20, parallel_workers 2, min_trades 20,
    max_drawdown_r 4.0, min_profit_factor 1.2, min_expectancy_r 0.05,
    max_oos_degradation 0.65, max_conditions 9, max_features 6,
    max_timeframes 2, max_entry/exit_clauses 4, max_runtime_sec 3600,
    max_generation_cost 50, max_llm_requests 2000, target_elite_count 8,
    no_improvement_generations 4, stagnation_diversity_floor 0.25,
    exploration_boost 0.15).
- HOT PATH / PERFORMANCE: pure models; pydantic validation cost on every
  candidate construction/validation (400 candidates/generation × several
  constructions) — fine off the tick path.
- EDGE CASES & PITFALLS:
  - `FactoryCandidate.candidate_id` is CALLER-derived: the model does not
    recompute it from definition_hash — a mismatch (caller bug) creates
    inconsistent identity; dsl.py's candidate_id_from_hash is the single
    intended constructor.
  - `ValidationVerdict.details` is free-form; consumers (store.py UI,
    summarizer) must decode defensively.
  - `CandidateResult.lifecycle` accepts arbitrary strings — the enum
    guardrails of CandidateLifecycle do not apply here (loose status
    strings GENERATED/REJECTED/RANKED/ELITE).
  - `EvolutionConfig.max_oos_degradation` (0.65) is softer than the
    research OOS gate's 1.0 ceiling — the factory's "hard gates" are
    additive floors/sub-ceilings applied via _derived_failure_reasons, not
    replacements for the research gates.
  - EvolutionMemory.elite/worst are list[dict] (not EliteEntry) — bounded
    and compact by design; swappable shapes weaken type safety.