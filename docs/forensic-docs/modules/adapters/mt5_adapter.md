# src/nexus_scalp/adapters/mt5/mt5_adapter.py

- **PURPOSE:** The Direct MetaTrader 5 Win32 IPC adapter — `DirectMT5Adapter`
  implementing `IMT5Port` over the official `MetaTrader5` Python package
  (native terminal IPC). The production execution path in LIVE mode.
- **ARCHITECTURE LAYER:** Adapters (infrastructure boundary). Highest-risk
  component class in the repo (external platform dependency, IPC, order
  dispatch).
- **RESPONSIBILITY:** (a) connect/disconnect lifecycle with retry +
  timeout (5000ms default); (b) classic domain reads (account/symbol/tick);
  (c) Phase-14 BROKER-AWARE provider methods — every snapshot carries
  provenance + captured_at (get_account_snapshot via mt5.account_info(),
  get_symbol_snapshot via symbol_info + tick separation, get_broker_tick
  via symbol_info_tick, get_all_positions (ALL account positions — never
  bot-magic-only), get_pending_orders_snapshot, history orders/deals via
  history_orders_get/history_deals_get, rate history via
  copy_rates_from_pos/copy_rates_range with UTC normalization,
  tick history via copy_ticks_from/copy_ticks_range, order_calc_profit/
  order_calc_margin snapshots); (d) call diagnostics: every MT5 call is
  wrapped (`run_mt5_call` from diagnostics.py) recording latency/retcode/
  errors into `MT5CallDiagnostic`, surfaced via
  `diagnostics_summary()` (IPC telemetry endpoint).
- **DEPENDENCIES:** MetaTrader5 package, `ports.mt5_port`, `adapters.mt5.
  providers` (snapshot types), `adapters.mt5.diagnostics`
  (MT5ConnectionState/run_mt5_call), domain models, logging.
- **CONNECTS TO:** LiveEngine (primary), OrderManager (orders), broker
  history + accounting reconstruction, `/api/debug/ipc-telemetry`,
  tests (test_mt5_adapter, test_mt5_providers_phase14,
  test_mt5_raw_fixtures).
- **KEY CONCEPTS:**
  - **UTC discipline:** all broker timestamps normalize via
    `broker_epoch_to_utc` (servertime→UTC; the epoch bug class BUG-058/070
    makes this the adapter's most safety-relevant behavior).
  - **Broker-truth honesty:** snapshots default `available=False`
    source=UNAVAILABLE; real values only when the MT5 call actually
    succeeded — no fabricated numbers at the broker boundary.
  - **Diagnostics on every call:** `run_mt5_call` wraps the IPC with
    operation name, duration, retcode label (`retcode_label` maps
    retcodes like DONE=10009 vs 0 which is NOT a trade retcode — the
    pending-cancel forensics lesson), error classification, and emits a
    structured log line. This is the observability backbone of the
    "is MT5 healthy?" answers.
  - **Rate history selection:** `copy_rates_range` when from_utc given,
    else `copy_rates_from_pos(count)`.
- **HOT PATH / PERFORMANCE:** IPC calls are inherently blocking (the
  binding is synchronous); the engine mitigates by calling the adapter
  at its own cadence (ticks/bars) and never inside the async loop's pure
  sections; every call carries latency recording — the IPC latency stats
  endpoint (`/api/debug/ipc-telemetry`) reports reconnect count and
  percentiles.
- **EDGE CASES & PITFALLS:** terminal not running → connect() False with
  explicit diagnostics (engine preflight aborts before trading);
  MT5ConnectionState is REAL connection state (never derived from config —
  the "is_real_account/connection" truth rule); retcode 0 ≠ success
  (retcode_label documents the mapping); concurrent access to the MT5
  binding from multiple threads can deadlock — callers must serialize
  (the engine does; never call the adapter from worker threads without
  coordination).