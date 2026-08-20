# tests/unit/test_adaptive_position_management.py

- GUARDS: Adaptive position management: profit giveback protection, hysteresis state debouncing, immutable recovery budget.
- KEY ASSERTIONS:
  - `test_profit_giveback_failure_regression` (the log-autopsy giveback fix); `test_hysteresis_state_debouncing` (state toggles need hysteresis, no flapping); `test_immutable_recovery_budget` (recovery budget can't be mutated mid-cycle) (9 asserts).
- PITFALLS IT ENCODES: giveback detection must not fire on noise; debouncing prevents oscillator flapping between states.
- NOTES: MockMT5Adapter + temp AuditRepository; pairs with test_log_autopsy_fixes.py (BUG-B fixes).
