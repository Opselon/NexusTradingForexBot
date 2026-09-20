# AGENT-BACKTEST — ML-BT-001 Trading Quality Metric Suite

**Task:** ML-BT-001 — Trading Quality Metrics vs Classification (STREAM I, P1)
**Specialist:** AGENT-BACKTEST
**Branch:** `agent/backtest/ml-bt-001` (worktree `/tmp/wt-backtest-ml-bt-001`, off `origin/main` @ `463f5e1f`)
**Status:** **DONE — PR opened**

## What was built

A new pure/deterministic module separating economic trading metrics from
classification metrics, so a model can no longer be promoted on accuracy alone.

`src/nexus_scalp/research/trading_metrics.py` (new):

| Symbol | Purpose |
|---|---|
| `TradeRecord` | Input unit — one CLOSED trade. `realized_r` required; `realized_pnl_usd`, `risk_distance`, friction paid, commission optional. Frozen pydantic model; rejects non-finite at construction. |
| `EconomicMetrics` | The report object: expectancy R, win rate, avg win/loss R, profit factor (+capped), max drawdown R/USD/pct, Calmar, turnover, t-stat, friction-adjusted expectancy, slippage decay, classification half carried alongside, provenance. |
| `SlippageDecayPoint` | One point of the expectancy-vs-friction curve. |
| `calculate_economic_metrics(trades, assumptions=None, *, annualization_factor, classification_metrics)` | Pure, deterministic, no I/O. Raises `ValueError` on empty or non-finite input. |
| `compute_slippage_decay(trades, *, assumptions, grid_ticks)` | Expectancy degradation across `[0,1,2,3,5]` extra ticks (the task grid), canonical friction model. |
| `attach_classification_metrics(metrics, cls)` | Returns a copy with the classification half attached alongside (NON_GOALS: separation, not replacement). |
| `economic_metrics_to_report(metrics)` | Canonical JSON report — `metrics.classification` and `metrics.economic` side by side, benchmark-report compatible. |

Constants: `FRICTION_R_CAP = 0.5` (identical to the clamp in
`research.metrics._friction_sensitivity` / `compute_backtest`),
`DEFAULT_SLIPPAGE_GRID_TICKS = (0.0, 1.0, 2.0, 3.0, 5.0)`,
`PROFIT_FACTOR_INF_CAP = 99.0` (same no-loss convention as `experience.evaluator`).

## Acceptance criteria — both met

1. **`calculate_economic_metrics()` passes all mathematical unit tests** — 70/70
   green. Every expected value hand-computed from canonical institutional
   definitions (PF = gross profit / gross loss; expectancy = mean R; drawdown =
   peak-to-valley of the cumulative R curve; Calmar = annualized return / max
   drawdown), none derived from the implementation under test.
2. **Slippage decay curve computes expectancy degradation across tick friction** —
   16 dedicated tests: the task grid, per-level arithmetic against the canonical
   friction model, monotonic expectancy/degradation, the `0.01 * ticks` linear
   fallback when no risk distance is recorded, the 0.5R ceiling saturation case,
   and a flat curve when no `ExecutionAssumptions` are supplied.

## Verification evidence (all executed, not claimed)

| Gate | Command | Result |
|---|---|---|
| Unit tests | `PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/unit/test_trading_metrics.py -p no:xdist` | **70 passed in 0.31s**, 0 failed |
| Ruff lint (repo-wide) | `ruff check .` | `All checks passed!` |
| Ruff format (repo-wide) | `ruff format --check .` | `2203 files already formatted` |
| mypy | `mypy src/nexus_scalp/research/trading_metrics.py` | `Success: no issues found in 1 source file` |
| Manifest | `python scripts/ci/verify_critical_suite_manifest.py` | `CRITICAL_SUITE_MANIFEST_OK: 211 paths all exist` (new test registered) |
| BENCHMARK_PLAN | 10,000 simulated trades | **14.7 ms** (budget 50 ms) |
| BENCHMARK_PLAN | 5-level decay curve on 10,000 trades | **< 50 ms** |
| Neighbour regression | `test_bug299_friction_cap_saturation` + `test_zero_friction_guard_e1` + `test_purge_embargo_monotonicity` + `test_sample_weights` | **61 passed**, 0 failed |

Sample report shape (10 trades, 1+1 tick friction, classification attached):

```json
{
  "metrics": {
    "classification": { "accuracy": 0.65, "macro_f1": 0.41, "n": 10 },
    "economic": {
      "total_trades": 10, "win_rate": 0.5, "wins": 5, "losses": 5,
      "expectancy_r": 0.45, "avg_win_r": 2.0, "avg_loss_r": -1.1,
      "profit_factor": 1.818182, "profit_factor_capped": 1.818182,
      "max_drawdown_r": 2.0, "max_drawdown_usd": 200.0, "max_drawdown_pct": 0.5,
      "calmar_ratio": 56.25, "turnover_r": 15.5, "avg_abs_r": 1.55,
      "expectancy_t_stat": 0.810771, "expectancy_std_r": 1.755151,
      "net_expectancy_r_after_friction": 0.43, "friction_r_per_trade": 0.02,
      "slippage_decay": [
        {"added_ticks": 0.0, "friction_r_per_trade": 0.02, "expectancy_r": 0.43,
         "degradation_r": 0.02, "degradation_pct": 0.044444},
        {"added_ticks": 5.0, "friction_r_per_trade": 0.07, "expectancy_r": 0.38,
         "degradation_r": 0.07, "degradation_pct": 0.155556}
      ]
    }
  },
  "metric_families_separated": true
}
```

The two profitability paradoxes from the task's WHY_IT_EXISTS are pinned as
named tests: 90% win rate with fat tails → PF < 1 and negative expectancy;
40% win rate with 3:1 payoff → PF 2.0 and positive expectancy.

## Contract discoveries (must be honoured by downstream tasks)

1. **Drawdown peak convention — the repo oracle is `peak_t = max(0, cum_0..cum_t)`.**
   `research.metrics.drawdown_metrics` (metrics.py:40-83) initialises `peak = 0.0`
   and updates it AFTER booking the trade (`if cum > peak: peak = cum`), so the
   peak is a 0-floored running high inclusive of the current trade. This module is
   byte-identical to it, pinned by a **200-sequence randomized cross-check**.
   BOTH naive formulas are wrong:
   - bare `np.maximum.accumulate(cum)` (no 0 floor) anchors the peak at the first
     negative cumulative value and UNDER-reports a losing sequence:
     `[-1,-2,-3]` → dd 5.0R instead of 6.0R;
   - a strictly left-aligned peak (`0..t-1`) OVER-reports a winner:
     `[2,-1,3,-1,-1]` → dd 4.0R instead of 2.0R.
   Any future drawdown change must keep three implementations in lockstep:
   `metrics.drawdown_metrics`, `shadow.comparison._max_drawdown`, this module.

2. **The friction ceiling is 0.5R and is NOT relaxed here.** A single trade can
   never lose more than `FRICTION_R_CAP = 0.5` R to spread+slippage
   (`min(friction_frac, 0.5)`), so the MAXIMUM measurable expectancy degradation
   is exactly 0.5R regardless of tick count. This matches the ML-VAL-003 finding
   on `compute_backtest`. Downstream robustness/gate tasks must treat 0.5R as the
   theoretical ceiling, not a tunable.

3. **Relative degradation reuses the canonical helper.** The decay curve's
   `degradation_pct` calls `research.metrics.compute_relative_degradation`
   (metrics.py:86, BUG-140 Phase 6 — epsilon guard + clip) instead of inventing a
   second ratio, so this curve can never disagree with the OOS gate family.

4. **`max_drawdown_pct` is only meaningful for a positive peak.** When the
   sequence never went positive there is no positive equity base, so the honest
   bound is `1.0` — never `0.0`, which would misreport a loser as drawdown-free.
   `max_drawdown_r` remains the authoritative number.

## Files changed

- `src/nexus_scalp/research/trading_metrics.py` (new)
- `tests/unit/test_trading_metrics.py` (new, 70 tests)
- `tests/critical_suite.txt` (+1 manifest entry)
- `docs/ml-system/tasks/ML-BT-001.md` (criteria [x], evidence, STATUS DONE)
- `docs/ml-system/TASK_BOARD.md` (ML-BT-001 → DONE)
- `docs/ml-system/06_TASK_LEDGER.md` (ML-BT-001 → DONE)
- `agents/taskboard.md` (+1 DONE row)

## Scope discipline

Changes confined to the declared `OWNERSHIP_SCOPE`
(`src/nexus_scalp/research/trading_metrics.py`) plus its test battery and the
task-bookkeeping files. No existing source file was modified — the module only
*reads* `research.models.ExecutionAssumptions` and reuses
`research.metrics.compute_relative_degradation`. `RemoteMT5GatewayAdapter`,
`_process_tick_pipeline`, workflows and frozen domain models untouched.
`agents/locks.yaml`: no lock was needed (scope is a brand-new file with no
contested consumer).
