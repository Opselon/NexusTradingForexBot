# src/nexus_scalp/strategies/factory/__init__.py

- PURPOSE: Strategy Factory — public package surface (2026-08-20):
  autonomous strategy evolution, research, validation, ranking & strategy
  factory. Orchestrates candidate generation (template / diversity /
  regime / exploration / optional LLM-provider), structural validation,
  deterministic backtest orchestration through the authoritative Phase 09B
  research pipeline, scoring, ranking, elite selection, failure analysis
  and evolution memory.
- ARCHITECTURE LAYER: package root (re-export surface; no logic; no I/O;
  no order authority).
- RESPONSIBILITY: aggregate the subpackage's public API into one `__all__`
  (lines 101-168) and document the SAFETY BOUNDARY (mirrors research/):
  the factory never places/modifies/closes an order, never holds an adapter
  or risk engine, never modifies the backtest engine, never allows an LLM
  candidate to bypass deterministic validation, and never promotes a
  strategy to ACTIVE automatically.
- DEPENDENCIES: dsl, evolution, models, orchestrator, provider, ranking,
  store, summarizer, telegram, validators, worker — all submodules.
- CONNECTS TO: any consumer importing `nexus_scalp.strategies.factory`
  (LiveEngine wiring, web/API, CLI, cron/loop drivers).

- KEY CONCEPTS:
  - Re-exports the complete surface: DSL/canonical helpers + generators
    (dsl module), evolution operators, all 17 domain models, the
    StrategyFactory orchestrator, the optional LLM provider +
    LLM_API_KEY_SECRET, ranking functions, the full store function set
    (writes + reads + loop state + provider usage), summarizer functions,
    send_factory_event, the four validators, and AutonomousLoopWorker.
  - `LLM_API_KEY_SECRET` is intentionally exported so operators/settings UI
    can address the secret-store key name.
  - The docstring is the standing safety contract; identical in spirit to
    research/__init__ but scoped to generation/evolution.
- HOT PATH / PERFORMANCE: import-time only.
- EDGE CASES & PITFALLS:
  - Importing this package triggers imports of dsl → features.schema_
    contract and store → adapters.database.audit_repository: any import
    error in those (e.g. missing optional deps or a schema contract
    mismatch) breaks the WHOLE factory surface including pure helpers
    (validators, ranking) — no guarded optional imports at this level.
  - `CandidateSource`, `EvolutionOperator`, etc. are re-exported both as
    enums and via models imports; no name collisions exist today.
  - The factory surface does NOT re-export research.pipeline or
    research.candidates — consumers needing those go through
    nexus_scalp.research directly.
  - No `__version__` or package-level constants beyond DSL_SCHEMA_VERSION /
    SUPPORTED_TIMEFRAMES re-exports.