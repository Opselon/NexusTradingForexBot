# MODEL GOVERNANCE GOLDEN FIXTURES (TASK-6 / TESTS/golden/)

> Maintained per the MASTER MULTI-AGENT CONTRACT §23.
> These are canonical, stable inference samples for the Champion (50D) and
> the 60D Challenger alignment contract. They protect against silent
> preprocessing drift, model corruption, and runtime/package drift
> (TEST-LG-16 / TEST-LG-25 / TEST-LG-26).

## Champion 50D — deterministic input

- schema_id: `scalp_v1`
- dimension: 50
- generator: `seeded_vector(seed=7)` — deterministic, finite, clipped [-3,3]
- expectations are recorded as invariant properties of the pipeline, not as
  brittle float literals of a random artifact (the live Champion artifact is
  versioned by its hash, which the governance load gate verifies at runtime).

## 60D Challenger alignment (50D -> 60D / 72D)

- base: the 50D live vector (identical object, byte-for-byte)
- extras: `features/schema_augment.compute_60d_extras` — 10 REAL features
  from the same causal bar window (TASK-5 contract). Zero-fill is REJECTED.
- news (72D only): the 12-field canonical `NewsContextSchema` order.
- alignment labels: `IDENTICAL` (same schema) / `NEWS_EXTENDED` (50+10+12)

## Golden samples (stable synthetic vector)

`golden_50d.json`: a 50-float vector with canonical NaN/Inf sanitization and
[-3.0, +3.0] clipping applied, plus its SHA-256 content hash.

`golden_60d_extras.json`: 10 extras from a fixed 60-bar candle window
(opens/highs/lows/closes/volumes) used by replay tests.

Usage: tests reference these files; a change here requires an explicit
reason + contract/version update + handoff note (contract §23).