# src/nexus_scalp/news/gate.py

- PURPOSE: The bounded decision gate — the ONLY place news influences
  trading decisions, and it does so strictly inside caps. HARD RULES
  (docstring, enforced by tests): news can NEVER force BUY/SELL, NEVER
  bypass RiskEngine/exposure/kill switch/position protection.
- ARCHITECTURE LAYER: Application (decision-adjacent modifier).
- RESPONSIBILITY: evaluate one proposal against CurrentNewsContext; produce
  an explainable NewsGateVerdict with a bounded confidence adjustment.
- DEPENDENCIES: NewsConfig (bounds), models (CurrentNewsContext,
  NewsDirection, NewsState). Holds NO adapter, NO order manager, NO risk
  engine (docstring line 19).
- CONNECTS TO: live engine proposal pipeline and (via verdict.to_dict)
  logging/UI explainability.
- KEY CONCEPTS:
  - NewsGateDecision: CONFIRM / CONFLICT / IGNORE / CAUTION.
  - NewsGateVerdict (dataclass): decision, signed bounded
    confidence_adjustment, news/strategy direction, aligned/conflicted/
    cautioned/blocked flags, reason code, context_state, relevance,
    confidence, notes; `to_dict` for serialization.
  - `evaluate` rules, in order:
    1. context unavailable/stale -> IGNORE (NEWS_UNAVAILABLE_OR_STALE) —
       failure of the news subsystem is a no-op, never an influence.
    2. Non-entry actions (NO_TRADE, CLOSE_POSITION, PARTIAL_CLOSE,
       MODIFY_SL_TP, CANCEL_ORDER) -> IGNORE: position-protection is NEVER
       gated by news (line 116-128).
    3. Non-entry set (BUY/SELL variants) checked; anything else -> IGNORE.
    4. relevance = max(xauusd_relevance, usd_relevance); < 0.25 -> IGNORE
       (LOW_NEWS_RELEVANCE).
    5. blocked_states (BREAKING/HIGH_IMPACT) with context.confidence >=
       0.35 -> CAUTION, `blocked = proposal_confidence < 0.6` — only weak
       setups are blocked, stronger setups downgraded to caution.
    6. caution_states (CONFLICTED/ELEVATED) -> CAUTION with
       adjustment = -min(max_confidence_penalty*0.5, proposal_conf*0.5).
    7. Alignment: aligned AND regime_aligned -> CONFIRM, boost =
       max_confidence_boost * context.confidence * relevance, capped at
       max_confidence_boost (default 0.05 — a +5% boost).
    8. Conflicted -> CONFLICT, penalty = max_confidence_penalty *
       context.confidence capped at 0.10 (a −10% penalty). Never a
       direction flip.
    9. Else -> IGNORE (NEWS_NEUTRAL_OR_WEAK_ALIGNMENT).
  - `_net_direction` (line 215): bullish if (bullish-bearish) > 0.08,
    bearish < -0.08, CONFLICTED if conflict_score > confidence, else
    NEUTRAL. The 0.08 deadband prevents noise flipping.
  - `_aligned`/`_conflicted` treat MIXED/CONFLICTED/NEUTRAL as never
    aligned; strategy NEUTRAL/CONFLICTED is never conflicted.
- HOT PATH / PERFORMANCE: pure float/string logic per proposal — trivial.
- EDGE CASES & PITFALLS: blocked requires confidence >= 0.35 (a low-
  confidence context in a blocked state does NOT caution); the boost is
  scaled by (context.confidence * relevance) then min-capped to the bound
  so real boosts are typically far below 5%; the gate never classifies the
  regime itself — regime_aligned must be supplied by the caller; verdict
  defaults (decision=IGNORE) make a partially-built verdict a safe no-op.