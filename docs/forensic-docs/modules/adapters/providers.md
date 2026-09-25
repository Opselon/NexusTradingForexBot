# src/nexus_scalp/adapters/mt5/providers.py

- **PURPOSE:** The Phase-14 typed snapshot layer — `SnapshotBase` family
  (AccountSnapshot, SymbolSnapshot, BrokerTickSnapshot, PositionSnapshot,
  OrderSnapshot, HistoryOrderSnapshot, DealSnapshot, RateBarSnapshot,
  TickHistorySnapshot, BrokerCalcSnapshot) plus UTC normalization helpers
  (broker_epoch_to_utc, normalize_utc) and attribute coercion helpers
  (_attr/_bool_attr/_int_attr/_float_attr).
- **ARCHITECTURE LAYER:** Adapters (data contracts for broker-facing
  reads; dependency of the ports layer itself).
- **RESPONSIBILITY:** (a) define what a broker snapshot IS (fields +
  provenance: available/source/captured_at/error); (b) convert raw
  MetaTrader5 objects (account_info tuples, symbol_info, ticks, deals,
  orders, rates) into typed, UTC-normalized snapshots with safe attribute
  reads; (c) `net_result` on DealSnapshot (profit - commission - swap —
  canonical realized money).
- **DEPENDENCIES:** stdlib dataclasses/datetime; nothing repo-internal
  beyond typing.
- **CONNECTS TO:** IMT5Port (imports these types), DirectMT5Adapter +
  PaperMT5Adapter (produce them), RiskEngine broker verification
  (order_calc snapshots), accounting reconstruction, UI/API consumers,
  tests (test_mt5_providers_phase14).
- **KEY CONCEPTS:**
  - `SnapshotBase.as_error(operation, code, message)` — uniform error
    snapshot shape; every provider method can return an error snapshot
    that still satisfies the type (no None-contract violations).
  - `normalize_utc` — the single coercion point: datetime → aware UTC,
    epoch → UTC, string ISO → UTC; None → None. THE fix for the epoch bug
    family.
  - Attribute coercion guards: raw MT5 objects expose tuples/structs
    whose fields may be missing → _attr returns None instead of raising.
  - Every snapshot carries `source` (BROKER_NATIVE / FALLBACK_ESTIMATE /
    UNAVAILABLE) + `captured_at` — consumers can always tell WHO produced
    the number.
- **EDGE CASES & PITFALLS:** `net_result` returns None when any leg is
  None (never a fabricated 0.0 — accounting invariant); source values are
  strings (no enum) — keep the taxonomy in docs+tests; adding a field to
  a snapshot is additive but MUST be mirrored in every producer
  (SHARED API CHANGED).