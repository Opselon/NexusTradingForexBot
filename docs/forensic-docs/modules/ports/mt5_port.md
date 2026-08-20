# src/nexus_scalp/ports/mt5_port.py

- **PURPOSE:** The dependency-inversion boundary between the engine core and the
  MetaTrader 5 world. Everything the application needs from a broker is declared
  here as an abstract contract; concrete adapters (DirectMT5Adapter,
  RemoteMT5GatewayAdapter, PaperMT5Adapter) implement it, and the rest of the
  system codes against the interface, never the concrete library.
- **ARCHITECTURE LAYER:** Ports (hexagonal outer boundary, inbound-facing).
- **RESPONSIBILITY:** (a) Force every adapter to implement the same operational
  surface (connect/disconnect/account/symbol/tick/positions/orders), so
  LiveEngine, OrderManager and tests can swap brokers (live/remote/paper)
  without code changes; (b) declare the BROKER-AWARE PROVIDER surface (Phase 14)
  whose snapshots carry provenance so "I don't know" is never faked as data.
- **DEPENDENCIES:** domain models + enums, `market_data.bar_aggregator.BarData`
  (bars are domain-neutral), and the Phase-14 provider snapshot types from
  `adapters.mt5.providers` (typed containers for account/symbol/tick/orders/
  deals/rates/tick-history/calc results).
- **CONNECTS TO:** LiveEngine (main consumer), OrderLifecycleManager
  (`send_order`/`modify_position`/`close_position`), AccountingCore/monitoring
  (snapshot providers), CLI diagnostics, tests (PaperMT5Adapter).
- **KEY CONCEPTS:**
  - Classic methods versus provider methods: the classic surface returns
    domain models (`AccountInfo`, `TickData`, `Position`); the provider surface
    (Phase 14) returns rich snapshot types with `available`/`source`
    (BROKER_NATIVE / FALLBACK_ESTIMATE / UNAVAILABLE) + `captured_at` + `error`
    fields. The default implementations return honest UNAVAILABLE/empty
    results — a base class consumer NEVER receives fabricated data from an
    adapter that didn't implement the method. This is the "no fake numbers"
    invariant at the broker boundary.
  - `get_closed_deals_history`, `execute_market_order`, `place_pending_order`,
    `modify_order`, `cancel_pending_order` have default no-op/False/0
    implementations (not abstract) — backward-compatible evolution: adding a
    capability doesn't force every adapter to implement it immediately, and a
    stub returns a safe, detectable default rather than raising.
  - `get_all_positions()` is documented "NEVER restricted to bot
    magic/symbol" — full account truth, so the accounting layer can see
    non-bot positions too (they must NOT be treated as bot trades).
  - `get_rate_history` normalizes timestamps to UTC (the repo's epoch/zone
    discipline, cf. BUG-058/070) and chooses `copy_rates_range` vs
    `copy_rates_from_pos` based on whether `from_utc` is provided.
- **EDGE CASES & PITFALLS:**
  - Signature changes here cascade into ALL adapters + every test double —
    the repo runs a cross-adapter contract test suite (`test_mt5_providers_phase14.py`).
  - `Any` typing on `from_utc`/`to_utc` (no datetime enforcement) is loose —
    adapters must coerce; a future tightening is possible but would be a
    SHARED API CHANGED event.
  - Return `0` from `execute_market_order` is the "no ticket" sentinel —
    callers must not confuse it with broker ticket 0 (broker tickets are
    positive).