# src/nexus_scalp/accounting/retention.py

- PURPOSE: Evidence-based winner-profit-retention metrics (BUG-081 / Defect #3) — quantifies MFE capture and giveback per trade and across a cohort so any future retention-policy change is driven by measured evidence, never hardcoded thresholds.
- ARCHITECTURE LAYER: Application (pure analytics helpers, no I/O; consumed by reporting/insights and available via accounting exports). This is the per-trade/crossover retention counterpart to the portfolio-level MFE capture computed in reporting/engine.py.
- RESPONSIBILITY: `mfe_capture_ratio`, `giveback`, `giveback_ratio` per trade, and `cohort_capture_report` over (realized_profit, mfe) pairs.
- DEPENDENCIES:
  - `collections.abc.Sequence` → typing for the record sequence.
- CONNECTS TO: Referenced behaviorally by `reporting/insights.py` (MFE-capture insights) and BUG-081 documentation. It is exported implicitly via the accounting package surface (not in `__init__.__all__` — called by module path or imported directly by consumers that need per-trade retention stats; the reporting package computes its own population-level `mfe_capture_ratio` in engine.py:551-555 with documented distinct semantics).
- KEY CONCEPTS:
  - Statistical honesty rule mirrors aggregation.py: every metric is None when the sample cannot support it; MFE <= 0 explicitly yields None capture — a trade can never "capture" a fraction of a non-positive excursion (lines 20-41). NEVER a synthetic 0.0.
  - `mfe_capture_ratio(realized_profit, mfe)` (line 20): `realized_profit / max(mfe, 1e-9)`. Can exceed 1.0 when realized >> MFE (e.g. runner beyond measured peak) — informational, callers decide interpretation; the ratio is raw evidence, not clamped.
  - `giveback` (line 30): `mfe - realized_profit` (how much peak profit was given back) — positive when profit leaked, negative when the trade exceeded its measured MFE.
  - `giveback_ratio` (line 37): `(mfe - realized) / max(mfe, 1e-9)`; None when MFE <= 0.
  - `cohort_capture_report(records)` (line 44): aggregates `(realized_profit, mfe)` pairs into `{sample_trades, profitable_trades, avg_capture_ratio, median_capture_ratio (sorted-middle, even-N pair average), avg_giveback, avg_giveback_ratio, total_mfe, total_realized, worst_capture_ratio}`. Ratios/givebacks are computed per trade then averaged (NOT total_realized/total_mfe) — running a per-trade list comprehension filter for each statistic; all aggregate fields stay None when their supporting list is empty. `total_mfe`/`total_realized` are emitted only when `total_mfe > 0.0` (lines 86, 100-102). Everything is rounded to fixed digits (4 for ratios, 2 for USD).
- HOT PATH / PERFORMANCE: O(n) with three passes over records — fine for cohort sizes bounded by trade ceilings.
- EDGE CASES & PITFALLS:
  - `profitable_trades` counts `realized > 0.0` (line 85) — scratch trades (net within ±0.01) do not count as profitable even though they are not losses either; the report has no scratch bucket.
  - `avg_giveback` averages over trades with MFE > 0 only — a losing trade still contributes its (positive) giveback; `avg_giveback` can therefore be large purely due to losers' MFE, which is by design (retention across the whole cohort).
  - Negative capture ratios are possible when realized_profit < 0 (a losing trade still had MFE) — the raw value is kept, not clamped to 0, so consumers see the full evidence.