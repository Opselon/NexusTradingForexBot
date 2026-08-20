# src/nexus_scalp/candle_intelligence/config.py

- PURPOSE: Tunable thresholds for the candle-close gate, pattern scoring
  and decision hierarchy (BUG-061). "All defaults are conservative: when
  in doubt -> no trade" (docstring).
- ARCHITECTURE LAYER: Configuration (pydantic settings; deliberately
  separated from AlgoConfig so the module stays isolated and its safety
  knobs cannot be silently changed by unrelated hot-reload paths).
- RESPONSIBILITY: the module's full safety-knob surface — close
  geometry thresholds, decision-gating thresholds, behavior flags, DB
  settings.
- DEPENDENCIES: pydantic (BaseModel, Field) only.
- CONNECTS TO: CandleCloseClassifier (geometry thresholds),
  PatternEngine (pattern_min_confidence), CandleDecisionEngine (entry/
  hold/exit thresholds, breakout penalties, blocked-set behavior),
  CandleIntelStore (db_path, max_batch_size).
- KEY CONCEPTS:
  - Close-geometry thresholds: min_candle_range=1e-9 (range below this
    -> INVALID), strong_body_ratio=0.60, weak_body_ratio=0.20,
    long_wick_ratio=0.45, rejection_reversal_threshold=0.55,
    continuation_threshold=0.55, indecision_threshold=0.55,
    exhaustion_threshold=0.60.
  - Decision-gating thresholds: entry_min_confidence=0.62,
    hold_min_confidence=0.35, fast_exit_confidence=0.60,
    pattern_min_confidence=0.45, multi_factor_min_confirmations=2 —
    the multi-factor rule: one pattern alone is never enough.
  - Behavior: weak_close_blocks_entry=True (the gate itself),
    false_breakout_reduces_confidence_by=0.35,
    trapped_breakout_reduces_confidence_by=0.25,
    fallback_conservative_no_trade=True (when in doubt -> no trade).
  - DB: db_path="artifacts/candle_intel.db" (isolated),
    max_batch_size=500 (batched write flush cap).
- HOT PATH / PERFORMANCE: plain attribute reads; instantiated once per
  engine/store/classifier; no runtime cost.
- EDGE CASES & PITFALLS: every threshold is pydantic-validated (ge/le),
  so a bad config cannot widen the gate beyond its conservative intent;
  min_candle_range=1e-9 is effectively a zero-tolerance malformed-input
  guard rather than a real minimum range requirement; the docstring's
  "plain floats with strict ranges" matches the pydantic validation.
- NOTE: config is the single source of truth for the package's safety
  posture — changing entry_min_confidence here changes the gate
  behavior for every consumer.