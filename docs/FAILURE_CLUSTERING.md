# Failure Clustering Report

**Target Repository:** `NexusTradingForexBot`  
**Branch:** `main` (HEAD `0f6541a`)  
**Scope:** Cluster all 1379 `research_runs` by primary failure cause with counts and percentages, cross-referenced against candidate family and discovery source.

---

## 1. Primary Failure Distribution (all research runs)

| Primary Failure | Count | Percentage |
|---|---|---|
| `OOS` (out-of-sample expectancy below 0.0R) | 1309 | **94.9%** |
| `WALK_FORWARD` (fold stability / degradation failure) | 70 | 5.1% |

**Finding:** OOS failure dominates the funnel. The factory's search space is producing strategies whose in-sample edge does not generalize — the classic signature of overfitting to in-sample data or of a search space concentrated on non-predictive feature combinations.

## 2. Rejection Reason Clusters (top granular reasons)

| Reason (truncated) | Count |
|---|---|
| OOS expectancy `-0.1407R` below minimum; both non-positive | 432 |
| OOS expectancy `-0.1513R` below minimum; both non-positive | 77 |
| OOS expectancy `-0.2346R` below minimum; both non-positive | 75 |
| OOS expectancy `-0.1501R` below minimum; both non-positive | 71 |
| OOS expectancy `-0.1537R` below minimum; both non-positive | 70 |
| "OOS evidence confirms positive edge" (passed OOS, failed later gates) | 70 |
| OOS expectancy `-0.1241R` below minimum; both non-positive | 58 |
| No out-of-sample samples available (empty holdout slice) | 45 |
| OOS expectancy `-0.0996R` below minimum; both non-positive | 38 |

### Interpretation
- The dominant rejection reason is a **shared negative OOS expectancy around -0.14R**, matching the behavioral-clone cluster identified in `CANDIDATE_QUALITY_DISTRIBUTION.md` (345 candidates executing identical trades). A single bad strategy idea was evaluated hundreds of times.
- The 45 `"No out-of-sample samples available"` failures are structural: family-filtered dataset slices too small to leave an OOS remainder — wasted evaluation budget.

## 3. Cross-Tab: Failure × Family

Families with the largest absolute OOS-failure counts are those most heavily generated (`MEAN_REVERSION` 273 candidates, `TREND_FOLLOWING` 246, `BREAKOUT` 234), while per-family survival rates show `LIQUIDITY_SWEEP` (9.0% OOS pass) and `HYBRID` (12.5%) generalize best and `MOMENTUM` worst (1.5%).

## 4. Actionable Signals for the Search Engine

1. **Clone elimination before evaluation** would remove ~30% of run volume that shares one fate.
2. **Slice-size floor before evaluation**: require the family slice to support ≥3 WF folds + non-empty OOS window, else defer/skip (saves the 45 empty-OOS failures).
3. **Family re-weighting toward LIQUIDITY_SWEEP/HYBRID patterns** (evidence: higher survival), keeping MOMENTUM at exploration-only levels until evidence changes.
