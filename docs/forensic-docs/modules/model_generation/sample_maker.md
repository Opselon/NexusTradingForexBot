# src/nexus_scalp/model_generation/sample_maker.py

- **PURPOSE:** `HunterSampleMaker` — builds high-quality labeled samples
  from bars + setups (the "Hunter" strategy-family sample maker):
  converts raw bar history + detected setups into training samples with
  quality tiering (`quality_tier`) and hunter metadata
  (`attach_hunter_metadata`).
- **ARCHITECTURE LAYER:** ML research (sample construction).
- **RESPONSIBILITY:** (a) for each detected setup (entry bar, type,
  direction), extract the feature window + label window (triple-barrier
  aware) deterministically; (b) quality scoring: setup quality, liquidity
  proximity, trend alignment → tier (A/B/C) so training can weight by
  tier; (c) attach provenance metadata (setup id, type, quality) to every
  sample.
- **DEPENDENCIES:** numpy/polars, domain models, labeling helpers.
- **CONNECTS TO:** sample_factory (the factory pipeline), strategy_factory
  (HunterStrategy selects setups), dataset builders, tests.
- **KEY CONCEPTS:** Sample quality is multi-factor and explicit (tiers)
  — the trainer can gate on tier via config; determinism (same history +
  same setups → same samples) is the parity invariant.
- **EDGE CASES & PITFALLS:** A setup near the dataset tail must not
  produce a sample whose label horizon overruns (tail-bounded, like the
  labeler); missing columns coerce to defaults with logging (never
  silent).