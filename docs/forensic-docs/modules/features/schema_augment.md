# src/nexus_scalp/features/schema_augment.py

- **PURPOSE:** The TASK-5 causal 60D augmentation layer — 10 additional
  features (feat_50..feat_59) computed from COMPLETED bars + the decision
  tick, strictly causal so live ≡ replay ≡ training. Produces the `scalp_v2`
  60D candidate contract.
- **ARCHITECTURE LAYER:** Features (research/candidate path — never the
  ACTIVE live contract; live stays scalp_v1/50D).
- **RESPONSIBILITY:** (a) `compute_60d_extras()` — the causal 10D
  augmentation (regime_compression, momentum_5_atr, wick_imbalance_5,
  volume_z_5, range_z_5, clv_avg_5, session_phase_enc, price_acceleration,
  atr_trend_ratio, direction_bias_8 — all windowed over bars ENDING at the
  decision tick); (b) `augment_50d_to_60d()` — assemble the 60D vector;
  (c) `validate_60d_vector()` — strict dim/finite/[-3,+3] validation;
  (d) `feature_quality_report()` — per-feature finite/zero/range stats for
  dataset QA.
- **DEPENDENCIES:** numpy (vector math), `observability.logging`. Uses the
  same `_atr`/`_safe_div` conventions as the rest of the features layer.
- **CONNECTS TO:** model_generation dataset builders (schema_v2), shadow/70D
  benchmark matrix (60D cell), research/validation suites
  (test_schema_70d_reconciliation, test_70d_* parity suites), the frozen
  `scalp_liquidity_v1` sibling line (which owns indices 50..59 under its own
  schema id).
- **KEY CONCEPTS:**
  - Causality discipline is the whole point: every window slice ends at or
    BEFORE the decision tick — `compute_60d_extras` never peeks past the bar
    the model is deciding on (the walk-forward/embargo machinery downstream
    additionally guards training labels).
  - `session_phase_encoding(hour_utc)` — sinusoidal-ish encoding of session
    phase rather than hard 0/1 session flags (continuous, unlike the base
    50D's binary session bits).
  - `_safe_div(num, den, default)` — every division uses a guarded divisor
    with explicit default; `_range` adds epsilon floors. Zero-division can
    never propagate NaN into the vector.
  - `validate_60d_vector` mirrors the 50D validator: fail-loud on arity,
    NaN/Inf, or out-of-bounds — the "never silently repair" invariant.
- **EDGE CASES & PITFALLS:** The 60D contract is CANDIDATE-ONLY and FROZEN
  per the 70D series (scalp_v2 superseded by scalp_v3 as the canonical
  research schema); do not extend it — new work goes through schema_contract.