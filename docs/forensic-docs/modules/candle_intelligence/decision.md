# src/nexus_scalp/candle_intelligence/decision.py

- PURPOSE: Candle Decision Engine (BUG-061) — turns one candle-close
  summary + pattern detections + regime + risk inputs into a single
  immutable decision. "The candle close is a GATE: a weak, contradictory
  or invalid close downgrades confidence, blocks entry, or accelerates
  exit — before any pattern or risk logic runs."
- ARCHITECTURE LAYER: Decision logic (advisory — never touches an adapter
  or order manager).
- RESPONSIBILITY: the 6-level rule hierarchy (spec §10), deterministic
  decisions with stored reason codes.
- DEPENDENCIES: config, models (all decision contracts).
- CONNECTS TO: engine (decision_engine.decide), store (record_decision,
  record_veto).
- KEY CONCEPTS:
  - Rule hierarchy (docstring + code): 1) hard safety veto (INVALID data /
    risk blocked); 2) regime filter; 3) candle-close validation (the
    close-quality gate); 4) pattern confirmation (multi-factor, never
    single-pattern); 5) risk sizing; 6) execution decision.
  - BLOCKED_REGIMES (line 40): UNKNOWN, ERRATIC, CRASH, GAP_FILL_HUNT,
    NEWS_SPIKE — conservative hard entry blocks.
  - `decide` structure:
    - Level 1: INVALID close -> NO_TRADE with hold_allowed=False,
      exit_required=holding_position (VETO:INVALID_CANDLE); risk blocked
      (not risk_allowed or BLOCKED) -> NO_TRADE/EXIT for holders
      (VETO:RISK_BLOCKED) — position-protection is never held hostage by
      the gate.
    - Level 2: regime in BLOCKED_REGIMES -> NO_TRADE/EXIT
      (VETO:REGIME:<name>).
    - Level 3: bias + base confidence from _bias_from_close (continuation/
      reversal -> close_strength*0.8+0.2; EXHAUSTION -> rejection*0.6;
      TRAPPED_BREAKOUT -> rejection*0.5; FALSE_BREAKOUT -> 0.3; WEAK_CLOSE
      -> close_strength*0.4; else indecision*0.3). Close classes that
      BLOCK entry: INDECISION, WEAK_CLOSE, TRAPPED_BREAKOUT,
      FALSE_BREAKOUT, EXHAUSTION. FALSE_BREAKOUT/TRAPPED_BREAKOUT
      immediately reduce confidence by 0.35/0.25.
    - Level 4: aligned patterns (direction == bias) can lift a decent
      close: best >= pattern_min_confidence -> confidence =
      max(conf, (close_conf + best)/2); multi-factor gate:
      len(aligned) >= multi_factor_min_confirmations (2) else
      PATTERN:INSUFFICIENT_CONFIRMATION.
    - Level 5: risk caps — CAUTION -> min(conf, 0.6), REDUCED ->
      min(conf, 0.4) (RISK:CAUTION_CAP/RISK:REDUCED_CAP).
    - Level 6: holding_position -> _manage_position (TRAPPED_/FALSE_
      BREAKOUT -> FAST_EXIT + exit_required; EXHAUSTION -> MODIFY_SL_TP;
      INDECISION below hold_min_confidence -> FAST_EXIT; losing position
      + WEAK_CLOSE below fast_exit_confidence -> FAST_EXIT; below
      hold_min_confidence -> NO_TRADE (flat hold); else HOLD). No
      position: close_blocks + weak_close_blocks_entry -> NO_TRADE
      (VETO:WEAK_CLOSE:<cc>); confidence >= entry_min_confidence (0.62)
      + bias != NEUTRAL -> ENTRY; else NO_TRADE (VETO:LOW_CONFIDENCE).
  - `_build` (line 314): constructs the frozen CandleDecision incl.
    computed_payload (close scores + pattern name/score list) — the
    explainable audit record.
- HOT PATH / PERFORMANCE: pure logic; called once per completed bar.
- EDGE CASES & PITFALLS: line 223 `if no_trade_reason and not
  no_trade_reason:` is dead code (condition can never be true) — harmless;
  FALSE_BREAKOUT is handled but never produced by the classifier;
  EXHAUSTION close yields NEUTRAL bias so it cannot enter, only modify/
  hold; a user-supplied risk=RiskEvaluation() default has risk_allowed=True
  + SAFE — an absent risk input never blocks (caller must pass a real
  evaluation when risk is a real constraint).