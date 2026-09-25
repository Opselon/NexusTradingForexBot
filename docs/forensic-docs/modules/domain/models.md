# src/nexus_scalp/domain/models.py

- **PURPOSE:** The frozen, validated data contracts that flow across every layer
  boundary — ticks from the broker, account/symbol metadata, trade proposals from
  the signal pipeline, orders to the broker, and open position state. These are
  the system's "wire format" in memory.
- **ARCHITECTURE LAYER:** Domain. Pure Pydantic models; zero infrastructure
  imports; deliberately isolated from MT5/frameworks (hexagonal inner ring).
- **RESPONSIBILITY:** Enforce structural + business invariants at the moment a
  value enters the system, so downstream layers can trust the shapes they receive.
  Immutability (`frozen=True`) guarantees no layer silently mutates shared state —
  updates must be explicit `model_copy(update=...)`, which the whole codebase
  relies on (e.g. SL/TP modifier flows).
- **DEPENDENCIES:** pydantic v2 (`ConfigDict`, `field_validator`,
  `model_validator`), `domain.enums`.
- **CONNECTS TO:** everything. `TickData` is minted by adapters per tick;
  `TradeProposal` is produced by SignalPolicy and consumed by RiskEngine then
  OrderLifecycleManager; `TradeOrder` is what the OrderManager sends through
  `IMT5Port.send_order`; `Position`/`AccountInfo` come back from broker state.
- **KEY CONCEPTS:**
  - `TickData` — market snapshot. Validators: (a) naive datetimes are
    auto-upgraded to UTC (never silently interpreted as local — epoch handling
    is a recurring bug class in this repo, see BUG-058/070); (b) bid > ask is a
    hard `ValueError` — a negative-spread tick is a broker anomaly, better to
    fail loudly than feed the feature pipeline garbage. `spread_points` rounds
    to 6dp — matching typical FX digits.
  - `SymbolInfo` — broker contract metadata (digits/point/tick_size/tick_value,
    volume min/max/step, stops_level/freeze_level, contract size). The RiskEngine
    and lot-sizer read these to clamp volumes and compute USD risk correctly;
    `stops_level`/`freeze_level` bound how close SL/TP may sit.
  - `AccountInfo` — balance/equity/margin snapshot; `is_real_account` is
    `trade_mode == 2` (MT5 enum: 0=Demo, 1=Contest, 2=Real). The engine gates
    LIVE order authority partly on this flag.
  - `TradeProposal` — the signal contract. The rich optional diagnostic fields
    (`buy_probability`, `regime`, `risk_allowed`, `guardian_status`,
    `rejection_reason`, `confidence_before/after_filters`, `decision_stage`,
    `blocked_by`, `execution_mode`) are NOT decorative: they are the forensic
    payload that `audit_signals` persists (BUG-054 lean 8-field contract) and
    what the Debug Hub shows. `is_ai_reversal`/`reversal_action` carry the
    AI-flip request from the exit engine. Price-geometry validator: for a
    BUY-family action SL must be strictly below entry and TP strictly above
    (mirror for SELL) — catches inverted SL/TP at creation, not at the broker.
  - `TradeOrder` — broker-bound request. `magic_number` tags every order so the
    engine can filter MT5's position/order lists to its own trades;
    `comment` is capped at 31 chars (broker field limit).
  - `Position` — broker truth for an open position; SL/TP are `ge=0.0` because
    0.0 encodes "no stop set" in MT5.
- **EDGE CASES & PITFALLS:**
  - Frozen models + Pydantic v2: mutation attempts raise
    `pydantic.ValidationError` — a documented repo pitfall (skill §16). A
    `model_copy(update={...})` is the sanctioned path.
  - `TradeProposal`'s price invariants are NOT enforced for
    `WAIT`/`NO_TRADE`/`CLOSE_POSITION` actions — those carry no directional
    geometry, and policy emits them without entry/SL/TP.
  - `TickData.last` defaults to 0.0 — consumers needing last price must check
    it (some feeds only provide bid/ask).
  - Model evolution hazard: adding a required field here breaks every adapter +
    test + UI contract simultaneously (SHARED API CHANGED discipline under the
    multi-agent contract).