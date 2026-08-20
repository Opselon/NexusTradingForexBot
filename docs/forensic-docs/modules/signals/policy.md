# src/nexus_scalp/signals/policy.py

- **PURPOSE:** The decision core of the live engine — `SignalPolicy.
  evaluate_probabilities()` converts the model's 4-class tensor + tick +
  features + regime into a `TradeProposal`, applying the multi-gate cascade
  (guardian → dedup → confidence → spread/range → direction hysteresis →
  rule matrix → SMC confluence). 1,844 lines because every rejection path is
  an explicit, auditable branch with its own reason_code.
- **ARCHITECTURE LAYER:** Signals (Application-adjacent, called on the tick
  hot path). Holds NO order authority — its output (TradeProposal) must pass
  RiskEngine + OrderManager before any broker contact.
- **RESPONSIBILITY:** (a) authoritative Regime Guardian Gate (unsafe regimes
  → NO_TRADE with BLOCKED_BY_GUARDIAN); (b) tick de-duplication
  (TICK_DUPLICATE_SUPPRESSED — hot path invoked faster than the feed);
  (c) multi-confluence scoring: confidence threshold (0.20 calibrated for
  HFT), spread ≤ 18% of ATR, min RR 1.10, range displacement ≥ 0.15,
  direction hysteresis (flip penalty 0.10 within 8s memory), SMC God Mode
  confluence (BOS/CHoCH, 50% equilibrium, liquidity sweep), predictive
  limit generation, pending-order lock (30s / ≥1.0×ATR drift);
  (d) AI-reversal evaluation (`_evaluate_ai_reversal` — the model's
  directional flip can close-and-reverse via OrderManager); (e) produce
  chart overlays (`extract_live_chart_overlays`) for the UI.
- **DEPENDENCIES:** torch (probabilities tensor), `domain.models`
  (TickData/TradeProposal), `features.scalp_features.FeatureVector`,
  `features.regime_classifier.MarketRegimeState` +
  RecommendedExecutionType, `signals.rule_matrix.RuleMatrixEngine`,
  `configuration.AlgoConfig`, uuid, logging.
- **CONNECTS TO:** LiveEngine `_process_tick_pipeline` (per-tick caller),
  RuleMatrixEngine (rule gates), OrderManager (dispatch of proposals),
  `audit_signals` (payload rows), the UI overlays, tests
  (test_policy, test_signal_pipeline_health, test_hardened_protocol).
- **KEY CONCEPTS:**
  - **Execution trace id (INV-018):** ONE `EXEC-<ts>-<rand>` per evaluation,
    stamped BEFORE any gate and carried into EVERY proposal (NO_TRADE
    included) — logs, audit rows, and dispatch are joinable by a single key.
    Observability only — never influences a decision.
  - **Gate cascade order matters:** guardian FIRST (cheapest, strongest),
    dedup second (state-free), then the numeric gates, then rule matrix,
    then SMC confluence, then proposal construction. Each gate fills
    `blocked_by`/`decision_stage`/`reason_code` so the audit row tells the
    full story (BUG-054 lean 8-field payload).
  - **Hysteresis:** flip penalty + flip memory (8s) damp direction
    flip-flopping: a BUY→SELL flip needs extra confidence. Same-level
    re-entry lockout uses `_last_executed_price` — no immediate re-entry at
    the same level after an exit.
  - **Cooldown:** 3s minimum between active signals (micro-scalp cadence —
    the anti-order-spam guard at the signal layer; frequency throttle exists
    too in the order manager).
  - Survives `survival_mode` (kill-switch state) by refusing entries.
  - `_sanitize_float(val, default)` normalizes any None/nan → default so a
    poisoned feature can't propagate a poisoned proposal.
- **HOT PATH / PERFORMANCE:** Runs per tick inside the 50ms budget. All
  state is in-memory (cooldown/dedup/direction memory); rule matrix reads
  are TTL-cached (5s) via the engine; no I/O on the path.
- **EDGE CASES & PITFALLS:**
  - Dedup returns NO_TRADE WITHOUT touching state (documented in code) —
  dedup'd ticks must NOT reset cooldown/direction memory (else a fast feed
  would never arm).
  - `UNSAFE_REGIMES` set uses string values matching `RegimeType` — keep in
  sync with the enum (a renamed regime silently stops being blocked).
  - The policy receives completed bars for overlay/SMC zones — those list
  slices must use the SAME causal tail discipline as features (no future
  bars).