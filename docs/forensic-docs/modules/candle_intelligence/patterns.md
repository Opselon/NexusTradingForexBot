# src/nexus_scalp/candle_intelligence/patterns.py

- PURPOSE: Candlestick & Chart Pattern Engine (BUG-061) — local-only
  detection of the 29 required patterns with multi-factor context weights
  (trend, volatility, structure, sweep proximity, spread/ATR).
  "A pattern alone is never sufficient: the decision engine requires
  multi-factor confirmation and a non-contradictory candle close"
  (docstring).
- ARCHITECTURE LAYER: Analysis (pattern detection; advisory).
- RESPONSIBILITY: 29-pattern taxonomy over a sliding candle window; raw
  shape fidelity + context weighting into PatternDetection records
  (confidence [0,1]); deterministic (same window -> same detections).
- DEPENDENCIES: CandleCloseClassifier, config, models (PatternDetection,
  RegimeState); stdlib math.
- CONNECTS TO: engine (pattern_engine.detect(self._window, regime)),
  store (record_patterns).
- KEY CONCEPTS:
  - `Candle` (line 28): lightweight __slots__ OHLCV view with body/rng/
    wick/bullish/bearish/body_ratio properties; is_doji(tol).
  - `PatternContext` (line 85): trend (-1..+1), volatility (0..1),
    structure (0..1), sweep_proximity (0..1), spread_atr (0..1) — the
    multi-factor context.
  - `PATTERNS` registry (line 109): the required 29 — HAMMER,
    INVERTED_HAMMER, HANGING_MAN, SHOOTING_STAR, MARUBOZU (1-candle);
    BULLISH/BEARISH_ENGULFING, HARAMI, DARK_CLOUD_COVER, PIERCING_LINE,
    GAP_WINDOW (2); MORNING_STAR, EVENING_STAR, THREE_WHITE_SOLDIERS,
    THREE_BLACK_CROWS (3); RISING/FALLING_THREE_METHODS, DOUBLE_TOP/
    BOTTOM, HEAD_AND_SHOULDERS, INVERSE_H&S, FLAG, PENNANT, WEDGE,
    TRIANGLE (5) — with direction + min candles.
  - `detect` (line 148): <2 candles or non-finite last-5 -> []; context =
    provided or derived; per pattern: skip if window too short; raw =
    _detect_one; skip raw <= 0; confidence = _weight(name, raw, ctx);
    skip below pattern_min_confidence; PatternDetection with
    context_weight = confidence/raw and reason_codes
    [NAME_SHAPE, CTX_TREND_<label>]; requires_confirmation = direction !=
    NEUTRAL; sorted confidence desc.
  - `_weight` (line 263): trend alignment (bullish patterns boosted in
    uptrends: 0.7+0.6*trend; bearish mirror; NEUTRAL uses volatility),
    volatility dampening (1 - 0.4*|vol-0.5| — too low = noise, too high =
    unreliable shapes), structure amplification (0.8+0.4*structure),
    sweep proximity for directional patterns (0.9+0.3*sweep), spread/ATR
    degradation (1 - 0.5*spread_atr); clamped [0,1].
  - `_derive_context` (line 292): trend from last-10 closes normalized by
    max-min span; vol from regime.atr (atr/3 heuristic, clamped) else
    0.5; spread_atr = (spread/atr)*5 clamped; structure fixed 0.6;
    sweep 0 (no sweep input in this module).
  - Shape math (pure, all return 0..1 fidelity): _hammer (small body at
    one end + long wick; body_ratio > 0.35 -> 0; wick/body >= 2 boosts;
    color soft-penalty x0.85), _marubozu (wick_total/range*4 penalty),
    _engulfing (opposite colors + body strictly larger; partial coverage
    x0.6), _star (big first move + small middle + confirm close; score
    base 0.6 +0.2 each for big/gap/confirm), gravestone/dragonfly/
    standard/long-legged doji variants, _three_soldiers (3 same-color,
    rising closes, growing bodies +0.3), _harami (inside body; tiny inner
    body boosts), _cloud_cover (close beyond prev midpoint + penetration),
    _three_methods (strong first/last + contained mid), _double_top_bottom
    (two comparable extremes within 1%), _head_shoulders (head > both
    shoulders), _chart_pattern (convergence of highs/lows; WEDGE slope
    sign check, TRIANGLE flat-side boost, FLAG prior-move/pullback boost,
    PENNANT symmetric-range boost), _gap (cur beyond prev range, sized by
    prev range).
- HOT PATH / PERFORMANCE: O(29 patterns x window) per completed bar (new-
  bar cadence only); pure math, no I/O.
- EDGE CASES & PITFALLS: _detect_one relies on candles[-1] always being
  the current candle — the engine's flat window assumes one symbol/
  timeframe (interleaving corrupts it); defaulted RegimeState (atr=0,
  spread=0) yields spread_atr=0 (tight) and vol=0.5 — benign; _star's
  gap test uses c2.high < c1.close or tiny body — generous; _double_top
  uses max(highs[:3]) and max(highs[2:]) which can both be the SAME
  candle index 2 (overlapping windows) — a two-of-three-lookback
  simplification; FLAG/PENNANT names are chart-pattern approximations,
  not strict textbook definitions.