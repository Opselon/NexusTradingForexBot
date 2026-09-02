# tests/unit/test_strategies_ichimili_phase15c.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 15C unit tests — seedable Ichimoku (Ichimili) strategies: line math matches the Pine reference (donchian/tenkan/kijun/span A/span B, displacement), signal emission, alternation/gap rules, builtin registration, candidate determinism.
- Guards: donchian mid basic math; ichimoku lines match Pine reference exactly; final variant emits BUY in uptrend / SELL in downtrend; displacement lookback RESPECTED (`test_final_variant_respects_displacement_lookback` — no look-ahead through displacement); spaced variant enforces MIN GAP and the gap is parameterizable; builtin registration runs (`register_strategy` at import).
- Candidates: deterministic + content-addressed; version changes with definition (content-hash).
- 17 defs / 220 lines; `_Bar`/`_mk_bars` + `_slow_bull_series`/`_slow_bear_series` fixtures.
- NOTE: research-safety contract — pure bar-based signals, no I/O, no order authority.