# tests/unit/test_order_lifecycle.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Behavioral suite over a MockMT5Port adapter: structural entry + SL/TP generation, risk-engine fixed-dollar sizing, order modification + SL shift, trade autopsy DB persistence.
- Guards: asymmetric RR below configured threshold → `NO_TRADE` with reason `ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD`; volatility-scaled sizing (`vol_tight ≈ 1.00` vs `vol_wide ≈ 0.50` fixed-dollar); successful modify → `success_modify is True` and final SL == 2000.10.
- Autopsy persistence (real SQLite via AuditRepository): ledger row exists; `is_rf == 1` for risk-free BE hit; exit mechanism `BREAK_EVEN_SL_HIT`; MAE == -3.20 / MFE == 15.50 recorded; `initial_sl` vs `final_sl` distinguish modified (SL shifted to 2000.10) vs unmodified (1990.00, `TAKE_PROFIT_HIT`, `is_rf == 0`).
- NOTE: DB reads require the queued-writer flush pattern; close() mid-test breaks the fixture (BUG-058).