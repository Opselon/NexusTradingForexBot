# tests/unit/test_mt5_adapter.py + test_mt5_database_persistence.py + test_mt5_raw_fixtures.py

# test_mt5_adapter.py
- **GUARDS:** adapters/mt5/mt5_adapter DirectMT5Adapter against the
  mt5_fixtures fake terminal.
- **KEY ASSERTIONS:** connect/disconnect lifecycle; account/symbol/tick
  reads; UTC normalization of broker epochs (broker_epoch_to_utc);
  provider snapshots carry provenance (available/source/captured_at);
  retcode extraction + diagnostics recording (run_mt5_call);
  get_rate_history selection (from_pos vs range).
- **PITFALLS IT ENCODES:** retcode 0 is NOT a trade retcode (phantom
  cancel forensics); absent fields → None (never crash).

# test_mt5_database_persistence.py
- **GUARDS:** broker history → local persistence (broker_history_sync +
  audit_repository sync_broker_history).
- **KEY ASSERTIONS:** idempotent sync (repeat passes upsert, never
  duplicate); UTC-normalized storage; logical trade reconstruction
  (open/close pairing); warm-accounting kick.
- **PITFALLS IT ENCODES:** row_factory=sqlite3.Row needed for dict(row);
  windowed sync bounded per pass.

# test_mt5_raw_fixtures.py
- **GUARDS:** the raw MT5 fixture data fidelity (tests/helpers/mt5_fixtures)
  — fixture tuples match real MetaTrader5 struct shapes.
- **KEY ASSERTIONS:** fixture field names/order/types match the binding;
  account_info/symbol_info/tick/position/deal tuple shapes.
- **PITFALLS IT ENCODES:** a fixture shape drift silently breaks every
  adapter test — this suite pins the contract.