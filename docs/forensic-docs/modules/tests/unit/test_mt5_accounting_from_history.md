# tests/unit/test_mt5_accounting_from_history.py

- GUARDS: AccountingCore MUST read the normalized broker history (RED phase): strategy rows/trade counts existed but financials were zero because ledger rows carried zero PnL and deal evidence never reached accounting.
- KEY ASSERTIONS:
  - `TestAccountingFromBrokerHistory`: total trades from broker history; net PnL from broker history; win rate from broker history; period report financials REAL; break-even counted, not skipped; cumulative PnL curve real points; equity curve marks source (20 asserts).
- PITFALLS IT ENCODES: financials must derive from REAL deal evidence (fixtures), never be zero-padded; break-even trades are counted trades with a defined outcome.
- NOTES: Real MT5 fixtures via tests/helpers/mt5_fixtures.py — the RED-phase target modules were built against these proofs.
