# tests/unit/test_liquidity_engine_causality.py

- GUARDS: TASK-01-60D-LIQUIDITY — causality & anti-leakage gold-standard tests (TEST-LIQ-12/13, 18-28, TEST-60D-BASE-01): features at T computed from bars through T MUST equal features at T computed from bars through T+N (once the confirmation bar passes).
- KEY ASSERTIONS:
  - causal invariance across the confirmation boundary; no future bar influences features at T; known swing/touch/sweep timestamps exact per the fixture; liquidity dims stable once confirmed (40 asserts).
- PITFALLS IT ENCODES: this is THE anti-leakage suite — any future-data dependence in the liquidity calculations fails here; fixtures engineered so swings appear at exact indices (monotonic-ramp building blocks).
- NOTES: Uses tests/helpers/liquidity_fixtures.py.
