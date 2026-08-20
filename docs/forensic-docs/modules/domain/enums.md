# src/nexus_scalp/domain/enums.py

- **PURPOSE:** Canonical vocabulary of every state the trading engine can express:
  execution modes, proposed actions, order types, order lifecycle statuses, and
  system health. Everything downstream (signals, risk, execution, UI, ledger)
  serializes these values; nothing may invent a new string literal where an enum
  exists.
- **ARCHITECTURE LAYER:** Domain. Pure vocabulary — no logic, no imports beyond
  stdlib `enum`.
- **RESPONSIBILITY:** Single source of truth for strongly-typed state strings.
  Using `StrEnum` (Python 3.11+) makes each member a `str` subclass, so values
  flow into JSON/DB rows/Telegram text without custom serializers, while still
  being type-checkable and typo-proof at the call sites.
- **DEPENDENCIES:** none (stdlib only).
- **CONNECTS TO:** every layer. `ActionType` is the currency of the whole
  signal→risk→execution chain; `ExecutionMode` gates the LiveEngine's order
  authority; `SystemHealth` drives the circuit-breaker states surfaced by
  `/api/status`.
- **KEY CONCEPTS:**
  - `ExecutionMode` — 5 modes form a monotone authority ladder:
    BACKTEST/REPLAY (no live I/O) → PAPER (live data, simulated fills) →
    SHADOW (live data + live inference, ZERO order authority) → LIVE (real
    orders). The engine derives its "may I send orders" decision from this
    value; shadow/live distinction is the core safety boundary of the system.
  - `ActionType` — the decision language of the neural head + policy.
    `BUY`/`SELL` are directional intent; the `_MARKET`/`_LIMIT`/`_STOP`
    variants are execution-venue qualifiers; `CLOSE_POSITION`,
    `PARTIAL_CLOSE`, `MODIFY_SL_TP`, `CANCEL_ORDER` are lifecycle commands
    handled by the OrderManager (not the model head — the 4-class model head
    only ever emits NO_TRADE/BUY_MARKET/SELL_MARKET/WAIT).
  - `OrderStatus` — internal state-machine vocabulary for tracking an order's
    progress toward the broker (PENDING→FILLED/REJECTED/CANCELLED/EXPIRED).
  - `SystemHealth` — health ladder used by the circuit breaker: after 3
    consecutive broker rejections the engine drops to SAFE_MODE/DEGRADED and
    halts dispatch.
- **EDGE CASES & PITFALLS:**
  - Because members are `str`, an accidental comparison `action == "buy"`
    (wrong case) silently yields False instead of raising — callers must use
    the enum members. This is a deliberate trade-off of StrEnum for serialization
    simplicity.
  - `ActionType.WAIT` is distinct from `NO_TRADE`: WAIT means "hold judgment,
    re-evaluate next tick" whereas NO_TRADE is a committed rejection (audited
    differently in `audit_signals` reason codes).