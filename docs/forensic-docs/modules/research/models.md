# src/nexus_scalp/research/models.py

- PURPOSE: PHASE 09B immutable domain contracts for the research / backtest /
  validation layer — the single source of typed truth shared by every research
  engine (dataset, metrics, backtest, walk-forward, OOS, robustness, scoring,
  registry, pipeline, worker).
- ARCHITECTURE LAYER: Domain (frozen pydantic contracts). Lives BELOW the
  Phase 08 experience ledger in authority: every research artifact is DERIVED
  from the immutable experience store and rebuildable from it. No order
  authority anywhere (docstring lines 6-8).
- RESPONSIBILITY: define the research vocabulary — samples with full
  provenance, datasets, execution-friction assumptions, backtest / walk-forward
  / OOS / robustness results, the decomposable strategy score, the registry
  entry, and the run metadata envelope. Versioning/schema safety: every
  candidate and result carries `feature_schema_id` + `feature_dimension` so a
  strategy discovered under scalp_v1/50D is never silently compared under a
  wider schema (lines 10-12).
- DEPENDENCIES: pydantic; `experience.models` CANONICAL_FEATURE_DIMENSION /
  CANONICAL_FEATURE_SCHEMA_ID (the canonical scalar contract).
- CONNECTS TO: every other research module; `strategies/base.py` and
  `strategies/seeder.py` build StrategyRegistryEntry rows from it; the web API
  reads its JSON dumps.

- KEY CONCEPTS:
  - Constants: MIN_EVIDENCE_SAMPLES=20 (minimum before scoring confidence,
    line 26) and SMALL_SAMPLE_FLOOR=8 (hard floor below which a candidate is
    always LOW EVIDENCE regardless of win rate/expectancy, lines 28-29).
  - `CandidateLifecycle` (StrEnum, lines 32-45): DISCOVERED ... VALIDATED,
    SHADOW, ACTIVE plus terminal REJECTED / DEGRADED / RETIRED.
    `_INELIGIBLE` (lines 49-51): REJECTED/RETIRED/DEGRADED may never become
    live; consumed by `is_eligible_for_new_trades` (lines 345-347).
  - `ExecutionAssumptions` (lines 54-78): frozen friction bundle —
    spread_ticks / slippage_ticks / latency_ms added stresses, price_tick
    converts ticks→points, pay_spread default True (entry at adverse edge),
    max_slippage_ticks=5.0 guard against runaway friction. `with_perturbation`
    returns a NEW bundle with added stress (spec 16 robustness).
  - `ResearchSample` (lines 81-136): one causally-safe training observation
    with full provenance (experience_id, idempotency_key, decision/outcome
    timestamps, symbol/timeframe, strategy id/version, schema, regime/session/
    volatility/trend tags, feature_hash + context_fingerprint, entry/SL/TP,
    realized R & PnL, MAE/MFE, exit_reason). `effective_expectancy` = R
    multiple; `outcome_horizon_seconds` = outcome − decision clamped >= 0.
    Timestamp validator normalizes naive→UTC.
  - `ResearchDataset` (lines 139-166): frozen; records schema_ids; `ordered()`
    sorts by decision_timestamp so temporal splits are meaningful; `__len__`.
  - `BacktestResult` (lines 169-213): deterministic backtest output —
    trade counts, expectancy R/USD, avg win/loss, profit factor, drawdowns
    (R + USD), recovery duration, variance, worst/largest loss, tail_loss_count
    (r <= -1.5), max consecutive losses, MAE/MFE/holding means, friction
    sensitivities (spread/slippage/latency per-tick R degradation) and the
    cumulative R equity curve. Helpers `has_positive_expectancy`,
    `win_rate`.
  - `WalkForwardFold` / `WalkForwardResult` (lines 216-249): per-fold train/
    val/oos window boundaries + sample counts + val/oos expectancy +
    oos_drawdown + status (PASS|FAIL|INCONCLUSIVE); result aggregates passed
    flag, avg val/oos expectancy, relative `degradation`.
  - `OOSResult` (lines 252-265): hard gate output (spec 15/34) — in-sample vs
    OOS expectancy, oos_samples, oos_win_rate, status PASS|FAIL, reason.
  - `RobustnessResult` (lines 268-279): baseline expectancy, per-scenario
    stress expectancies dict, max absolute degradation, PASS|FAIL + reason.
  - `StrategyScore` (lines 282-303): explainable 10-dimension score, every
    dimension bounded [0,1]: performance, risk, stability, oos, robustness,
    sample_confidence, regime_coverage, recency, execution_resilience,
    degradation_score + final_score + verdict (VALIDATED|REJECTED|
    INCONCLUSIVE) + reasons list.
  - `StrategyRegistryEntry` (lines 306-347): the enduring registry row —
    identity, schema, discovery source/window, context definition, parent
    ids, lifecycle, all four result payloads, score, confidence,
    sample_count, append-only validation_lineage, retirement_reason,
    created/updated timestamps. Registry is INDEPENDENT of any model file.
  - `ResearchRun` (lines 350-374): reproducible run metadata envelope —
    run_id, dataset/strategy/version, config, build_identity, result_summary,
    status, run_outcome, snapshot_id, gates list.

- HOT PATH / PERFORMANCE: pure frozen models; no I/O; used on the worker
  cycle / pipeline, never the tick path.

- EDGE CASES & PITFALLS:
  - `win_rate` returns wins/total_trades (breakeven excluded from numerator
    but included in denominator) — a breakeven-heavy run deflates win rate.
  - BacktestResult fields `max_drawdown_usd` are "USD notional" per the
    metric convention (positive magnitude), computed from the R curve —
    labels can mislead consumers expecting a true USD equity drawdown.
  - `ResearchSample.outcome_horizon_seconds` clamps negative deltas to 0.0,
    masking data where outcome precedes decision; dataset.evaluate_sample
    (dataset.py) rejects such rows with OUTCOME_PRECEDES_DECISION.
  - RegistryEntry `confidence` is free-floating — the pipeline sets it from
    score.sample_confidence; nothing re-validates the invariant here.