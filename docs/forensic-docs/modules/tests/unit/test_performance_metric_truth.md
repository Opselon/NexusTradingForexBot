# tests/unit/test_performance_metric_truth.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TASK-1 regression suite — Performance Intelligence data-truth audit (TEST-1..24 matrix post-BUG-045 accounting rewrite).
- Classification: win/loss/BE by MONEY only; win+loss+BE sums reconcile to total; profit factor uses GROSS sums not averages; expectancy = net over trades; win-rate denominators explicit.
- R-multiple: `r` from INITIAL risk (`test_r_from_initial_risk`); missing risk → UNKNOWN, never zero (`risk is None`, `r is None` — no fabricated 0.0); R aggregates EXCLUDE unknown entries.
- Excursions: MAE negative / MFE positive sign convention; MAE/MFE invariants on the report; MFE-capture is a portfolio ratio; zero-MFE / missing data handled (`mae, mfe is None` safe paths).
- Drawdown: `max_drawdown_pct is None or == 0.0` when no losing streak — no fake DD.
- Split fills: split fill == one canonical economic trade; all sibling tickets inherit exact context.
- 48 defs / 718 lines; in-memory fixtures + AuditRepository (`_snapshots`, `_bounds` helpers).