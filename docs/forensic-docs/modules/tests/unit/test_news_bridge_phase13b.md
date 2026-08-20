# tests/unit/test_news_bridge_phase13b.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 13B news pipeline bridge behavioral suite: 12-field schema completeness, categorical encoding (state/novelty), strict historical causality at T-1/T/T+1.
- Normalize guard: all 12 fields present and NUMERIC; no dead-zero columns with informative input; state/novelty encoded numerically; bullish/bearish aliased from scores; empty frame or None → None (`normalize_news_frame(pl.DataFrame()) is None`).
- Causality: T-1/T/T+1 causal boundaries exact; future event NEVER visible; no news → zero vector (and logic elsewhere gates on it); output matches schema order exactly.
- DB export: `build_frame_from_db` roundtrip; export bounds respected.
- 16 defs / 301 lines; pure polars. Part of the 3-file news_bridge family (contract + finalize variants).