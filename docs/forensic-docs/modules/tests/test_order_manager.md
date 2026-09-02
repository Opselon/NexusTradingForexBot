# tests/unit/test_order_manager.py + test_order_manager_audit.py + test_order_lifecycle.py

# test_order_manager.py
- **GUARDS:** execution/order_manager dispatch + protection machinery.
- **KEY ASSERTIONS:** MAX_TOTAL_EXPOSURE gate (1 pos OR 1 pending);
  HARD_MAX_LOTS clamp; market vs pending dispatch routing; entry-context
  staging (register_entry_context); ticket>0 audit logging; rejection
  paths return False with logged reasons; split-fill family context
  binding (BUG-081).
- **PITFALLS IT ENCODES:** the paper adapter's ticket semantics; audit
  queue flush (`_queue.join()`) before asserting order rows.

# test_order_manager_audit.py
- **GUARDS:** order_manager → audit_repository integration.
- **KEY ASSERTIONS:** every dispatch writes the audit order row with the
  right action/reason/execution_mode; close/modify events audited; no
  duplicate rows for repeated calls (dedup keys).
- **PITFALLS IT ENCODES:** queued writes are async — flush before querying;
  never close() mid-test (BUG-058).

# test_order_lifecycle.py
- **GUARDS:** the position lifecycle state machine transitions
  (PROPOSED→SUBMITTED→OPEN→profit/loss states→CLOSED).
- **KEY ASSERTIONS:** hysteresis-gated transitions
  (transition_state_with_hysteresis); breakeven lock arming; trailing;
  giveback severity progression; cleanup on close
  (_cleanup_ticket_state).
- **PITFALLS IT ENCODES:** the 60s grace period suppresses instant exits
  (except kill switch) — tests advance time/tick timestamps explicitly.