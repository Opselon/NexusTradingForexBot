# tests/unit/test_scalp_features.py + test_scalp_features_forensic_bug082.py + test_bar_aggregator.py

# test_scalp_features.py
- **GUARDS:** The 50D master feature engine (features/scalp_features.py) —
  computation accuracy across all 10 feature groups.
- **KEY ASSERTIONS:** expected values for price-action anatomy (wick/
  body ratios, doji, engulfing), swing/S-R distances, session flags
  (UTC-hour boundaries), lags/ATR/vol-z, ICT/SMC signals, Ichimoku
  (tenkan/kijun/kumo/cross), indicators (RSI, EMA distances, z-score),
  MTF context (H4/H1/M30/M15), SMC OB validation; cold-start behavior
  (<55 bars); determinism (same bars+tick → identical vector).
- **PITFALLS IT ENCODES:** any change to a formula breaks the pinned
  expectations (the executable contract — docs were historically wrong,
  e.g. norm_rsi /16.66 not /25).

# test_scalp_features_forensic_bug082.py
- **GUARDS:** The BUG-082 forensic contract: 7 fixtures × 50 dims =
  350/350 PASS, determinism ×100, causality T-1, dataset/live replay
  parity, float32 model-input roundtrip err ≤ 8.6e-8.
- **KEY ASSERTIONS:** every one of the 50 dims independently verified;
  feat_38/feat_39 exact-negation pinned; no MACD/BB/ADX/OBV/VWAP exist in
  the 50D (docs-claims-vs-code rejection).

# test_bar_aggregator.py
- **GUARDS:** market_data/bar_aggregator — tick→M1 aggregation.
- **KEY ASSERTIONS:** completed-bar minting on minute rollover; forming
  bar OHLC updates; duplicate same-minute ticks don't double-mint;
  history reseed REPLACE+ALIGN (BUG-058: no blind append; forming bar
  seeded from latest close); timestamp handling (UTC, no host clock).
- **PITFALLS IT ENCODES:** a late tick from a previous minute must not
  retro-mutate a completed bar.