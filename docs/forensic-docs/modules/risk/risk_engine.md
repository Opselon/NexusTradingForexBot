# src/nexus_scalp/risk/risk_engine.py

- **PURPOSE:** The capital-protection authority — dynamic lot sizing
  (fixed-dollar risk scaled by regime/drawdown/confidence), portfolio
  context gates (concurrent positions, pending orders, directional
  exposure), broker stops-level validation, spread gate, risk-reward
  gatekeeper, Almgren-Chriss market-impact/slippage guard, margin clamping,
  kill switch, and Phase-14 broker-native margin/profit verification with
  explicit provenance.
- **ARCHITECTURE LAYER:** Risk. Called on the tick path (proposal → sized
  TradeOrder); holds NO order authority itself — its output is what
  OrderManager dispatches.
- **RESPONSIBILITY:** Convert a `TradeProposal` into either a broker-safe
  `TradeOrder` (sized, margin-safe, stops-legal, impact-bounded) or None
  (rejected, with a logged reason). The engine FAILS CLOSED: any invalid
  input → 0.0 volume / None.
- **DEPENDENCIES:** `configuration.config.RiskConfig` (risk_per_trade_pct,
  max_concurrent_positions, max_spread_points, max_pending_orders), domain
  models (AccountInfo/SymbolInfo/TradeProposal/TickData/Position/TradeOrder),
  regime classifier (volatility scaling), observability.logging.
- **CONNECTS TO:** SignalPolicy output → `evaluate_proposal` →
  OrderManager.dispatch_order; LiveEngine wiring; broker-calc snapshot
  adapters (Phase 14); tests (test_risk_engine).
- **KEY CONCEPTS:**
  - **`calculate_dynamic_volume` 8-step pipeline:** (1) validate inputs
    (NaN/Inf/None → INVALID_INPUT_NAN_INF_NONE; pricing/equity/margin/
    leverage/contract/step guards each with a reason code); (2) risk USD =
    equity × risk%; (3) raw lots = riskUSD / (SL distance × contract size)
    — the fixed-dollar-risk formula: wider SL → smaller lot;
    (4) floor to broker volume_step; (5) cap at broker volume_max;
    (6) account tier ceiling (equity<100 → 0.02, <1k → 0.10, <10k → 1.00,
    else min(10.0, vol_max)) — note: this differs from the skill's older
    "0.50/2.00" table; the CODE is truth; (7) 20% free-margin clamp:
    max_margin_volume = (free_margin×0.20×leverage)/(contract×price);
    re-floor to step; (8) broker minimum: below volume_min → micro-account
    exception (equity<50 → force volume_min) else 0.0. Returns
    (volume, reason).
  - **`evaluate_proposal` gate cascade:** kill switch → action filter
    (NO_TRADE/WAIT → None) → max concurrent positions → max pending orders
    → opposite-exposure limit-order guard (unless HEDGE reason code) →
    directional exposure squeeze (max_allowed_lots) → spread gate
    (points > max_spread_points) → RR gatekeeper: min RR 1.8 default,
    relaxed to 1.2 when confidence ≥ 0.95 (note: ctor default
    high_confidence_threshold is 0.70 but the evaluate path reads the
    attribute with a 0.95 fallback — the effective branch uses 0.95) →
    triple stops-level validation (pending entry vs market distance; SL/TP
    vs stops_level) → regime VOLATILITY_EXPANSION halves risk% →
    drawdown penalty (peak_equity drop >1% scales risk by
    max(0.2, 1-0.2×dd%)) → confidence scalar (0.5..1.2 × risk%) →
    calculate_dynamic_volume → exposure-cap clamp → final min-lot recheck →
    margin verification (required_margin > margin_free → 0.0) →
    Almgren-Chriss impact loop (reward vs slippage; step down volume until
    slippage ≤ 45% of reward; below min → micro-account exception or
    EXCESSIVE_MARKET_IMPACT_REJECTED).
  - **Almgren-Chriss impact** `_estimate_market_impact`: eta × (vol/
    contract_size) × max(atr, 0.50); LIMIT orders are liquidity-MAKERS →
    zero taker slippage (the HFT microstructure rule).
  - **Kill switch:** enable/disable — a hard stop usable by operators and
    the survival machinery.
  - **Phase 14 broker verification:** `verify_margin_with_broker`/
    `verify_profit_with_broker` use the adapter's snapshot API when
    available (BROKER_NATIVE), else the mathematical estimate with explicit
    FALLBACK_ESTIMATE provenance, else UNAVAILABLE — OPTIONAL, never on the
    safety-critical path, failure-isolated (exception → fallback kept).
  - Magic number 888101 + comment "NSE_HFT_SIZED" tag every risk-approved
    order for broker-side filtering.
- **HOT PATH / PERFORMANCE:** Runs per proposal (not per tick), O(1)
  except the impact-reduction while-loop (bounded by step downs, cheap).
  All pure math — no I/O; broker-calc snapshots are opt-in calls outside
  the critical decision.
- **EDGE CASES & PITFALLS:**
  - Volume-step flooring twice (steps 4 and 7) — the second floor can
    REDUCE an already-floored volume; rounding to 4dp avoids float dust.
  - `_floor_to_step` returns 0.0 for invalid step/val — feeding back into
    the pipeline yields a 0.0 volume (fail-closed), never a crash.
  - Peak-equity drawdown penalty reads `account.peak_equity` via getattr —
  absent → equity (no penalty); only >1% drawdown triggers (deliberate
  deadband so a 0.5% floating dip doesn't shrink risk).
  - Note the skill's older tier table (0.50/2.00) vs code (0.10/1.00) —
  the code wins (documented inconsistency, see issues ledger).