# src/nexus_scalp/signals/_rule_evals_smc.py

- **PURPOSE:** The SMC-family rule evaluators — Smart Money Concept rules
  (FvgSniperFill, JudasSwingFade, LiquiditySweepConfirm, OrderBlockTapReserve,
  WickAbsorptionPlay) executed on candle/context data.
- **ARCHITECTURE LAYER:** Signals (rule implementations).
- **RESPONSIBILITY:** Encode the institutional playbook as rules:
  - `FvgSniperFillEvaluator` — a fair value gap that filled (price returned
    into the gap) → sniper-entry setup; `_timeframe_ok` gates the rule to
    its configured timeframe.
  - `JudasSwingFadeEvaluator` — a false break (Judas swing) that reclaims
    → fade the break.
  - `LiquiditySweepConfirmEvaluator` — sweep of a pool + confirmation →
    allow (confluence with the feature-layer sweep signal).
  - `OrderBlockTapReserveEvaluator` — price tapping an order block without
    breaking → reserve/limit-entry signal.
  - `WickAbsorptionPlayEvaluator` — wick absorption (rejection wicks on a
    level) → reversal play.
- **DEPENDENCIES:** ctx types, result types, `_timeframe_ok` helper, logging.
- **CONNECTS TO:** `_rule_engine` registry; supplements the SMC features
  already in the 50D vector (feat_ob_* family) by turning zone states into
  tradable rule verdicts.
- **KEY CONCEPTS:** These rules reason about ORDER BLOCKS / FVGs / SWEEPS —
  the same concepts the feature engine encodes numerically, but here as
  discrete events with veto/trigger semantics. Pure context functions.
- **EDGE CASES & PITFALLS:** Zone detection must be causally confirmed
  (taps on UNCONFIRMED zones must not trigger the fill rule) — same ±5
  confirmation discipline as the liquidity engine.