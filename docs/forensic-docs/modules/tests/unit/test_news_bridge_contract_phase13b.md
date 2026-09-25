# tests/unit/test_news_bridge_contract_phase13b.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 13B news bridge CONTRACT extension suite (spec 26): causal boundary duplicates, Windows timestamp safety, categorical unknowns, NaN/Inf protection, malformed/empty/multiple-source DB export, quality diagnostics, benchmark readiness gate.
- Causal boundary: identical-timestamp events deterministic; future event STRICTLY invisible to T (no peek-ahead).
- Windows timestamp safety: polars scalar conversion never raises OSError (Windows libc date-boundary trap); numpy datetime64, naive-datetime-as-UTC, ISO strings and bad values covered.
- Categorical: UNKNOWN state and novelty encode deterministically; NaN/Inf never enter the vector.
- No-news-DB safety: empty DB export → empty frame (`frame is None or frame.is_empty()`); missing news readiness gate = RED (benchmark blocked, honest).
- Malformed DB rows and multiple-source exports normalized without crashing.
- 28 defs / 439 lines; pure polars/NumPy pipeline, no DB engine.