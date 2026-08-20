# src/nexus_scalp/execution/order_manager.py

- **PURPOSE:** The AUTHORITATIVE order + position lifecycle manager — 6,209
  lines implementing the full execution plumbing: dispatch routing (market/
  pending), 11-state position protection state machine, LSF (local state
  feature) tracking, hold-score engine, adaptive recovery, AI reversal,
  breakeven lock, trailing, profit giveback protection, partial TP,
  pending-order lifecycle (verified cancel / reconcile / re-quote lock),
  broker-close verification, sibling-leg closure, ledger autopsy context +
  experience outcomes, and Telegram notifications.
- **ARCHITECTURE LAYER:** Execution (Application). The ONLY component with
  true order authority (besides the raw adapter calls it makes). Every
  proposal from RiskEngine passes through here; every protective action is
  decided here.
- **RESPONSIBILITY:** (a) NEW ENTRY: exposure gate (MAX_TOTAL_EXPOSURE = 1
  position OR 1 pending engine-wide), HARD_MAX_LOTS clamp + free-margin
  pre-check, entry-context staging (ledger autopsy WHY), broker dispatch +
  audit order log; (b) ACTIVE POSITION management on EVERY tick: state
  machine + arbitration (see below); (c) PENDING orders: setup tracking,
  30s re-quote lock, ≥1.0×ATR drift rule, verified cancellation (cancel is
  not complete until broker state confirms — `_pending_broker_state`),
  reconciliation pass; (d) CLOSE: broker-close verification
  (`_broker_close_verified`), sibling-leg closure (emergency close of one
  leg closes every leg of the same originating order_id), ledger autopsy
  finalize, experience outcome recording, lifecycle finalize (BUG-086).
- **DEPENDENCIES:** IMT5Port adapter, AuditRepository, TelegramNotifier,
  RuleMatrixEngine, AlgoConfig, RiskEngine (optional clamp), Experience
  engine, lifecycle tracker; domain models + enums; `datetime(UTC)`.
- **CONNECTS TO:** LiveEngine (per-tick `manage_active_positions` +
  dispatch calls), risk engine output, policy signals, audit tables
  (orders/ledger/experiences/outcomes), Telegram, the UI positions view,
  tests (test_order_manager*, test_exit_behavior_forensic,
  test_order_lifecycle, test_bug081_forensics, ...).
- **KEY CONCEPTS:**
  - **`PositionState` (11-state)** — PROFIT_UNPROTECTED → PROFIT_PROTECTED/
    PROFIT_TRAILING/PARTIAL_CLOSED → PROFIT_GIVEBACK_WARNING →
    PROFIT_GIVEBACK_CRITICAL; LOSS_EARLY → LOSS_RECOVERY_CANDIDATE →
    LOSS_RECOVERY_CONFIRMED → LOSS_RECOVERY_FAILING → LOSS_EXIT_PRESSURE →
    LOSS_HARD_EXIT. Transitions are HYSTERESIS-gated (`transition_state_with_hysteresis`:
    candidate must persist N evaluations within a window before adoption —
    prevents state flicker on noisy ticks).
  - **`_arbitrate_decision` — the 5-level exit hierarchy** (HOLD can NEVER
    override a protective EXIT): (1) legacy emergency cuts (S01-S13/S21/S22
    + RULE_* rule-matrix CLOSE verdicts) VETO everything — with a 60-second
    grace period: instant exits are suppressed so a trade can breathe
    through entry spread (except S01_CRITICAL_COMPOUND_KILL_SWITCH);
    (2) LOSS_HARD_EXIT (recovery budget/horizon exhausted) + minimum-loss
    EV optimization (`_evaluate_minimum_loss_optimization` — expected
    recovery anchored at ENTRY risk × RRR, EV decreases monotonically with
    drawdown, BUG-056); (3) PROFIT_GIVEBACK_CRITICAL (tiered giveback
    floor breach); (4) adaptive exit pressure (low recovery probability);
    (5) strategy/router suggestions; default HOLD. Time-in-trade derives
    from the CURRENT TICK timestamp, never host clock (the clock-bug
    lesson: broker clock ahead of host produced negative ages and
    suppressed every time-based exit).
  - **Bounded per-ticket state** (dicts keyed by MT5 ticket):
    `_trajectory_history` (deque of PositionEvaluationStep — bounded),
    `_reversal_events` (MODEL_REVERSAL/REGIME_REVERSAL/
    LIQUIDITY_REVERSAL/CONFIDENCE_COLLAPSE captured while open, persisted
    to the autopsy), `_protection_state` (monotonic peak + BE lock +
    giveback arming + close idempotency), `_sl_modified_flags`
    (broker-truth SL movement — a stop at entry is RISK_FREE_SL_HIT only
    when `was_sl_modified=True`, BUG-081).
  - **BUG-081 split-fill context inheritance:** entry context staged in
    `_pending_context_registry` (bounded: TTL 3600s, max 64 entries) keyed
    by order/request id; EVERY sibling ticket of a broker split-fill binds
    the SAME immutable context; family pruned on final-sibling close;
    provenance gaps (`NO_STAGED_CONTEXT`) recorded in
    `_unbound_ticket_contexts` — never silently confidence 0.0.
  - **Hold score** (`_calculate_hold_value_score` → convex drawdown penalty
    80×ratio^1.5 capped 80, time decay, spread expansion penalty, trend
    bonus suppressed when ratio ≥0.30, profit-shield floor only when
    profit ≥ 0) — evaluated per position every ~500ms; hold_score < 30 in
    drawdown → S09_CRITICAL_HOLD_SCORE_BREACH_BAILOUT.
  - **Breakeven lock** (`apply_breakeven_lock`): trigger $1.00 profit →
    move SL to entry + $0.25 lock (covers commission+spread); trailing
    distance $1.50 ATR-scaled; `min_modify_step_usd` 0.20 gate before any
    modify IPC (protects the broker from modify spam).
  - **Broker-truth discipline:** `cancel_pending_order_verified` +
    `_pending_broker_state` (cancel not done until broker state confirms),
    `reconcile_pending_state` + `_reconcile_seen` (dedup across passes/
    restarts), `_broker_close_verified`, `_is_closed_ticket` guard so a
    CLOSED position never receives protective modifications (TASK-7
    invariant).
  - **Canonical exit classification:** `_forced_exit_mechanisms` override
    the broker-history heuristic at autopsy; `_exit_label()` maps the
    canonical ExitReason taxonomy for Telegram (notify_canonical_close
    built from the SAME exit_mechanism the classifier writes to the ledger
    — never re-inferred, never defaulted to MANUAL).
- **HOT PATH / PERFORMANCE:** `manage_active_positions` runs per tick for
  each open position — ALL decisions are in-memory dict lookups + math;
  broker I/O only on actual actions (modify/close) which are throttled
  (`min_step`, hold-eval 500ms cadence, telemetry throttle);
  reconciliation broker fetch is monotonic-gated (`_last_reconcile_attempt
  — BUG-090: never a per-tick history_deals_get). No sync DB on the tick
  path (audit writes queued via AuditRepository worker).
- **EDGE CASES & PITFALLS:**
  - 60s grace is the documented trade-off: a REAL emergency in the first
    minute (except kill switch) waits for the grace — deliberate breathing
    room, but it means insta-loss exits are deferred.
  - `count_total_exposure` includes pendings: MAX_EXPOSURE_REACHED blocks
    entry — the false-82% MAX_EXPOSURE decomposition (pending-cancel
    forensics) showed broker-pending state must be reconciled before the
    gate is trusted.
  - Memory discipline: every `dict[int, ...]` state map is bounded by
    the number of live tickets + cleanup on close (`_cleanup_ticket_state`);
    unbounded growth across restarts would be a leak (regression guarded).