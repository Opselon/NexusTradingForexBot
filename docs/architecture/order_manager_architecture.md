# ORDER_MANAGER.PY — Architectural Map (Agent-5, Phase A)

Evidence base: forensic inventory at HEAD `0015608` (post Agent-4 winner-protection
sprint `0839da3`). No edits made during this phase. This document precedes any
extraction (task brief §24).

## 1. Current shape

    6372 LOC, 1 god class + 6 support types

| Item | Span | Notes |
|---|---|---|
| Module constants (18) | L60-155 | exposure caps, BE/giveback/trailing geometry |
| ExitMechanism (Enum) | L158 | exit taxonomy |
| PositionState (Enum) | L172 | 11 in-trade lifecycles |
| PositionEvaluationStep | L190 | trajectory step record |
| LSFTicketState | L209 | LSF per-ticket state |
| PositionProtectionState | L226 | per-ticket protection facts (dataclass) |
| SmartPositionMetrics | L281 | 57 derived metrics (dataclass) |
| OrderLifecycleManager | L348-6372 | 81 methods, 99 distinct `self.*` state attrs |

## 2. Responsibility map (evidence-verified, method-level)

| Responsibility | Methods (span) | State used | Ext. deps | Side effects |
|---|---|---|---|---|
| A. Order dispatch/submission | execute_order, dispatch_order, _clamp_dispatch_volume, count_total_exposure, _is_exposure_available | _processed_orders, _live_tickets_lock+cache, exposure consts | adapter, risk_engine, rule_matrix | BROKER order_send via adapter; audit rows; dedup tombstone |
| B. AI reversal / lifecycle actions | execute_ai_reversal, execute_lifecycle_action | entry context maps | adapter, notifier | broker; audit; telegram |
| C. Pending order mgmt | manage_pending_orders, should_modify_pending_order, _pending_broker_state, cancel_pending_order_verified/with_retry, reconcile_pending_state, _sweep_stale_pending_contexts, _emit_terminal_for_pending | _pending_orders_setup_time, _pending_cancel_reasons, _pending_context_registry | adapter, audit | broker cancel/modify; terminal outcomes |
| D. Position state machine | transition_state_with_hysteresis, _evaluate_candidate_state, _arbitrate_decision | _position_states, _state_transition_candidates (+ reads _sl_modified_flags, _recovery_*, protection state) | — | in-memory only |
| E. Protection geometry & SL mgmt | refresh_protection_state, get_protection_state, calculate_breakeven_sl, _is_sl_at_or_beyond, is_sl_improvement, _protective_sl_floor, apply_breakeven_lock, _maybe_tighten_protective_sl, _tiered_giveback_floor, evaluate_profit_giveback, enforce_profit_giveback_protection, apply_atr_trailing_stop, _should_modify_sl, _log_protection_audit | _protection_state, _sl_modified_flags, _last_modify_sl, _last_mod_price/time | adapter (order_modify), audit, notifier | broker SL modify; audit rows |
| F. Smart metrics / hold score | _calculate_smart_position_metrics, _calculate_hold_value_score, _recalculate_hold_score_with_position_state, _resolve_position_management_scenario, _calculate_protection_score, _calculate_adaptive_evidence_scores, _calculate_trajectory_features, _add_trajectory_step, _calculate_continuous_giveback_severity | _mfe/mae trackers, _time_* trackers, _stagnation/adverse/favorable ticks, _trajectory_history, _hold_score_tracker(+base/last_reasons) | FeatureVector | in-memory |
| G. Recovery mode | _initialize_recovery_mode, _evaluate_recovery_budget_and_horizon, _evaluate_minimum_loss_optimization | _recovery_* (6 dicts) | — | in-memory |
| H. Position loop orchestration | manage_active_positions (1465L) | reads nearly everything | adapter, audit, notifier | broker close/modify; persistence; telemetry |
| I. Fill/reconciliation | reconcile_missed_closes, refresh_live_tickets_cache, _is_closed_ticket, _broker_close_verified, _ensure_ticket_bootstrap, _update_lsf_desync_metrics, _update_tick_state | _live_tickets_cache, _reconcile_seen, _lsf_state | adapter, audit | broker reads; audit |
| J. Outcome/autopsy | _record_experience_outcome, _update_mfe_mae, _capture_reversal_state, _close_sibling_legs, _cleanup_ticket_state | experience_engine, lifecycle_tracker, ~45 per-ticket dicts | experience_engine, audit | experience ledger; state teardown |
| K. Telemetry/telegram | should_emit_console_telemetry, register_telegram_message, register_order_message, _send_telegram, _log_throttled_be_failure | notifier, _order_message_ids, _order_id_to_message_id, _last_telemetry_time | notifier | telegram (INV-010 read-only consumer) |

## 3. State ownership map (key structures)

| State | Writers | Readers | Lock | Lifecycle | Notes |
|---|---|---|---|---|---|
| _processed_orders | execute_order | execute_order | none (loop-thread only) | engine lifetime | order-id dedup tombstone |
| _live_tickets_cache | refresh/reconcile/manage/close | many | **_live_tickets_lock** (12 CS) | engine lifetime | the ONLY locked structure |
| _protection_state | get_protection_state (lazy init), refresh (via dataclass) | 12 sites via accessor; debug_snapshot via method | none | NOT in cleanup tuple; bounded by open tickets | access 100% through accessor |
| _position_states / _state_transition_candidates | transition_state_with_hysteresis only | evaluate_candidate, cleanup, live_engine(L4507), tests | none | cleanup tuple | state machine core |
| _sl_modified_flags | refresh, apply_breakeven_lock, _maybe_tighten, apply_atr_trailing, manage_active_positions, cleanup | evaluate_candidate, manage loop | none | cleanup tuple | mirrors broker truth |
| _recovery_* (6 dicts) | _initialize_recovery_mode, _evaluate_recovery_budget_and_horizon, cleanup | _evaluate_candidate_state, manage loop | none | cleanup tuple | recovery budget |
| ~40 per-ticket trackers | manage_active_positions, _record_experience_outcome, capture/update fns | cleanup tuple (single teardown) | none | **atomic bundle teardown in _cleanup_ticket_state** | THE cross-cutting invariant |

CRITICAL INVARIANT: `_cleanup_ticket_state` releases ~45 per-ticket dicts as ONE
bundle, atomic with the close/autopsy path. Any state extraction must preserve
this single teardown (or route through an explicit per-ticket state object).

## 4. Concurrency map

- SINGLE loop thread owns: dispatch, SL/TP management, state transitions, cleanup.
  No asyncio, no worker threads inside order_manager (0 async methods, 0 Thread()).
- The ONE lock: `_live_tickets_lock` (threading.Lock) protecting `_live_tickets_cache`
  (12 critical sections) — shared with web reads (debug_snapshot) so the cache
  can be read from request threads.
- Hot-restart path: live_engine swaps `order_manager.adapter`/`mt5_adapter`
  (L5684-5703) — adapter replacement is externally driven.
- No queues, no timers of its own; telemetry cadence gated by monotonic time.

## 5. Order/position state machine (actual, from code)

PositionState (11 states): PROFIT_UNPROTECTED -> PROFIT_PROTECTED -> PROFIT_TRAILING;
giveback branch PROFIT_GIVEBACK_WARNING <-> CRITICAL; loss branch LOSS_EARLY ->
LOSS_RECOVERY_CANDIDATE -> CONFIRMED / FAILING -> LOSS_EXIT_PRESSURE -> LOSS_HARD_EXIT.
Transitions go through `transition_state_with_hysteresis` (count-based + time-based
debounce; emergency bypass: LOSS_HARD_EXIT / PROFIT_GIVEBACK_CRITICAL honored
immediately, incl. on first observation). Candidate states are staged in
`_state_transition_candidates` (target, now, count) until thresholds met.

Order-level idempotency: `execute_order` checks `_processed_orders[order_id]`
BEFORE dispatch and tombstones after (both success AND failure outcomes),
preventing duplicate submissions across reconciliation replays.

## 6. Broker boundary

ALL broker interaction flows through `self.adapter` (IMT5Port) — 21 call sites,
no direct `order_send` anywhere in this file (grep = 0). Broker-touching methods:
execute_order, dispatch_order, _clamp_dispatch_volume, execute_ai_reversal,
execute_lifecycle_action, refresh_live_tickets_cache, _broker_close_verified,
apply_breakeven_lock, _maybe_tighten_protective_sl,
enforce_profit_giveback_protection, apply_atr_trailing_stop,
manage_active_positions (10 sites), reconcile_missed_closes, _close_sibling_legs.
The adapter contract (IMT5Port) is the existing boundary — no new adapter needed.

## 7. Persistence boundary

`self.audit` (AuditRepository) used in 19 sites: log_order (protection audit),
experience/ledger writes via _record_experience_outcome, failure rows,
terminal pending outcomes (via execution.terminal_outcome). Persistence calls
inside dispatch/execution methods are part of execution invariants — they are
NOT a separable persistence layer; only the protection-audit write is already
isolated (`_log_protection_audit`).

## 8. Consumer / fan-in map

| Consumer | Symbol | Public? | Usage | Risk |
|---|---|---|---|---|
| application/live_engine | OrderLifecycleManager | public | constructs, wires adapter/audit/notifier/risk/experience, calls dispatch/manage/reversal/lifecycle; hot-swap adapter; reads `_position_states`, `_hold_score_tracker`, `_safe_feature_float` (de-facto private) | HIGH |
| web/debug_snapshot.py | get_protection_state, _live_tickets_cache | mixed | reads protection + live cache (locked) | MEDIUM |
| 15 test files | OrderLifecycleManager + PositionState + internals | de-facto public | construct with fakes; POKE PRIVATE DICTS directly (`_hold_score_tracker`, `_entry_timestamps`, `_entry_prices`, `_entry_directions`, `_last_known_volume`, `_processed_orders`, `_live_tickets_cache`, `_last_reasons_tracker`) | HIGH for any state relocation |

## 9. Candidate seams & decisions

| Seam | Cohesion | State ownership | Risk | Coupling reduction | Decision |
|---|---|---|---|---|---|
| S1 ProtectionLedger (PositionProtectionState + per-ticket dict + get/refresh) | HIGH (self-described per-ticket facts) | CLEAN: dict touched at 4 sites only; all external consumers via methods | LOW-MED | Removes dataclass+dict from god class; accessor stays | **EXTRACT FIRST** |
| S2 PositionStateMachine (_position_states + candidates + transition/evaluate) | HIGH logic | MEDIUM: reads _sl_modified_flags/_recovery_*/protection (cross-cluster reads) | MED | Real | EXTRACT LATER (after S1) |
| S3 RecoveryBudget (6 dicts + 2 fns) | HIGH | MEDIUM: read by evaluate_candidate | MED | Real | EXTRACT LATER |
| S4 SmartMetrics/hold-score calculators | HIGH (pure-ish) | LOW: read trackers; write trajectory | MED | High LOC, zero I/O | EXTRACT LATER |
| S5 dispatch/submit pipeline | HIGH | couples _processed_orders + exposure + risk | HIGH | — | KEEP in class |
| S6 manage_active_positions decomposition | — | reads everything | VERY HIGH | — | BLOCK (needs S1-S4 first) |

## 10. Recommended extraction order

S1 ProtectionLedger -> S3 RecoveryBudget -> S2 StateMachine -> S4 metrics.
Each behind facade methods on OrderLifecycleManager; per-ticket teardown
bundle preserved (ledger exposes `drop_ticket(ticket)` called from
_cleanup_ticket_state).

## 11. Golden baseline

11 execution suites, pre-extraction: ALL PASS (see pytest_om_baseline.txt,
RC=0) — order_lifecycle, execution_architecture, hardened_protocol,
adaptive_position_management, winner_protection_geometry_sprint,
order_manager_exit_bugs, trade_lifecycle_task3, lifecycle_bug140,
log_autopsy_fixes, accounting_hedging, rule_matrix.


## 12. S2 PositionStateMachine — extraction record (Agent-5, post-S3)

- New modules: execution/position_states.py (the 11-state enum, single
  source; facade re-exports), execution/position_state_machine.py
  (PositionStateMachine: _states + _candidates + transition_with_hysteresis,
  verbatim rules; hysteresis params injected via a getter — the machine owns
  state + rules, NOT config/broker).
- Facade: order_manager.transition_state_with_hysteresis is a one-line
  delegate (same signature); compatibility @properties expose the machine's
  LIVE dicts under the historical names (_position_states /
  _state_transition_candidates) — live_engine's bool check and 6 test sites
  unchanged; cleanup calls _state_machine.drop_ticket() inside the atomic
  teardown.
- State graph (actual): first observation seeds a safe neutral state
  (profit-side targets -> PROFIT_UNPROTECTED, else LOSS_RECOVERY_CANDIDATE)
  except emergency targets (LOSS_HARD_EXIT / PROFIT_GIVEBACK_CRITICAL) which
  bypass with zero latency incl. on first observation; same-state
  observation cancels the candidate; normal transitions require BOTH
  min_confirmation_duration AND min_observation_count (window never resets);
  confirmation logs [STATE MACHINE TRANSITIONED].
- Coupling before/after: the machine owns NO recovery/protection state —
  evidence-based evaluate stage stays in the manager and READS budget
  remaining (S3 view) + SL-modified/protection facts; the machine receives
  only the resulting target state. Clean read-only/command boundary.
- Verification: 10-test golden (written pre-extraction against the original
  methods, green post) + 12 execution suites RC=0; perf probe 1.634us per
  repeat-observation transition (O(1), no I/O).
- Remaining seams: S4 intelligence/metrics calculators; S6
  manage_active_positions decomposition (last, after S4).


## 13. S4 PositionIntelligence (SmartMetrics kernel) — extraction record (Agent-5, post-S2)

- New module: execution/position_intelligence.py (356L) — SmartMetricsInputs
  dataclass (every input explicit) + _safe_feature_float (NaN/inf guards) +
  _estimate_liquidation_impact (Almgren-Chriss, eta parameterized) +
  calculate_smart_metrics (241L kernel VERBATIM — all 57 metrics).
- Purity: zero self.* references, zero I/O, imports carry no broker/audit/
  notifier surfaces (enforced by a golden source-scan test).
- Facade: order_manager._calculate_smart_position_metrics + the 3 helpers are
  thin delegates building SmartMetricsInputs from self.*; 6 external call
  sites unchanged; formulas exist ONLY in the kernel.
- Coupling before/after: the kernel previously read 15+ manager attributes
  inline; now the manager passes an explicit immutable input bundle — the
  intelligence has no reference to OrderLifecycleManager (no fake boundary).
- Units documented (price/ATR/spread in price units, USD via contract-size,
  duration seconds) — NOT normalized.
- Parity: 57-key dict equality over representative/edge cases + NaN/inf
  feature guards + thin-facade identity; perf 16.8us per full calculation.
- Correction disclosed: a regime= input erroneously added by the extraction
  script (the original method never read features.regime_state) was caught by
  mypy pre-commit and removed — the kernel contains exactly the original 57
  metrics.
- Remaining intelligence methods are STATE_MUTATING tracker writers
  (hold-value score writes _rolling_spreads; trajectory/tick/MFE-MAE updaters)
  — they stay in the manager; extracting them is future work only with an
  explicit tracker-state owner.
- Next: S6 manage_active_positions decomposition (last, highest risk).


## 14. S6 manage_active_positions — dead-ticket autopsy sweep extraction (Agent-5)

- Forensic phase map (1435L method): account snapshot refresh; falling-knife
  protection; live-tickets cache rebuild (the ONLY lock, 12 critical
  sections); Phase-14 reconcile close-loop (isolated); dead-ticket sweep +
  autopsy + experience outcome + teardown (~460L); rolling spread; per-
  position loop with 11 sections (bootstrap, protection refresh, tick
  trackers, entry-mod/partial-close [broker], throttled hold-score,
  trajectory+state, AI-flip [broker], protection priority chain, telemetry,
  rule-matrix, arbitration, CLOSE/MODIFY dispatch [broker+audit]).
- Side-effect ordering declared sacred and preserved: reconcile BEFORE
  dead-sweep BEFORE no-positions return; protection priority chain order
  byte-identical; teardown bundle atomic.
- S6 seam extracted: _sweep_dead_tickets(symbol, positions, current_tick,
  now, symbol_info, atr) — the vanished-ticket autopsy concern moved
  VERBATIM as a method extraction on the same object (no dependency change,
  no state duplication, no broker writes added). Call sits at the identical
  position. `atr` passed explicitly (derived pre-loop from feature_vector).
- Golden execution-trace tests (written PRE-extraction, green after):
  vanished-ticket trace (deal lookup -> autopsy -> outcome -> teardown ->
  cache release), BUG-046 window anchoring (>=24h, <=7d), live-ticket
  survival. Characterization discovery: the Phase-14 reconcile close-loop
  probes deal history even with zero positions (test corrected to real
  behavior).
- Pre-existing unrelated failure proven via git worktree at pre-S6 commit:
  closed-loop BUG-140 integration expects p0e-bug140-1 while BUG-185 moved
  the contract to p0e-bug185-1 (owner's lineage change, predates S6).
- Remaining in manage_active_positions: the per-position loop (bootstrap,
  protection refresh, trackers, entry-mod/partial-close, throttled scoring,
  trajectory/state, AI-flip, protection chain, telemetry, rule-matrix,
  arbitration, dispatch). Future extractions require tracker-state ownership
  first (hold-value writes _rolling_spreads; trajectory/tick updaters).
