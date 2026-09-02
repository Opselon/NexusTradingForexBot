# src/nexus_scalp/model_generation/schema_v2_incremental.py

- **PURPOSE:** The optimized incremental 70D builder (BUG-106 era, PHASE
  19): `compute_70d_frame_fast` — the same 70D frame computed WITHOUT
  re-running the full liquidity engine per row; an `IncrementalLiquidityState`
  carries pool/liquidity state forward row-by-row, so dataset builds over
  long histories are an order of magnitude faster while staying causally
  identical.
- **ARCHITECTURE LAYER:** ML research (dataset factory — optimization
  track).
- **RESPONSIBILITY:** (a) `sweep_lookback` — efficient pool-lookback
  cursor; (b) `_session_ranges` (decision-ts → session window);
  (c) `IncrementalLiquidityState` — the state machine that maintains
  confirmed pools/strengths/distances incrementally (update per row,
  same outputs as the batch path); (d) `compute_70d_frame_fast` — the
  fast frame builder.
- **DEPENDENCIES:** polars, numpy, liquidity engine types, logging.
- **CONNECTS TO:** dataset builds (PHASE 19 incremental),
  test_70d_bug106_incremental_phase19 (parity + speed regression guard),
  benchmark.
- **KEY CONCEPTS:** The state machine must produce BIT-IDENTICAL outputs
  to the batch builder (parity is the regression test's core assertion —
  the fast path is only an optimization, never a semantic fork); state
  boundaries (session/day rollover) must reset exactly where the batch
  path resets.
- **EDGE CASES & PITFALLS:** Divergence risk is the primary hazard: any
  state carried across a boundary the batch path doesn't carry = silent
  dataset corruption; the parity test (same input → same frame) is the
  non-negotiable gate.