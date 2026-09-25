# src/nexus_scalp/research/robustness.py

- PURPOSE: PHASE 09B robustness engine (spec 16/35/38): a strategy must be
  tested under realistic perturbations. Robustness is NOT "still profitable"
  — it is measured as degradation under stress (docstring: +0.44R → −0.12R
  under +1 tick slip is fragile and fails).
- ARCHITECTURE LAYER: Research (pure stress computation; no order authority).
- RESPONSIBILITY: apply the fixed stress scenario matrix on top of baseline
  execution assumptions, recompute expectancy under each, track the worst
  degradation, and emit a PASS/FAIL RobustnessResult with reasons.
- DEPENDENCIES: `research.metrics` (compute_backtest),
  `research.models` (ExecutionAssumptions, ResearchDataset, RobustnessResult),
  observability.logging.
- CONNECTS TO: pipeline stage ROBUSTNESS; status feeds scoring verdict rules
  and registry invariants; factory `_derived_failure_reasons` maps a FAIL to
  ROBUSTNESS_FAILURE.

- KEY CONCEPTS:
  - Ceiling: MAX_ACCEPTABLE_DEGRADATION_R = 0.25 (line 27): max ABSOLUTE R
    drop from baseline before the strategy is FRAGILE.
  - STRESS_SCENARIOS (lines 29-36): six bounded scenarios —
    spread_plus_1, spread_plus_2, slippage_plus_1, slippage_plus_2,
    latency_plus_50ms, latency_plus_150ms (ticks / ms added via
    ExecutionAssumptions.with_perturbation).
  - `RobustnessEngine.evaluate` (lines 52-116): baseline backtest over the
    FULL dataset (no split — robustness is measured on all evidence),
    then one backtest per stressed scenario; degradation = baseline_exp −
    stressed_exp (absolute R drop, so a collapse shows as large positive).
  - Failure rules (lines 87-98): FAIL if max_deg > 0.25R; also FAIL if the
    WORST stressed expectancy is negative AND max_deg > 0.125R (half the
    ceiling) — a strategy that crosses zero under material stress is fragile
    even if the headline drop is modest. Reasons explain both triggers.
  - Status/result: PASS/FAIL + reason ("Robust to modelled stress" default);
    stress_expectancies map kept for visibility.

- HOT PATH / PERFORMANCE: 7 compute_backtest passes over the dataset
  (baseline + 6 scenarios); worker-cycle only.

- EDGE CASES & PITFALLS:
  - `MAX_ACCEPTABLE_DEGRADATION_R` and the half-ceiling trigger at line 94
    are ABSOLUTE drops — a strategy with a 0.1R baseline that turns −0.05R
    degrades 0.15R (< 0.25) and FAILS nothing despite a 150% relative loss;
    conversely a 1.0R baseline losing 0.3R (30% relative) FAILS. The gate
    favors absolute stability over relative edge retention; the scoring
    layer's relative legs live elsewhere.
  - Latency stress is modeled purely through compute_backtest's synthetic
    latency term (metrics.py degrades expectancy by min(latency_ms/60000,
    0.05)) — a "150ms" scenario can change expectancy by at most ~5% before
    saturation; the dimension is more nominal than physical.
  - Stress expectancies below the baseline appear as positive "degradation";
    a strategy that IMPROVES under stress simply contributes a low (possibly
    negative) deg — improvements never fail the gate but never reward it
    either.
  - The 0.25R ceiling is a plugin-default constant; the factory re-checks its
    own max_drawdown/min_trades floors separately (orchestrator
    `_derived_failure_reasons`).