# src/nexus_scalp/research/__init__.py

- PURPOSE: package surface for PHASE 09B Strategy Research, Backtesting &
  Validation — documents the evidence pipeline and re-exports the public
  API (`__all__`, lines 91-133).
- ARCHITECTURE LAYER: Research package root (no logic; no I/O; no order
  authority).
- RESPONSIBILITY: define the documented pipeline
  EXPERIENCE → RESEARCH → CANDIDATE → BACKTEST → WALK-FORWARD →
  OUT-OF-SAMPLE → ROBUSTNESS → STATISTICAL SCORE → VALIDATED → SHADOW /
  OPERATOR-APPROVED, list the module layout, and state the SAFETY CONTRACT
  (research never places/modifies/closes an order; holds no adapter, no
  order manager, no risk engine; a candidate can never become LIVE
  automatically — promotion is operator-gated on the production side).
- DEPENDENCIES: every research module (backtest, candidates, dataset,
  discovery, evidence, lifecycle, models, observability, oos, pipeline,
  registry, robustness, scoring, walkforward, worker).
- CONNECTS TO: consumers importing `nexus_scalp.research.*` — the LiveEngine
  worker wiring, strategies.base (StrategyCandidate),
  strategies.seeder / factory (registry + candidate contracts), web/CLI.

- KEY CONCEPTS:
  - The docstring (lines 1-42) IS the reference architecture statement:
    research consumes the trustworthy Phase 08 experience ledger (NOT a
    parallel trade database) and enforces the strict research process.
  - Re-exports: engines (BacktestEngine, OOSGate, RobustnessEngine,
    WalkForwardEngine, ResearchPipeline, ResearchWorker), builders
    (ResearchDatasetBuilder, discover_candidates), domain models
    (ResearchDataset/Sample/Run, all result types, StrategyCandidate,
    StrategyRegistryEntry, StrategyScore, CandidateLifecycle,
    ExecutionAssumptions), lifecycle helpers (transition, can_transition,
    approve_for_live, LifecycleError), observability (ResearchObservability
    Store, ResearchGate/Event/RunSnapshot, EvidenceArtifact/EvidenceKind,
    FailureClass, GateStatus/Type, RunOutcome/Status, WorkerHealth,
    build_run_snapshot, stable_digest), worker status formatter.
- HOT PATH / PERFORMANCE: import-time only (module loads); no runtime cost.
- EDGE CASES & PITFALLS:
  - The docstring's module map (lines 15-34) does not list evidence.py /
    observability.py (added later by TASK-21) — the map is stale relative
    to the actual layout; the imports at lines 48-63 show the truth.
  - `ResearchRun` is exported but `store` (facade) itself is not re-exported
    — consumers import `nexus_scalp.research.store` directly (e.g. factory
    orchestrator, worker status).
  - No lazy imports: importing research pulls experience.models (and via
    store/registry, adapters) — acceptable for the app, but unit tests of
    pure modules must import the leaf modules directly.