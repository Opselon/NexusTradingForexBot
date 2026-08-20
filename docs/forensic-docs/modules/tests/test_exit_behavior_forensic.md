# tests/unit/test_exit_behavior_forensic.py + test_pending_cancel_reconciliation.py

# test_exit_behavior_forensic.py
- **GUARDS:** The Phase-15 exit-behavior audit — 8 behavioral scenarios
  (D1..D8: strong reversal, long drawdown, AI flip, regime invalidation,
  fast MFE→giveback, early BE→full stop, healthy continuation, isolated
  sweep) + 4 execution-integrity regressions (R1: hold_score<30
  dispatches close; R2: giveback close not suppressed without locked SL;
  R3: time-in-trade decay; R4: EV-breach fires/doesn't fire).
- **KEY ASSERTIONS:** BUG-054 (probs+regime threaded into
  manage_active_positions), BUG-055 (LOSS_HARD_EXIT arbitration),
  BUG-056 (EV anchored at entry × RRR), clock-timestamp discipline
  (age from tick, not host wall clock), giveback close suppression rules.
- **PITFALLS IT ENCODES:** the 60s grace suppresses instant exits
  (advance the tick clock); [POSITION_EXIT_EVAL] structured verdicts are
  asserted (observability contract).

# test_pending_cancel_reconciliation.py
- **GUARDS:** pending-order cancellation + reconcile machinery
  (cancel_pending_order_verified, reconcile_pending_state) —
  "cancel requested" ≠ "cancel confirmed".
- **KEY ASSERTIONS:** cancel completes only after broker state confirms;
  retcode semantics (0 is NOT DONE); reconcile dedup across passes
  (_reconcile_seen); the 30s re-quote lock + ≥1×ATR drift rule;
  MAX_EXPOSURE gate reflects reconciled broker state (the 82%-false-
  MAX_EXPOSURE decomposition).
- **PITFALLS IT ENCODES:** phantom tickets (cancel verified by broker
  history, not assumption); function-local import time hazard (BUG-074)
  — never `import time` inside functions.