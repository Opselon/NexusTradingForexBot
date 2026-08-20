# src/nexus_scalp/model_generation/setup_detector.py

- **PURPOSE:** `SetupDetector` — detects market setups (the chart-state
  fingerprints the Hunter strategies trade: breakouts, liquidity sweeps,
  FVG fills, CHoCH, compression, etc.) from bars, producing
  `SetupDetection` records with quality scores (`_quality` weighted
  terms) and deterministic ids (`_make_id`). The bridge between raw bars
  and strategy-conditioned training samples.
- **ARCHITECTURE LAYER:** ML research (setup/sample preprocessing).
- **RESPONSIBILITY:** (a) scan completed bars for each setup type;
  (b) score setup quality (multi-factor weights, `_f` sanitizer);
  (c) `validate_setup_type` — the known-type registry check.
- **DEPENDENCIES:** numpy, domain models, logging.
- **CONNECTS TO:** sample_maker (HunterSampleMaker consumes detections),
  strategy_factory (best_strategy_for), dataset builders, tests.
- **KEY CONCEPTS:** Causality: detection uses ONLY completed bars up to
  the decision bar (no future confirmation); determinism: same bars →
  same detections (fractal/confirmation windows fixed); ids are
  content-derived (setup type + row + ts).
- **EDGE CASES & PITFALLS:** An unknown setup type must be rejected
  (validate_setup_type) before it pollutes the sample stream; near-tail
  detections must not produce out-of-range label windows.