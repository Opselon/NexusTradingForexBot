# src/nexus_scalp/research/walkforward.py

- PURPOSE: PHASE 09B walk-forward validation engine (spec 14/38): a strategy
  must survive repeated temporal re-evaluation — one lucky fold is not
  robustness.
- ARCHITECTURE LAYER: Research (pure orchestration over splits; no order
  authority).
- RESPONSIBILITY: consume the purged/embargoed folds from splitting.py,
  backtest each validation AND OOS window, track per-fold expectancy /
  drawdown / status, aggregate pass fraction, avg val/OOS expectancy and
  relative degradation, and emit a parked PASS/FAIL decision.
- DEPENDENCIES: `research.metrics` (compute_backtest),
  `research.models` (WalkForwardFold/WalkForwardResult, ExecutionAssumptions,
  ResearchDataset), `research.splitting` (walk_forward_folds),
  observability.logging.
- CONNECTS TO: pipeline stage WALK_FORWARD; the gate status (passed flag)
  feeds scoring.degradation_score and registry invariant checks.

- KEY CONCEPTS:
  - Thresholds: MIN_FOLD_EXPECTANCY_R = 0.0 (a fold PASSES when validation
    expectancy is strictly positive, lines 26-27) and MIN_PASS_FRACTION =
    0.5 (≥ half of folds must pass, line 29).
  - `WalkForwardEngine.validate` (lines 43-138): builds folds (n_splits
    default 3), runs `compute_backtest` on each fold's validation and OOS
    windows under the same assumptions; fold status = PASS if
    val expectancy > 0.0. Degradation = relative drop from avg validation to
    avg OOS: (avg_val − avg_oos)/|avg_val| (0.0 when avg_val == 0).
  - Aggregate decision (lines 112-117): passed ⇔ total_folds > 0 AND
    pass_count/total_folds >= min_pass_fraction AND avg_oos >= 0.0. Note the
    OOS-average floor is content-free when only some folds have OOS windows —
    empty OOS folds contribute 0.0 to avg_oos (see pitfalls).
  - Per-fold WalkForwardFold records OOS drawdown + samples, giving
    observability of every window; INCONCLUSIVE string is the default status
    in the model but this engine always writes PASS/FAIL.
  - Logs `[WALK_FORWARD] event=COMPLETE` with passes/avg/status.
- HOT PATH / PERFORMANCE: O(n_splits × backtest); folds are pre-computed once
  per validate; worker-cycle only.
- EDGE CASES & PITFALLS:
  - Fold count variance: `walk_forward_folds` emits fewer folds when the
    dataset is small (breaks when val window overruns); `validate` reports
    those folds — avg_oos and pass fraction are computed over the emitted
    subset, so a small dataset with 1 fold and 1 pass trivially "passes" the
    0.5 fraction check.
  - Empty OOS windows still append an oos_expectancy of 0.0 into avg_oos —
    an empty OOS pull can mask a pass (0.0 satisfies >= 0.0) or depress a
    strategy that actually had folds with OOS; callers must inspect fold
    oos_samples.
  - Degradation is relative to |avg_val|; a near-zero avg_val produces huge
    degradation numbers (the scoring layer's degradation_score clamps
    [0,1]).
  - `MIN_FOLD_EXPECTANCY_R` name suggests a floor value but it is the pass
    threshold constant (0.0) and also reused as the avg_oos floor at line
    116.