# tests/unit/test_mt5_providers_phase14.py

- GUARDS: MT5 broker-aware provider snapshots (Phase 14): pure mapping/validation layer — account/symbol/position snapshots from raw MT5 objects, UTC normalization, bar validation, diagnostics wrapper, risk/profit broker provenance.
- KEY ASSERTIONS:
  - `TestUtcNormalization`: aware/naive/numpy64/ISO-Z/float epochs normalize to UTC; garbage → None; `TestSymbolSnapshotMapping`: broker epoch offset applied; spec vs tick separated; stale tick detected; both-none → unavailable; `TestBarValidation`: duplicate/descending timestamps, high-low violations, negative volume all detected; `TestDiagnosticsWrapper`: success/failure/exception recorded with duration + error code, retcode labels, connection state machine; `TestRiskBrokerProvenance`: broker-native margin/profit preferred, estimate fallback only when unavailable (95 asserts).
- PITFALLS IT ENCODES: missing optional fields are None, never fake; broker-native values are provenance-first (estimates are a labelled fallback).
- NOTES: _FakeMT5Module/_FakeCalcAdapter harness; counterpart of integration test_mt5_accounting_api_contract.py.
