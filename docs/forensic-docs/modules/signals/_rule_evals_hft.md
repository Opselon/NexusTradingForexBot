# src/nexus_scalp/signals/_rule_evals_hft.py

- **PURPOSE:** The HFT-family rule evaluators — microstructure-speed rules
  evaluated on the tick path. Each class implements `evaluate(ctx) ->
  RuleEvaluationResult`.
- **ARCHITECTURE LAYER:** Signals (rule implementations).
- **RESPONSIBILITY:** The five HFT rules:
  - `FlashMomentumScrapeEvaluator` — fast momentum burst detection (rapid
    consecutive directional ticks) → allow/scalp-strength or veto.
  - `TickImbalanceReversalEvaluator` — order-flow imbalance (aggressive
    buys vs sells) exceeding a threshold → reversal guard.
  - `SpreadSqueezeOnlyEvaluator` — only trade when the spread is squeezed
    (tight); veto entries into wide-spread liquidity gaps.
  - `RejectionWallBlockerEvaluator` — a wall of rejections (repeated
    round-trips on a level) → block further entries at that level.
  - `BidAskSpoofDetectorEvaluator` — spoofing pattern (large resting size
    that vanishes) → block fading the spoof.
- **DEPENDENCIES:** ctx types (`RuleEvaluationContext`), result types,
  logging.
- **CONNECTS TO:** `_rule_engine` registry (`_register_all` wires these in
  under their rule ids), policy stage gates.
- **KEY CONCEPTS:** Pure functions of the evaluation context — no hidden
  state, so training/replay/live parity holds; thresholds come from rule
  params (DB-configurable).
- **EDGE CASES & PITFALLS:** These rules read tick-level microstructure
  fields; contexts missing those fields must yield PASS (not crash) so the
  rule degrades gracefully on thin data.