# src/nexus_scalp/candle_intelligence/models.py

- PURPOSE: Immutable domain contracts for candle intelligence (BUG-061) —
  close classification, pattern detections, regime, risk, final decision.
  "The candle close is a GATE, not a feature: every decision record carries
  the full close classification and the reason codes that led to it, so
  any decision is explainable and deterministic for identical input state"
  (docstring).
- ARCHITECTURE LAYER: Domain (frozen pydantic contracts; never mutate,
  use model_copy).
- RESPONSIBILITY: the type surface consumed by classifier, patterns,
  decision, engine, store.
- DEPENDENCIES: pydantic only.
- CONNECTS TO: every candle_intelligence module; store serializes via
  model_dump / model_dump_for_db.
- KEY CONCEPTS:
  - `CandleCloseClass` (line 23): 10-valued primary classification —
    BULLISH/BEARISH_CONTINUATION, BULLISH/BEARISH_REVERSAL, INDECISION,
    TRAPPED_BREAKOUT, EXHAUSTION, FALSE_BREAKOUT, WEAK_CLOSE, INVALID.
  - `TradeBias`: BULLISH/BEARISH/NEUTRAL/NO_TRADE. `DecisionType`:
    ENTRY/HOLD/FAST_EXIT/EXIT/NO_TRADE/MODIFY_SL_TP/CANCEL_PENDING.
    `RiskState`: SAFE/CAUTION/RISK_ON/REDUCED/BLOCKED.
  - `CandleCloseSummary` (line 63): raw geometry (open/high/low/close/
    range/body/wicks) + bounded ratios (body_ratio, wick ratios,
    close_position_in_range 0=low..1=high) + derived scores (close_
    strength, rejection, continuation, reversal, indecision, momentum_
    decay — all [0,1]) + close_class + close_quality (STRONG/GOOD/
    NEUTRAL/WEAK/INVALID). `model_dump_for_db` flattens with close_class
    as its .value.
  - `PatternDetection` (line 106): pattern_name, direction, raw_score
    (shape fidelity), context_weight (trend/vol/structure multiplier),
    confidence_score = raw * context, requires_confirmation (True for all
    directional), reason_codes.
  - `RegimeState` (line 120): regime label, volatility_state
    (NORMAL/HIGH/LOW), atr, spread.
  - `RiskEvaluation` (line 134): risk_state, risk_allowed, reason_codes —
    never sizes orders here.
  - `CandleDecision` (line 144): full immutable decision record: close
    summary + patterns + regime + risk + trade_bias + confidence_score +
    per-action flags (entry_allowed, hold_allowed, fast_exit_required,
    exit_required, modify_order, cancel_pending) + decision_type +
    no_trade_reason + reason_codes + raw_payload + computed_payload (the
    audit trail for the decision).
- HOT PATH / PERFORMANCE: frozen models built once per new-bar close;
    cheap attribute reads in the tick gate.
- EDGE CASES & PITFALLS: computed_payload is a plain dict (not frozen);
  close_quality and close_class are duplicated-ish views over the same
  data (must stay consistent); `CandleCloseClass.FALSE_BREAKOUT` is
  declared in the enum but the classifier never produces it in the
  current classification ladder (decision engine still handles it — a
  defensive branch).