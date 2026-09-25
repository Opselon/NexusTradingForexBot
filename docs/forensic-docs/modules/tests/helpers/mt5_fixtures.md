# tests/helpers/mt5_fixtures.py

- GUARDS: Loads the REAL MT5 capture fixtures (tests/fixtures/mt5/), captured READ-ONLY from the live MetaQuotes-Demo terminal on 2026-08-17 (capture script deleted after capture). Every value is a real broker response — no synthetic data. This is the empirical anchor for the accounting-from-broker-history and MT5 provider suites.
- KEY ASSERTIONS: 1 (loaded-file sanity check).
- PITFALLS IT ENCODES: fixtures must be loaded via this helper so all consumers get byte-identical broker evidence; tests must NEVER invent broker numbers (the "no synthetic data" contract). Because captures are real, expectations like net-PnL sums must be derived from the fixture itself (e.g. summed deal streams) rather than hard-coded.
- NOTES: Helpers: `fixture_path(name)`, `fixture_objects(name)` (loads stored JSON objects), `fixture_object(name)`. Used by test_mt5_accounting_from_history, test_mt5_history_reconstruction, test_mt5_accounting_api_contract, test_accounting_hedging, test_mt5_providers_phase14.
