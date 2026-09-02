# tests/unit/test_accounting_advanced_metrics.py

- GUARDS: Unit tests for `compute_advanced_metrics` (accounting advanced risk math).
- KEY ASSERTIONS:
  - `TestAdvancedMetrics`: empty inputs return None-honest stats; basic stats over known trades; streaks; SQN requires ≥5 R samples; open trades excluded; equity-curve risk (sharpe/sortino) computed on returns; profit factor None when no losses; loss rates derived from PnL (43 asserts).
- PITFALLS IT ENCODES: statistical honesty — undefined ratios stay None, never coerced to 0.0/1.0; sample floors for R-dependent stats are enforced.
- NOTES: Pure-math counterpart of accounting/aggregation.py metrics; used by the API and worker aggregates.
