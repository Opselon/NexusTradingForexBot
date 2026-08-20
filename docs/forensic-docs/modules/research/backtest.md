# src/nexus_scalp/research/backtest.py

- PURPOSE: PHASE 09B deterministic, friction-aware backtest engine (spec
  12/13): runs `compute_backtest` (metrics.py) over a decided sample
  partition. Same dataset + strategy version + config + assumptions ⇒ same
  result.
- ARCHITECTURE LAYER: Research (pure computation over ResearchDataset; no
  I/O beyond logging; no order authority).
- RESPONSIBILITY: thin orchestrator around metrics.compute_backtest with the
  ability to restrict the measurement to the TRAIN+VALIDATION partition
  (`use_split=True`) — the correct in-sample measurement for the walk-forward
  / OOS gates.
- DEPENDENCIES: `research.metrics` (compute_backtest),
  `research.models` (BacktestResult, ExecutionAssumptions, ResearchDataset),
  `research.splitting` (split_temporal), observability.logging.
- CONNECTS TO: `ResearchPipeline.validate_candidate` (stage BACKTEST), the
  worker cycle, `strategies/factory/orchestrator` (via the pipeline — the
  factory never runs backtests itself).

- KEY CONCEPTS:
  - `BacktestEngine.__init__` (lines 32-33): defaults to a zero-friction
    `ExecutionAssumptions()` unless one is injected.
  - `run()` (lines 35-77): `use_split=True` (pipeline default) constructs a
    temporal split (val=0.2, oos=0.2, no purge/embargo — pipeline stage
    ordering applies embargo at the OOS/walk-forward gates instead) and
    backtests ONLY `train + validation`; the OOS partition is never measured
    here, preserving the in-sample/OOS boundary. Logs START/COMPLETE with
    expectancy/drawdown/trades.
  - Failure surface: an empty or all-rejected partition produces an
    all-fields-default BacktestResult (total_trades=0) — the caller
    (pipeline) treats total_trades == 0 as a DATA-level BLOCKED gate.

- HOT PATH / PERFORMANCE: single compute call per run; split construction is
  O(n log n); worker-cycle only.

- EDGE CASES & PITFALLS:
  - The engine itself applies NO purge/embargo on the split (split_temporal
    defaults are 0.0, 0.0): the discipline is delegated to the caller
    (pipeline passes purge/embargo only to walk-forward and OOS gates; the
    backtest gate runs raw train+val).
  - `use_split=True` with an already family-restricted dataset (pipeline
    family-select) still re-splits: small family datasets can produce near-
    empty train partitions, and backtest then reports 0 trades → BLOCKED —
    the root cause (family too small) is only visible in the rejection
    pipeline, not here.
  - Logged dataset_id is the caller's dataset; no per-run id is assigned by
    the engine (runs are the pipeline's concern).