# Near-Miss Analysis Report

**Target Repository:** `NexusTradingForexBot`  
**Branch:** `main` (HEAD `0f6541a`)  
**Scope:** Deep inspection of strategy candidates that nearly passed validation gates (near-miss OOS, OOS pass / WF fail, and highest-scoring candidates).

---

## 1. Near-Miss OOS Analysis

Out of 1165 evaluated candidates, 70 passed the raw OOS gate (`oos.status == 'PASS'`, meaning `oos_expectancy_r >= 0.0`), but failed other downstream gates (such as walk-forward stability or minimum sample evidence floors) resulting in overall `REJECTED` or `INCONCLUSIVE` verdicts.

### The Two Best OOS-Passing Candidates with Meaningful Sample Size (≥8 OOS samples):
1. **`SF-AFDF547340`** (`BREAKOUT`, 32 total BT trades)
   - **OOS Expectancy:** `+0.0268R` over `8` samples
   - **Backtest Expectancy:** `+0.0091R`
   - **Walk-Forward Status:** `FAIL` (average validation vs OOS divergence)
   - **Verdict:** `INCONCLUSIVE` (held back correctly by strict WF stability gates)
2. **`SF-2D709B3B3D`** (`LIQUIDITY_SWEEP`, 34 total BT trades)
   - **OOS Expectancy:** `+0.0229R` over `8` samples
   - **Backtest Expectancy:** `-0.0470R`
   - **Walk-Forward Status:** `FAIL`
   - **Verdict:** `INCONCLUSIVE`

### The 1-Sample OOS Pass Phenomenon
- 44 out of the 70 OOS-passing candidates achieved their positive expectancy over exactly **1 out-of-sample sample** (`oos_samples == 1`).
- These are statistical artifacts rather than true edge; the research pipeline's small-sample confidence cap (`sample_confidence = 0.0` for n < 8, capped at 0.4 for n < 20) correctly prevents them from achieving `VALIDATED`.

### Closest Negative OOS Near-Miss
- **`SF-2A943CAF69`** (`MEAN_REVERSION`): Achieved an OOS expectancy of `-0.0003R` over 3 samples (missed the `>= 0.0R` threshold by a microscopic margin).

---

## 2. OOS Pass / Walk-Forward Fail Discrepancy

- **10 candidates** passed the walk-forward engine (`walkforward.passed == True`), but **all 10 failed the OOS gate** (best among them: `SF-5DB2CA2C98` with OOS `-0.0175R`).
- **Root Cause:** Temporal split geometry and fold definitions differ slightly between walk-forward cross-validation splits and the final OOS holdout window, creating a strict independent barrier.

---

## 3. Characteristics of Near-Misses vs. Catastrophic Failures

| Dimension | Near-Misses (OOS Pass or near 0) | Catastrophic Failures (OOS < -0.15R) |
|---|---|---|
| Strategy Family | `BREAKOUT`, `LIQUIDITY_SWEEP`, `HYBRID` | Heavy concentration in `MEAN_REVERSION` |
| Trade Count | 19–34 trades | 50–72 trades (over-fitted grids) |
| Drawdown | Controlled (< 5R) | High adverse excursion |
