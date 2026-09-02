# src/nexus_scalp/candle_intelligence/classifier.py

- PURPOSE: CandleCloseClassifier — the heart of the candle-intelligence
  module (BUG-061): converts one COMPLETED candle into a full close-quality
  classification. "The candle close is a GATE — every downstream decision
  (entry / hold / fast-exit / no-trade / modify / cancel) consumes this
  summary."
- ARCHITECTURE LAYER: Analysis (pure, stateless close-geometry
  classification).
- RESPONSIBILITY: geometry ratios + five component scores + the close
  classification ladder + close-quality label; deterministic (identical
  input geometry -> identical summary).
- DEPENDENCIES: config (CandleIntelligenceConfig thresholds), models
  (CandleCloseClass, CandleCloseSummary), stdlib math.
- CONNECTS TO: engine.process_candle_close (classifier.classify), decision
  engine (consumes the summary), store (record_candle_closure), PatternEngine
  (the same config).
- KEY CONCEPTS:
  - `classify` (line 33): validates input first (`_invalid_summary`);
    geometry: range = high-low, body = |close-open|, upper/lower wicks;
    ratios all division-by-range guarded by rng > 1e-12 (else 0.0;
    close_position_in_range falls back to 0.5);
    direction UP/DOWN/FLAT.
  - Component scores (all [0,1], rounded 6dp):
    - close_strength: close_position_in_range for UP (1 - cpos for DOWN,
      0 for FLAT) — how decisively price closed toward the candle's own
      edge.
    - rejection_score: counter-wick against body direction normalized by
      long_wick_ratio (doji: total wick presence).
    - continuation_score = min(1, body_ratio * close_strength * 2).
    - reversal_score (shape-based only — prior-direction reversal is the
      decision engine's job): max counter-wick * (1-body_ratio) * 2.
    - indecision_score: (1-body_ratio)*(upper+lower wick)*1.5.
    - momentum_decay_score: same wick math as rejection (retrace from the
      extreme reached during the candle).
  - `_classify` ladder (line 158): body_ratio <= weak_body_ratio AND
    wicks > body -> INDECISION (doji family dominates). UP: upper_wick >=
    long_wick_ratio -> body < strong_body_ratio ? TRAPPED_BREAKOUT :
    EXHAUSTION; close_strength >= continuation_threshold ->
    BULLISH_CONTINUATION; >= weak_body_ratio -> WEAK_CLOSE; else
    INDECISION. DOWN mirrored. FLAT -> INDECISION.
  - `_quality` (line 197): STRONG for continuation classes with
    body_ratio >= 0.5; GOOD for continuation; NEUTRAL for INDECISION/
    WEAK_CLOSE; INVALID for INVALID; WEAK for everything else
    (TRAPPED_BREAKOUT/EXHAUSTION/reversal classes).
  - `_invalid_summary` (line 226): None/NaN/Inf in OHLC, high < low, or
    high-low <= min_candle_range (1e-9) -> INVALID summary with zeroed
    geometry and flags — "malformed input is rejected with
    CandleCloseClass.INVALID rather than crashing" (docstring).
- HOT PATH / PERFORMANCE: pure float math per completed bar (new-bar
  cadence, not per tick) — negligible cost.
- EDGE CASES & PITFALLS: the ladder never emits BULLISH_REVERSAL /
  BEARISH_REVERSAL / FALSE_BREAKOUT (declared in the enum; the decision
  engine handles them defensively); a doji check compares wick-sum > body
  ratio — a tiny-bodied candle with no wicks (weak_body_ratio boundary)
  falls through to direction branches; FLAT with zero wicks still yields
  INDECISION via the direction fallback; INVALID summaries keep the input
  OHLC when finite (else 0.0).