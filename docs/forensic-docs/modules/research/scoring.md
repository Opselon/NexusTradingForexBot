# src/nexus_scalp/research/scoring.py

- PURPOSE: PHASE 09B explainable, decomposable Strategy Validation Score
  (spec 17/18/38) — NOT a single win rate. Each dimension is bounded [0,1],
  weighted into a final score, and every reason is exposed. Small-sample
  protection is mandatory (spec 18): 8 trades at +1.2R stays LOW EVIDENCE
  and never HIGH CONFIDENCE.
- ARCHITECTURE LAYER: Research (pure function; no I/O beyond inputs; no
  order authority).
- RESPONSIBILITY: combine backtest + walkforward + oos + robustness results
  into the 10-dimension StrategyScore with hard-gate verdict rules.
- DEPENDENCIES: `research.models` (StrategyScore, results, MIN_EVIDENCE_
  SAMPLES, ResearchDataset), stdlib math.
- CONNECTS TO: pipeline stage SCORING (verdict → lifecycle), registry
  invariants (verdict must be VALIDATED), factory ranking inputs
  (research_score component), web/API score rendering.

- KEY CONCEPTS:
  - Logistic helper `_logistic` (lines 33-37) with OverflowError guard.
    Sample-confidence saturation midway at 60 samples, steepness 0.06.
  - DIMENSIONS (lines 59-149):
    - performance: 0.5 + expectancy_r clamped [0,1]; ≤ 0 → 0.0 with reason.
    - risk: 1 − dd/8, further scaled by (1 − tail_loss_count×0.1); dd > 4R
      adds a reason.
    - stability: logistic(1 − var/2, mid 0.5, steep 4).
    - oos: PASS → 0.5 + oos_expectancy (then × (1 − degradation) when
      in-sample > 0 and degradation positive); FAIL → 0 with reason.
    - robustness: 1 − max_degradation/0.5; FAIL → reason.
    - sample_confidence: hard 0 below 8; logistic below 60 capped at 0.95;
      8-19 capped at 0.4 with LOW EVIDENCE reason; >= 20 full logistic.
    - regime_coverage: distinct regimes / 8 buckets; ×0.85 if UNKNOWN
      present.
    - recency: mean R of the last n//5 trades mapped as 0.5 + recent_exp.
    - execution_resilience: 1 − (|spread_sens| + |slippage_sens|)/0.5.
    - degradation_score: 1 − walkforward.degradation clamped.
  - WEIGHTS (lines 152-163): performance .20, oos .20, risk .15,
    robustness .15, stability .10, sample .08, regime .04, recency .04,
    execution .02, degradation .02.
  - VERDICT hard gates (lines 179-203): OOS != PASS ⇒ REJECTED; n < 8 ⇒
    INCONCLUSIVE; perf<=0 or missing oos/robustness or robustness FAIL ⇒
    REJECTED/INCONCLUSIVE per branch; n < MIN_EVIDENCE_SAMPLES (20) ⇒
    INCONCLUSIVE (TASK-4 never VALIDATED below the floor); walkforward not
    passed ⇒ INCONCLUSIVE; else VALIDATED. Note: REJECTED only fires from
    OOS status in the later branches (robustness FAIL alone yields
    INCONCLUSIVE, keeping the registry invariant's "REJECTED needs a failed
    gate" satisfiable).
- HOT PATH / PERFORMANCE: single pass over dataset for regimes/recency;
  worker-cycle only.
- EDGE CASES & PITFALLS:
  - `recency` uses `n // 5` of the WHOLE dataset while the score is computed
    over the (possibly family-restricted) backtest count n — if the dataset
    passed to compute_strategy_score is the family dataset but n comes from
    backtest.total_trades, the recent window `max(1, n//5)` slices the
    dataset tail, which is consistent only because the pipeline passes the
    SAME family_ds to both.
  - `perf` of exactly 0.0R yields 0.5 (not blocked) since the ≤ 0 branch
    zeroes it — flattish strategies get a 0.5 performance score unless
    expectancy is strictly ≤ 0.
  - sample_confidence cap 0.95 means final score can never reach 1.0 even
    with all gates perfect; verdict VALIDATED does not require any final
    score floor (a 0.5-score strategy can validate as long as its verdict
    rules pass) — the factory's elite selection adds its own >= 0.6 floor.
  - `risk` uses backtest.max_drawdown_r, which metrics computes from the
    ADJUSTED (friction-degraded) R curve — friction raises dd and thus
    lowers the risk score; intended but worth knowing when comparing raw
    runs.