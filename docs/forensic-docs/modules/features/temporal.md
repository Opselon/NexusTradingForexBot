# src/nexus_scalp/features/temporal.py

- **PURPOSE:** TASK-TEMPORAL-01 research candidate: 22 causal temporal
  liquidity dimensions (lag1/lag2/delta1/persistence/time-since-change over
  the canonical liquidity 10D) extending the 70D contract to the 92D
  `scalp_v4_temporal_candidate` (research-only; never ACTIVE/CHAMPION).
- **ARCHITECTURE LAYER:** Features (research track, strictly causal).
- **RESPONSIBILITY:** (a) `temporal_features_from_history(liquidity10_history)` —
  given a HISTORY LIST of prior liquidity 10D vectors, compute the 22
  temporal features (per liquidity dim: lag1, lag2, delta1, persistence,
  time-since-change) with `_neutral_for` fallbacks when history is short;
  (b) `TemporalLiquidityTracker` — an online state machine that ingests each
  new liquidity 10D + timestamp and returns a `TemporalLiquiditySnapshot`
  (the 22D vector + validate); (c) `extend_70d` — append the 22D to a 70D
  vector → 92D.
- **DEPENDENCIES:** numpy, `schema_contract` (base 70D geometry + hash for
  the 92D variant). Uses `_clip3` ([-3,+3]) and `_neutral_for` (per-index
  neutral constant) consistently with the family conventions.
- **CONNECTS TO:** dataset builders for the temporal candidate,
  shadow70 (temporal snapshots), tests (test_temporal_liquidity_phase20),
  `schema.FEATURE_SCHEMAS` (scalp_v4_temporal_candidate registration).
- **KEY CONCEPTS:**
  - Causality: `v_or_neutral` — a missing PREVIOUS value (short history)
    takes the per-index NEUTRAL, never zero and never a forward fill.
    `delta` compares current vs previous; `persistence` measures how long
    the value has remained near its current level (active_abs epsilon) —
    all strictly on past data.
  - The tracker is the live counterpart: `update(liquidity10, ts)` appends to
    a bounded history (`history_depth`), computes the snapshot, and can be
    reset — deterministic given the same sequence.
- **EDGE CASES & PITFALLS:** Research-only by governance (promotion is a
  separate decision, brief 45); short-history neutral substitution must stay
  IDENTICAL between training and live (a different neutral at inference would
  silently change model inputs — the parity invariant).