# src/nexus_scalp/features/scalp_features.py

- **PURPOSE:** The 50D master feature engine — the hot-path calculation that
  turns completed M1 bars + the current tick into the exact tensor ScalpNet
  consumes. This is the heart of the system's "market state interpretation":
  every tick the engine must describe the market in 50 numbers that are
  causal (no future information), finite, and bounded to [-3, +3].
- **ARCHITECTURE LAYER:** Features (Application-adjacent; invoked on the tick
  hot path by LiveEngine).
- **RESPONSIBILITY:** (a) compute the 50D vector per the verified
  `FEATURE_NAMES` contract (the executable contract supersedes all docs —
  forensic note: skill §5.5 table is the ONLY truth; historical docs claiming
  MACD/Bollinger/ADX/OBV/VWAP are false); (b) guarantee determinism + causality
  (verified: determinism ×100 PASS, causality T-1 PASS, live=replay=training
  parity); (c) fail soft with explicit fallbacks so a bad bar never freezes the
  engine but never silently pollutes inference.
- **DEPENDENCIES:** numpy (vectorized bar math), pydantic (`FeatureVector`,
  frozen), `domain.models.TickData`, `market_data.bar_aggregator.BarData`,
  `observability.logging`. Self-contained helpers: `aggregate_bars()` (M1→
  M15/M30/H1/H4 via simple resampling) and `find_support_resistance_levels()`
  (fractal window-3 level scan).
- **CONNECTS TO:** LiveEngine (per-tick), trainers/dataset builders (feature
  parity with live), Debug Hub (`/api/debug/features`), the 60D/70D paths
  (schema_augment builds on these 50), experience memory (FeatureSnapshot
  stores the same 50D), and governance alignment verifiers.
- **KEY CONCEPTS:**
  - **The 4-stage pipeline:** (1) `aggregate_bars` / HTF aggregation (M15/M30/
    H1/H4 from the same completed M1 bars — multi-timeframe context without
    extra broker calls); (2) per-tick `compute_from_bars` — 10 feature groups:
    price-action anatomy, swings/patterns, sessions, lags, ICT/SMC, Ichimoku,
    indicators/stat-arb, MTF context, S/R zones, institutional OB validation;
    (3) `FeatureVector.to_tensor_input()` — the ONLY sanctioned 50-float
    serialization: it applies ATR-normalization (safe_atr = max(atr, 0.20)),
    boolean→float coercion, NaN/Inf→0.0 sanitize, [-3,+3] clip, and asserts
    length == 50 (contract violation raises — never emits a 49/51 vector);
    (4) `validate_and_fallback` — atr_m1 NaN/Inf → 1.50 (hard-coded fallback;
    see pitfalls).
  - **Cold start:** `_cold_start_vector()` — below 55 bars the engine emits a
    neutral-ish vector (rsi 50, atr 1.50, kumo at mid, S/R dists 3.0 = maxed)
    instead of crashing. Neutral values are chosen so the model sees "no
    information" rather than zeros (zeros would read as extreme bearish
    signals). The htf_h4 fallback chain (≥3 bars EMA vs ≥1 bar point-vs-point)
    shows the same philosophy at HTF granularity.
  - **FVG semantics:** bullish FVG = gap between bar-3 low and bar-1 high
    (`lows[-1] - highs[-3] > gap`), DISPLACED BY 2 — the standard ICT
    definition looking back one bar; depth is normalized as distance in ATR,
    and the SAME depth feeds feat_26 (fvg_sig) — the "zone quality" feature —
    so a deep gap reads as a stronger zone. `fvg_mitigation_sensitivity`
    (hot-reloadable via runtime config, synced each tick) scales the gap
    threshold: larger sensitivity → deeper gap required.
  - **Session features** use tick-timestamp UTC hours (Tokyo 0-8, London 7-15,
    NY 13-21, overlap 13-15) — hard boundaries, deliberately simple.
  - **SMC block:** OB classification (bullish if close[-1] > high[-2] and
    prior bar bearish; mirrored bearish), strength = type × volume/vol_mean,
    swing scan (window = order_block_lookback_bars, ±5 fractal), 50% impulse
    equilibrium ratio (clamped 0..1), BOS validation (close beyond prior
    swing = 1.0; CHoCH/break = 0.5; else 0), liquidity-swept flag, and
    continuous Fibonacci 50-60% OTE alignment `clip(1 - |eq-0.55|/0.35, 0, 1)`.
  - **Ichimoku:** tenkan 9 / kijun 26 / span B 52 with PREVIOUS-period cross
    detection (`prev_tenkan`/`prev_kijun` from shifted windows) — cross signals
    only on actual cross, not level.
- **HOT PATH / PERFORMANCE:**
  - Runs on EVERY tick (LiveEngine `_process_tick_pipeline`). Uses numpy
    windowed ops; O(55) bar slices; HTF aggregation is O(n·(m/period)) per
    tick — `aggregate_bars` re-scans the full bar list 4× per tick (M15/30/H1/
    H4), the single most expensive line in this module. The 4 HTF aggregations
    re-run identical prefix work each tick; a cached/incremental aggregation
    would cut this (P3 candidate, see issues ledger).
  - RSI here is a SMA-of-gains/losses approximation of Wilder's RSI (simple
    mean, not Wilder smoothing) — deliberate simplicity for determinism; the
    divisor `16.66` in norm_rsi is the documented quirk (BUG-082: docs said
    /25, code says /16.66 — code is truth).
  - `cross_asset_z_score` appends the CURRENT mid to the last 19 closes
    (rolling 20-window) — the only feature that mixes tick+bar in its window;
    this is intentional "tick-aware z-score" but means feature stability
    differs from pure bar features.
- **EDGE CASES & PITFALLS:**
  - **_cold_start_vector is the guard for warm-up**: <55 bars → cold start;
    the S/R fallback (nearest_support=None → min(low)) also protects
    degenerate windows.
  - **Dead expression at line 879**: `last_sl_val + 0.50 * (last_sh_val - last_sl_val)` is
    a pure expression with no assignment/side effect — the 50% impulse level
    is computed but discarded (only `ob_equilibrium_ratio` is used later).
    Harmless but dead code; a reviewer should not mistake it for the used
    equilibrium path.
  - **Hard-coded fallback ATR 1.50** on NaN/Inf (in `validate_and_fallback`
    AND cold start) is a MAGIC number: if XAUUSD volatility regime changes
    materially, a 1.50-ATR placeholder biases normalization. It is bounded by
    safe_atr = max(atr, 0.20) only in the tensor path, so a cold-start vector
    feeds the model "ATR=1.5" normalization — acceptable warm-up behavior,
    but worth an explicit constant + rationale.
  - **log(n) guards**: `closes[-2] > 0` style checks exist (log of 0/negative
    guarded), dividing by `safe_atr` (≥0.20) everywhere — no div-by-zero.
  - **Boolean fields in FeatureVector**: is_doji/is_hammer etc. are stored as
    bool but serialized to 0.0/1.0 in the tensor — the bools are diagnostics,
    the floats are the model input; keep the mapping (they diverge only in
    to_tensor_input).
  - **BUG-082 forensics**: `norm_rsi` divisor is 16.66 (NOT 25); feat_38
    (norm_dist_to_tenkan) and feat_39 (norm_dist_to_kijun) are EXACT
    negations (corr -1.0 over stored experiences) — the model sees a
    redundant dimension pair; harmless but a known quality observation.