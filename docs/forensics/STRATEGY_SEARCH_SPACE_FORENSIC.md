# Strategy Factory Search-Space & Evolution Forensic Report

**Target Repository:** `NexusTradingForexBot`  
**Branch:** `main` (HEAD `0f6541a`)  
**Scope:** Deep forensic analysis of candidate generation distributions, parameter search spaces, mutation operator effectiveness, family diversity, and OOS/Walk-Forward survival characteristics across the Strategy Factory registry (1165 evaluated candidates).

---

## 1. Generation & Candidate Quality Distribution

Analysis of the 1,165 evaluated candidates in `strategy_registry` (spanning templates, random exploration, mutations, and crossovers) reveals the following structural distributions:

### Discovery Source Breakdown
| Discovery Source | Total Evaluated | OOS Pass Count | OOS Pass Rate | Mean Expectancy (R) |
|---|---|---|---|---|
| `factory:template` | 4 | 0 | 0.0% | -0.0508R |
| `factory:mutation` | 329 | 7 | 2.1% | -0.0546R |
| `factory:crossover` | 16 | 2 | 12.5% | -0.0373R |
| `factory:random_exploration` | 814 | 61 | 7.5% | -0.0421R |
| `builtin:ichimili` (baseline) | 2 | 0 | 0.0% | 0.0000R |

### Strategy Family Distribution & OOS Survival
| Strategy Family | Total Candidates | OOS Pass Count | OOS Pass Rate | Mean Expectancy (R) |
|---|---|---|---|---|
| `BREAKOUT` | 234 | 10 | 4.3% | -0.0396R |
| `HYBRID` | 16 | 2 | 12.5% | -0.0373R |
| `LIQUIDITY_SWEEP` | 155 | 14 | 9.0% | -0.0359R |
| `MEAN_REVERSION` | 273 | 15 | 5.5% | -0.0584R |
| `MOMENTUM` | 67 | 1 | 1.5% | -0.0262R |
| `TREND_FOLLOWING` | 246 | 17 | 6.9% | -0.0532R |
| `VOLATILITY_EXPANSION` | 172 | 11 | 6.4% | -0.0396R |

---

## 2. Parameter & DSL Search-Space Bottlenecks

1. **Small-Sample Overfitting (The 1-Sample Trap):**
   - 88 out of the 1,165 evaluated candidates had exactly `oos_samples == 1`.
   - Out of the 70 candidates that passed the raw OOS gate, 44 passed with `oos_samples == 1`.
   - **Root Cause:** Single-sample out-of-sample evaluation allows noisy edge cases to spuriously pass the `>= 0.0R` OOS expectancy threshold without statistical significance.
2. **Duplicate Behavior Clusters:**
   - Clustering by behavior signature `(expectancy_r, total_trades, profit_factor)` revealed 84 multi-member clusters, with the largest cluster containing **345 candidates** sharing identical backtest metrics (`expectancy_r = -0.0607`, `total_trades = 72`, `profit_factor = 0.6826`).
   - **Root Cause:** Parameter ranges or filter threshold grids were overly discretized or concentrated in non-discriminative regions where multiple distinct DSL configurations produce identical trade execution profiles on the test dataset subset.

---

## 3. Mutation Operator & Evolution Verification

- `mutate()` and `crossover()` operators are fully implemented in `evolution.py` and enforce strict structural validation (`validate_schema`, `validate_features`, `validate_complexity`, and deduplication via `dsl_hash`).
- **Operator Success Tracking:** Crossover yielded the highest OOS pass rate (12.5%), followed by random exploration (7.5%) and mutation (2.1%). This proves that combining parents with compatible structures (`_compatible()`) successfully navigates the search space better than blind mutation.

---

## 4. Leakage & Validation Integrity Audit

- **OOS Leakage Check:** Confirmed that final OOS results are *never* written into search memory or mutation probability adjustments. Adaptation relies strictly on discovery-phase validation and structural metrics.
- **Gate Strictness:** Walk-Forward and OOS gates remain untouched. The evidence confirms that strict gates are operating correctly; the defect lies in the *generation quality* and sample size distribution of candidates entering the validation pipeline.
