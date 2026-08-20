# src/nexus_scalp/research/metrics.py

- PURPOSE: PHASE 09B pure, deterministic performance & risk statistics for
  backtest / walk-forward / OOS: given the same sample list it returns the
  same numbers. No I/O, no randomness (docstring lines 6-7).
- ARCHITECTURE LAYER: Research (pure functions; no order authority).
- RESPONSIBILITY: full backtest computation including friction modeling and
  per-axis sensitivity; drawdown/recovery metrics; consecutive-loss runs;
  NaN-tolerant variance-preserving mean used by scoring.
- DEPENDENCIES: `research.models` (BacktestResult, ExecutionAssumptions,
  ResearchSample), numpy, observability.logging (warns on excluded rows).
- CONNECTS TO: backtest.BacktestEngine.run, walkforward (compute_backtest per
  fold), oos (in-sample/OOS), robustness (baseline + stressed runs),
  scoring (fields of BacktestResult).

- KEY CONCEPTS:
  - `_r_array` (lines 23-31): extracts realized R, EXCLUDES non-finite values
    with a `[STRATEGY_RESEARCH] event=NON_FINITE_R_EXCLUDED` warning — thus
    a NaN in the ledger silently shrinks the sample; the count mismatch is
    only visible via logs.
  - `drawdown_metrics` (lines 38-67): cumulative-R equity walk; peak tracking;
    reports drawdown as a POSITIVE magnitude; max_drawdown_usd is the same R
    number reused under a USD-ish name (notional convention); recovery
    duration = trades between peak recovery events, tracked as the drawdown
    in progress at the deepest point. `max_dd_r` equals `max_dd` (both from
    the R curve).
  - `max_consecutive_losses` (lines 70-79): run-length of R < 0.0.
  - `compute_backtest` (lines 82-229): deterministic engine. Friction model:
    friction_points = spread_ticks + slippage_ticks, capped at
    max_slippage_ticks (5.0); per trade R is degraded by
    friction_r = min((friction_ticks * price_tick) / risk_distance, 0.5) —
    i.e. the fraction of planned risk consumed by spread+slip, never more
    than 0.5R; if no risk_distance, an absolute floor 0.01*friction_ticks is
    used. USD degrades by the same fraction (1 - friction_r/|r|, floor 0)
    when |r| > 1e-9. Win/loss cutoff is |r| > 0.0001 else breakeven.
    tail_loss_count counts r <= -1.5. All-non-finite input yields an empty
    zero-trade result (TASK-4: never NaN metrics).
  - Sensitivity (lines 185-195, 232-253): re-runs the full adjusted-R walk
    with +1 tick spread and +1 tick slippage (`_friction_sensitivity`) and
    reports the EXPECTANCY UNDER STRESS (not the delta) as spread_/
    slippage_sensitivity_r; latency sensitivity is a synthetic fractional
    drop: `abs(expectancy_r) * min(latency_ms/60000, 0.05)` — nominal only.
  - Profit factor (lines 177-179): gross win R / |gross loss R|; when no
    losses, PF = gross_win (or 0.0 when no gross win — a flat strategy shows
    PF 0, not inf).
  - `variance_preserving_mean` (lines 256-259): mean ignoring NaN; used by
    scoring for robustness to missing entries.

- HOT PATH / PERFORMANCE: O(n) single pass + at most two sensitivity
  re-passes; backtest is called per fold in walk-forward, so cost is
  O(folds × n); runs on worker cycle, never the tick path.
  Bounded loops only.

- EDGE CASES & PITFALLS:
  - `max_drawdown_usd` is NOT a USD drawdown — it's the R-curve drawdown
    magnitude; the model field name is misleading for consumers.
  - win_rate (models.py) divides wins by total trades; breakevens deflate it.
  - `_friction_sensitivity` returns the stressed mean expectancy, while the
    BacktestResult field name `*_sensitivity_r` suggests a delta — scoring's
    exec_res treats `abs(sp) + abs(sl)` as degradation of a positive value;
    since sp/sl are usually negative-or-positive stressed expectancies, the
    semantics only hold when baseline expectancy is positive.
  - Breakeven trades still contribute 0 to the equity curve and count toward
    max_consecutive_losses (they reset a loss run).
  - Friction is applied even when `pay_spread` is False — the flag is not
    consulted anywhere in this module (ExecutionAssumptions.pay_spread is
    effectively unused).