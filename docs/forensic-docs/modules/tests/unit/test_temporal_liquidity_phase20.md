# tests/unit/test_temporal_liquidity_phase20.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- TEST-TEMPORAL-01..30 — Temporal Liquidity Intelligence + Signal Stability (TASK-TEMPORAL-01): lag/delta/persistence/tsc in features/temporal.py + stability controller.
- Lag: lag1 == previous value; lag2 == two back; lag3 missing → NEUTRAL (never NaN/0 fabrication).
- Delta: delta1 == half-diff ((2-1)/2); delta CLIPPED (saturation at 3.0 — bounded).
- Persistence: fraction math (0.0/1.0 cases).
- TSC: sweep counts bars since change; resets after change.
- Cold start: NEUTRALs during warmup; no zero-distance claims (`test_cold_start_no_zero_distance`).
- Causality: future bars do NOT change past snapshots (`test_future_bars_do_not_change_past`); extract uses ONLY past.
- Stability controller: signal-stability gating, `c.last_event() is None` before first event (truthful absence).
- 53 defs / 530 lines; pure math, deterministic fixtures (`_liq` builder).