# tests/unit/test_log_autopsy_fixes.py

- GUARDS: Log-Autopsy Bug Fix verification — runtime telemetry fixes from the log autopsy: BUG-B hold score must degrade non-linearly during drawdown (no bonus masking, no profit-shield masking); tiered giveback protection; split-order sync on emergency close; scaler cold-start persistence; breakeven clearance across gaps.
- KEY ASSERTIONS:
  - `test_deep_drawdown_drops_score_below_50`; `test_trend_bonus_cannot_mask_drawdown`; `test_micro_profit_below_half_r_is_disarmed`; `test_large_runner_still_locked_in`; `test_emergency_close_propagates_to_sibling_legs` + unrelated tickets NOT closed; `test_fallback_scaler_is_persisted_immediately`; `test_breakeven_defers_when_market_crosses_gap` (14 asserts).
- PITFALLS IT ENCODES: bonuses must never mask drawdown degradation; emergency close must propagate to split legs but never to unrelated positions.
- NOTES: MockMT5Adapter; pairs with test_adaptive_position_management.py (same giveback fixes).
