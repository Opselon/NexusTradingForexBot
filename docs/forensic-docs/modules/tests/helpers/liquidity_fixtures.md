# tests/helpers/liquidity_fixtures.py

- GUARDS: Deterministic bar fixtures for the liquidity feature suites (TASK-01-60D-LIQUIDITY). Engineered scenarios produce KNOWN structural features (a swing high at a specific index, a later touch, a sweep) so causality tests can assert exact timestamps.
- KEY ASSERTIONS: 1 (`assert` inside docstring only) — no runtime asserts; the module is pure fixture math. The core warning (docstring): flat/symmetric bars become fractal pivots EVERYWHERE (every bar is a local max/min) which pollutes swing detection.
- PITFALLS IT ENCODES: the pivot-free building block is a MONOTONIC RAMP — a ramp produces NO interior fractals, so the only swings in a scenario are the ones the test engineer put there; accidental symmetry must be avoided or swing detection sees garbage.
- NOTES: Builders: `bar()`, `ramp_bars()`, `steady_bars()`, `swing_high_bars()`, `swing_low_bars()`, `sweep_pool_bars()`, `bars_to_frame()`. Consumed by test_liquidity_engine_causality / _contract / _features.
