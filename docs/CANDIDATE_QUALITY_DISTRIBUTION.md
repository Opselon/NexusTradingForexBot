# Candidate Quality Distribution Report

**Target Repository:** `NexusTradingForexBot`  
**Branch:** `main` (HEAD `0f6541a`)  
**Scope:** Statistical distribution of the 1165 evaluated candidates in `strategy_registry` — expectancy, trade counts, OOS samples, WF behavior, and pathological clusters.

---

## 1. Backtest Expectancy Distribution (n=1163)

| Statistic | Value |
|---|---|
| Min | -0.7010R |
| P25 | -0.0607R |
| Median | -0.0607R |
| P75 | -0.0504R |
| Max | +0.8227R |
| Mean | -0.0456R |

**Finding:** The median candidate LOSES money in-sample. The distribution is heavily concentrated around -0.06R, indicating most candidates share near-identical mediocre trade profiles (see duplicate clusters below).

## 2. Trade Count Distribution

| Trades Bin | Candidate Count |
|---|---|
| < 5 trades | 96 |
| 5–20 trades | 138 |
| 20–50 trades | 310 |
| 50–100 trades | 619 |

**Finding:** 234 candidates (~20%) have fewer than 20 trades — statistically meaningless sample sizes that cannot support edge claims.

## 3. OOS Sample Distribution — THE CRITICAL DEFECT

OOS samples across all candidates: `{18: 409, 19: 145, 9: 99, 1: 88, 7: 69, 20: 58, 2: 50, ...}`

**Of the 70 raw OOS PASS candidates, the OOS sample counts were:**

| OOS Samples | Pass Count |
|---|---|
| **1** | **44** |
| 2 | 8 |
| 3 | 6 |
| 4 | 4 |
| 5 | 4 |
| 6 | 2 |
| 8 | 2 |

**63 of 70 (90%) OOS passes rest on ≤5 out-of-sample trades.** Only 2 candidates passed with ≥8 samples:
- `SF-AFDF547340`: OOS +0.0268R over 8 samples (BT exp +0.009R, WF FAILED)
- `SF-2D709B3B3D`: OOS +0.0229R over 8 samples (BT exp -0.047R, WF FAILED)

Both were correctly held at `INCONCLUSIVE` by the walk-forward gate.

## 4. Duplicate Behavior Clusters

Signature = `(expectancy_r, total_trades, profit_factor)` rounded:

- Unique signatures: 305 across 1163 scored rows
- Multi-member clusters: 84
- **Largest cluster: 345 candidates sharing IDENTICAL metrics (-0.0607R / 72 trades / PF 0.683)**

These 345 "different" strategies execute the exact same trades on the family-filtered dataset slice — they are behavioral clones wasting evaluation budget. The DSL-level dedup (`dsl_hash`) does NOT catch semantic equivalence because different filter combinations can select identical sample subsets.

## 5. Walk-Forward Behavior

- Folds distribution: 982 candidates with 3 folds; **181 candidates with 0 folds** (auto-fail)
- Zero-fold failures are structural: family-restricted dataset slices too small for fold construction
- Only 10/1163 candidates pass WF; all 10 then fail OOS (best OOS among them: -0.0175R)

## 6. Pathological Clusters Summary

| Cluster | Count | Pathology |
|---|---|---|
| Behavioral clones | 345+66+65+... | Wasted eval budget; no diversity value |
| 1-trade OOS passes | 44 | Statistically void positive OOS |
| Sub-20-trade books | 234 | Below evidence floor for VALIDATED claims |
| Zero-fold WF | 181 | Family slice too small for validation geometry |
