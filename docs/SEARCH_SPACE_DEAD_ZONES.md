# Search-Space Dead Zones Report

**Target Repository:** `NexusTradingForexBot`  
**Branch:** `main` (HEAD `0f6541a`)  
**Scope:** Identify regions of the strategy search space where many candidates are generated but almost none survive, using conservative evidence thresholds (≥50-sample support before any region is flagged).

---

## 1. The Behavioral-Clone Dead Zone (highest confidence)

**Signature:** `(expectancy_r=-0.0607, total_trades=72, profit_factor=0.683)`

| Metric | Value |
|---|---|
| Candidates sharing this exact behavior | **345** |
| OOS passes inside cluster | 0 |
| Share of all evaluated candidates | ~30% |

These are DSLs whose different filter combinations select identical sample subsets on the family-restricted dataset (`ds_36209d0d35c6d5c6`), producing byte-identical trade books. The DSL-level dedup gate (`dsl_hash`) cannot see semantic equivalence.

**Dead-zone rule (evidence-backed):** any new candidate whose projected sample-subset signature matches a known cluster with ≥50 members and 0 passes should be skipped pre-evaluation and counted as `CLONE_SKIPPED`.

## 2. Empty-OOS Structural Dead Zone

45 evaluation runs failed with `"No out-of-sample samples available"` — the candidate's filtered slice consumed the entire dataset, leaving nothing for the holdout.

**Dead-zone rule:** require minimum slice headroom (family slice ≤ N-samples minus required OOS floor) before spending an evaluation slot.

## 3. Family-Level Weak Regions (per-family survival, ≥50 samples each)

| Family | Evaluated | OOS Pass Rate | Verdict |
|---|---|---|---|
| MOMENTUM | 67 | 1.5% | Weak region — reduce sampling weight, do NOT blacklist (sample still small) |
| BREAKOUT | 234 | 4.3% | Below-average survival at scale |
| LIQUIDITY_SWEEP | 155 | 9.0% | Above-average survival — safe to up-weight moderately |
| HYBRID | 16 | 12.5% | Promising but BELOW evidence threshold (n=16) — exploration only |

Conservative adaptation only: no family drops below a sampling floor; no family exceeds its evidence-proportional ceiling.

## 4. Timeframe Coverage Gap

Fingerprints show heavy M30 concentration (`MEAN_REVERSION|M30` 132, `TREND_FOLLOWING|M30` 119, `BREAKOUT|M30` 100) with thinner coverage on M1/M5/M15/H1. No evidence yet that M30 is better — it is simply oversampled. Rebalance toward uniform timeframe coverage until per-timeframe evidence accumulates.

## 5. Explicit Non-Actions (guardrails)

- No region is permanently blacklisted: all rules use `sample_count + recency + diversity impact` with bounded re-trial.
- Dead-zone skipping must be observable (persisted counters) and reversible (config versioned).
