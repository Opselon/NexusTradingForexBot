# TASK-07-70D-LIQUIDITY-RESEARCH — Handoff

> Agent: Hermes-LiquidityResearch · Role: Liquidity Feature Attribution /
> Regime Research / Shadow Intelligence · 2026-08-19
> Task: 7 / 10 · Status: COMPLETED (feature-level) — model-level ablation
> benchmark running at handoff time
> Research baseline: e85de540e09d3339

---

## 1. What was delivered

1. **Research baseline freeze** (mission 3): `research_baseline.json` with
   research_baseline_id, frozen versions (liquidity-v1.0 / v1.1 candidate),
   schema/liquidity-engine sha256 hashes, code commit, and honest
   not-frozen list (model_id/dataset_id/news/shadow/parity).
2. **Feature attribution** (mission 5/6/23/24): distributions, coverage,
   missingness, saturation, stability, redundancy (spearman matrix, 45
   pairs), family aggregation, model-free importance proxy (OOS-safe by
   construction), per-feature + per-family scorecard.
3. **Version isolation v1.0 vs v1.1** (mission 43): identical causal inputs,
   400 points; eqh delta -0.31, confluence sat 0.9975->0.4325 uniq 2->225,
   sweep 65% rows changed, distance family bit-identical.
4. **Session analysis** (mission 9): 5 canonical sessions, 500 causal M5
   points; similar liquidity behavior across sessions (feature level).
5. **Regime analysis** (mission 8): trend/range proxy segmentation.
6. **Event studies** (mission 11-14, 18): 1000 strictly-causal events,
   horizons 3/5/10/15/30; sweep (no directional edge), confluence (high =
   calm, not better), distance (far = bigger moves), HTF (no dominance).
7. **News x Liquidity** (mission 10): INSUFFICIENT_OVERLAP documented.
8. **Shadow disagreement** (mission 16/17/32): INSUFFICIENT_LIVE_EVIDENCE
   (2 blocked observations).
9. **Drift analysis** (mission 19-22): PSI + mean/std shift; CRITICAL flags
   explained as window/version artifacts, not market drift.
10. **Model error attribution** (mission 27): NOT_COMPUTABLE + executable
    framework for when the model lands.
11. **Research hypotheses** (mission 25/26): HYP-LIQ-001..005, RESEARCH_ONLY.
12. **A/B/C benchmark execution** (mission 7): driver path bug fixed
    (parents[2] -> parents[1]); run launched on 6000 rows.
13. **Report + handoff**: docs/LIQUIDITY_70D_RESEARCH_REPORT.md.

## 2. Key findings (feature-level, PROVEN)

- Confluence v1 is degenerate (const 2.999, uniq 2); v1.1 fixes it.
- Sweep v1 floods (+1 for 40% of rows, no relevance gate); v1.1 gates it.
- EQH/EQL v1 are step-functions (51%/92% zeros); v1.1 closeness fix.
- Sweep/confluence carry volatility, not direction (event studies).
- High confluence => LOWER forward move (H5 1.12 vs 1.68) — "more
  confluence = better" NOT supported.
- Distance-to-liquidity: far => larger moves (monotone).
- HTF sign does not dominate.
- All feature-level; model-level verdict PENDING (benchmark running).

## 3. Files

- scratch/task07_*.py (scripts)
- scratch/task07_research/*.json (artifacts, tracked)
- docs/LIQUIDITY_70D_RESEARCH_REPORT.md
- agents/taskboard.md (TASK-07-70D-LIQUIDITY-RESEARCH row)
- agents/repository_state.md (snapshot)
- scratch/bench_70d_abc_driver.py (path fix only)

## 4. Verification

- TASK-01 contract suite: 27 passed
- TASK-06 opt suite: 23 passed
- All research scripts RC=0, JSON valid, run identity embedded
- No production code changed; no parameters mutated; no rules created;
  no auto-training; no auto-promotion (mission 26/46/47)

## 5. Bugs

None appended (v1.0 degeneracies already proven+fixed by TASK-06; driver
path bug fixed and documented).

## 6. EXACT NEXT-AGENT INSTRUCTIONS (TASK-8)

1. Read docs/LIQUIDITY_70D_RESEARCH_REPORT.md (the full synthesis).
2. Check artifacts/model_generation/liquidity_research/benchmark_70d_abc.json
   — if present, record the A/B/C verdict as the model-level ablation;
   if absent/failed, re-run `python scratch/bench_70d_abc_driver.py`.
3. Rebuild the drift reference on the SAME window as the golden baseline
   (first 5 months) with v1.1; expect bsl/ssl CRITICAL to drop to
   NORMAL/WATCH. Never disable features on drift alone.
4. When the 70D candidate trains: run model_error_attribution.json's
   framework (error classes x liquidity states).
5. Accumulate >= 50 valid shadow70 observations, then do disagreement
   outcome analysis (mission 17).
6. Build the aligned News x Liquidity 70D dataset and fill the 4-state
   interaction table.
7. Move HYP-LIQ-001..005 through governance (EVALUATING -> VALIDATED/
   REJECTED) with model-level evidence. NO auto-promotion.
8. Record liquidity_algorithm_version in every model manifest; never mix
   v1.0/v1.1 outputs (mission 43).
9. Do NOT convert event-study findings into trading rules (mission 26).
