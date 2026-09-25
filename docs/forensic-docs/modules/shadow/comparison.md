# src/nexus_scalp/shadow/comparison.py

- PURPOSE: Multi-dimension Champion vs Challenger comparison + explainable
  promotion evaluation with hard vetoes (PHASE 11 spec 7/8/11-15/21/22/23).
  The Challenger must NOT win on one metric: prediction quality, trading
  quality, strategy quality, stability, robustness are decomposed with
  regime/strategy/session breakdowns so critical regressions are NEVER
  averaged away.
- ARCHITECTURE LAYER: Domain (evaluation).
- RESPONSIBILITY: ShadowComparer.compare (aggregation over decisions),
  ShadowComparer.evaluate_promotion (weighted score + vetoes),
  helpers _norm_delta/_calibration/_drawdown/_profit_factor.
- DEPENDENCIES: shadow.models, logging, collections.defaultdict.
- CONNECTS TO: shadow.engine.finish_run (comparison per run), shadow
  worker, promotion evaluation consumers, UI/API.
- KEY CONCEPTS:
  - CHAMPION-SIDE R DERIVATION: both models are scored on the SAME
    simulated price path. hypothetical_r is the Challenger's realized R;
    the Champion's R is the same magnitude with its own directional sign
    — when actions agree, champion_r = hypothetical_r; when they disagree
    (opposing directions on the same path), champion_r = -hypothetical_r
    (one wins, one loses). This is the core comparison math.
  - Regime/strategy/session aggregates: per-slice sums normalized to
    means, delta = challenger_r - champion_r.
  - degraded_strategies: per-strategy delta < MAX_STRATEGY_REGRESSION_R
    (-0.20R) with >= 3 samples; improved_strategies: delta > 0.05.
  - Regime ranking: best/worst 3 by delta (>= 3 samples); a regime is
    DEGRADED if challenger_r < MIN_REGIME_EXPECTANCY_R (0.0, absolute)
    OR delta < -0.20 (relative) — both signals, so a bad regime is never
    averaged away.
  - mfe/mae are the SAME simulated excursion for both models
    (champion_mfe == challenger_mfe).
  - Evidence status: 0 valid → INSUFFICIENT_EVIDENCE; < min_samples →
    EVALUATING; >= min → still EVALUATING (PROMOTION_ELIGIBLE is only
    set via the promotion eval path).
  - _calibration: binned score over non-NO_TRADE/WAIT decisions:
    |accuracy - mean_confidence| → 1 - |·| (correctness = hypothetical_r
    sign vs action direction); NO_TRADE/WAIT decisions excluded.
  - _drawdown: max peak-to-trough of the CUMULATIVE R curve (both
    models use the same hypothetical_r per decision — drawdown is
    path-identical in shape except ordering of same decisions).
  - evaluate_promotion VETOES (single critical veto overrides score):
    insufficient evidence; drawdown_delta > MAX_DRAWDOWN_DELTA_R (3R);
    oos_expectancy_r < 0; robustness_status != "PASS"; challenger
    calibration < champion - MAX_CALIBRATION_DROP (0.15);
    degraded_strategies non-empty (each critical strategy regression);
    challenger_tail_losses > champion + 3 (catastrophic tail degradation);
    invalid_comparisons > 10% of samples.
  - SCORE (weights): 0.30 perf_delta + 0.15 risk + 0.10 drawdown
    (risk_delta duplicate) + 0.15 oos + 0.10 robustness + 0.05
    calibration + 0.05 stability + 0.10 sample_conf, minus strategy
    penalty (min 0.30, 0.10/strategy), clamped [0,1].
  - _norm_delta maps R delta to [0,1] via 0.5 + delta*2.5.
  - sample_conf = min(0.95, observed/(required*2)), 0 when none.
  - eligible = no vetoes AND observed >= required.
- HOT PATH / PERFORMANCE: aggregation runs at run-finalize frequency
  (bounded by run decision count); O(n) per slice.
- EDGE CASES & PITFALLS: _calibration with no tradable decisions returns
  0.0 (a challenger that ONLY says NO_TRADE gets calibration 0, which can
  trigger the calibration veto against a champion — poor-signal
  distortion); champion_r for disagreeing actions assumes the challenger
  is directionally correct in R sign (if both are wrong on the path the
  inversion is still consistent since hypothetical_r is measured from
  the champion's entry semantics, but the champion/entry is actually the
  CHALLENGER's entry proxy); worst_regimes sorted [::-1] after slicing
  the last 3 → order is ascending (worst first) — intentional but
  subtle; drawdown uses per-decision identical R so a disagreement-heavy
  run inflates both drawdowns identically; field champion_drawdown_r
  vs challenger_drawdown_r can differ only through decision ordering
  (same R values) — actually identical sequences, so deltas are usually
  0 (see pitfalls: drawdown delta is near-meaningless given the shared-
  path construction).