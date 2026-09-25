# src/nexus_scalp/features/regime_classifier.py

- **PURPOSE:** Classifies the LIVE market microstructure regime on every tick —
  the system's situational awareness layer. The 10-regime taxonomy feeds the
  Regime Guardian Gate (unsafe regimes → NO_TRADE) and conditions exit/logic
  decisions downstream.
- **ARCHITECTURE LAYER:** Features. Pure math + state; invoked on the tick
  hot path; holds NO broker/order authority (research-safety contract).
- **RESPONSIBILITY:** Convert tick stream (price + spread + volume/OFI) into a
  stable regime label with hysteresis — so the engine doesn't flip-flop regime
  every tick — plus the recommended execution type per regime.
- **DEPENDENCIES:** numpy, `domain.models.TickData`, `observability.logging`;
  internal ring buffers for tick series.
- **CONNECTS TO:** LiveEngine (per-tick classify), SignalPolicy
  (RegimeType conditions the decision), OrderManager exit paths
  (regime-aware giveback/volatility expansion logic), `audit_signals`
  (regime columns), Debug Hub state.
- **KEY CONCEPTS:**
  - `RegimeType` (10): TRENDING_UP/DOWN, RANGING, HIGH_SPREAD_CHOP,
    MARKET_HALTED, NEWS_LOCK, VOLATILITY_EXPANSION, ORDER_FLOW_IMBALANCE,
    UNKNOWN (cold), plus the frenzy/quiet states — each maps to a
    `RecommendedExecutionType` (e.g. HIGH_SPREAD_CHOP → STANDARD_OFF /
    AVOID_ENTRY). The guardian gate blocks unsafe regimes at the policy layer
    (ActionType.NO_TRADE with BLOCKED_BY_GUARDIAN).
  - `MarketRegimeState` (Pydantic, frozen) — the per-tick regime verdict:
    regime, confidence, reason, spread, volatility, and the recommendation.
  - `classify_tick` algorithm: (1) compute log-return + OFI
    (`_compute_ofi`, order-flow imbalance from tick volume/direction);
    (2) `_push` into bounded time buckets; (3) `_evict` stale buckets (fixed
    TTL window — memory bounded, no unbounded growth on a 24/7 stream);
    (4) `_candidate_regime` — rule cascade (halted? news-locked? spread
    ratio? volatility? trend slope? OFI?) with each rule carrying a reason;
    (5) `_apply_hysteresis` — require N consecutive confirmations (or a
    confidence delta) before switching regime, preventing label flicker;
    (6) `_exec_for` maps the stabilized regime to execution advice.
  - `_state` builds the frozen MarketRegimeState with confidence — the
    confidence feeds `TradeProposal.regime_confidence` and the audit payload.
- **HOT PATH / PERFORMANCE:** Runs every tick; the state is O(1) amortized
  (bounded buckets + TTL eviction). No I/O, no allocation beyond the state
  object — designed to sit inside the 50ms tick budget.
- **EDGE CASES & PITFALLS:**
  - Clock discipline: `classify_tick` takes `now_sec` from the tick
    timestamp — never the host wall clock (the repo-wide lesson from
    BUG-058/070 era: broker time vs host time can diverge by hours).
  - Cold state (no buckets yet) must classify UNKNOWN with low confidence —
    the policy must NOT treat UNKNOWN as tradable; the guardian gate handles
    that.
  - Hysteresis is a double-edged sword: it stabilizes labels but delays true
    regime changes — the delay is bounded by the confirmation window and is
    a documented design decision (stability > reactivity on the exit paths).