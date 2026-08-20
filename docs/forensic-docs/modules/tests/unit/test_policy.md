# tests/unit/test_policy.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- SignalPolicy behavioral tests via MockOrderManager: re-entry blocking, SR support-margin relaxation, tick sweeps, confidence handling, execution-ids.
- Pitfall encoded: a bare `SignalPolicy()` has no rule_matrix → only model probabilities decide (no rule filters). Rejection paths tested with explicit rule setup + refresh (see skill notes).
- Guards: SAME_LEVEL_REENTRY_BLOCKED fires only while live ticket exists and CLEARS when no live orders; strong-bearish SR support margin relaxation allows sell (no `SELL_REJECTED_SR_SUPPORT_MARGIN_FAIL`); tick sweep requires model confidence (no `TICK_LEVEL_LIQUIDITY_SWEEP` bypass at low confidence).
- Confidence: candidate confidence IS the raw probability, not floored (`proposal2.confidence >= 0.5` for p=0.5 vs `proposal.confidence <= 0.45`); NO_TRADE block stamped with `execution_id` (`EXEC-` prefix, len ≥ 20); execution_id unique across evaluations and present on actionable proposals.
- Same-second tick trap: to produce a genuine second signal row the probe timestamp must advance a full minute (TICK_DUPLICATE_SUPPRESSED otherwise).
- 13 defs / 507 lines.