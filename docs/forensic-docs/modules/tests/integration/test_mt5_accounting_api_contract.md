# tests/integration/test_mt5_accounting_api_contract.py

- GUARDS: API + chart contract (RED phase) — the accounting core wired to a temp SQLite repo seeded from the REAL MT5 fixtures; the API must serve the SAME numbers the database holds (trade totals, net PnL, win rate from real broker evidence).
- KEY ASSERTIONS:
  - `TestAccountPerformanceEndpoint`, `TestEquityCurveAndClosedHistory`, `TestStrategyAttributionFinancialConsistency`: API numbers == DB rows == broker fixture sums (32 asserts).
- PITFALLS IT ENCODES: NO synthetic financials — even when the source ledger is the raw fixture, sums must be reconciled against broker evidence; break-even trades counted, not skipped.
- NOTES: Fixture-backed via tests/helpers/mt5_fixtures.py; `_FakeEngine` drives the app.
