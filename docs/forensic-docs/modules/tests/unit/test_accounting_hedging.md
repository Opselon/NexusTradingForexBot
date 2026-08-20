# tests/unit/test_accounting_hedging.py

- GUARDS: Accounting ledger + intelligent hedging policy: audit-ledger recording/metrics over REAL MT5 fixtures and the hedging trigger/policy contract.
- KEY ASSERTIONS:
  - `test_audit_ledger_recording_and_metrics`: ledger rows and metrics derived from real broker fixture data; `test_intelligent_hedging_trigger_and_policy`: hedge triggers only under the defined conditions and respects policy bounds (18 asserts).
- PITFALLS IT ENCODES: hedging must be policy-driven (never ad-hoc); fixture-derived expectations keep the no-synthetic-numbers contract.
- NOTES: `MockMT5Port` shims the terminal while audit repo runs on temp SQLite.
