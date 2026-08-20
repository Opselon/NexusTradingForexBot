# tests/unit/test_risk_engine.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Enterprise dynamic risk engine invariants: position sizing math, safety ceilings, micro exceptions, scaling, free-margin clamps, boundary/safety protection.
- Guards: kill switch BLOCKS any proposal (`order is None`); XAUUSD dynamic risk engine matrix (7 defs deep — per-symbol parameterized sizing); stop-loss and equity scaling; risk INVARIANCE (same risk budget across market conditions); free-margin protection clamps sizing; safety/boundary conditions (zero/negative balance, absurd ATR) return safe verdicts.
- `test_no_flat_2_lot_bug_regression` — explicit regression: fixed-lot fallback must NOT silently emit a flat 2-lot order when dynamic sizing fails (the historical bug).
- 8 defs / 480 lines; pure-math suite, no MT5. Proposals route through RiskEngine verdict + OrderManager validation.