# HANDOFF — Agent 12: Execution pipeline deep forensic + regression net (CHG-0058)

- Date: 2026-09-05 (Asia/Tehran; UTC+03:30)
- Agent: Agent 12 (Nexus-Main orchestrated)
- Role: Execution / OrderManager / Protection / Recovery Forensics
- Task: user brief 2026-09-05 (26-section execution pipeline mission; CHG-0058 / TASK-AGENT12-EXEC-FORENSIC)
- Base at start: 30a4f448. Register commit: 2d030668 (CHG-0058 + TASK-AGENT12-EXEC-FORENSIC).
- End HEAD: f355c548 (regression net). Branch: main.
- Commits this mission: 2d030668, f355c548.
- Files touched: tests/unit/test_agent12_execution_forensic.py (NEW).

## Summary

Deep forensic sweep over the REAL execution lifecycle (TradeProposal ->
policy/confluence -> Regime Guardian -> confidence gate -> RiskEngine ->
OrderManager -> adapter -> 11-state lifecycle -> protection -> recovery ->
broker reconciliation -> accounting -> autopsy) and the reported 60-scenario
router / protection ledger / teardown surfaces. Delegated subagent sweeps
stalled under the custom provider 429s (max_iterations on the leaf model);
Nexus-Main took over via direct code forensics + executable paper-fixture
probes. All three parallel execution/risk fixes (BUG-239/240/241/242 by
Agent-11, commits 152e8ebe / ad06738f / d0a9b6d4) were independently
verified against HEAD code and then pinned by this mission's regression net.

## Forensic map (26 brief sections -> code truth)

- SS1 Single dispatch authority — PROVEN. Repo-wide broker-write call-site
  census (`send_order|execute_market_order|place_pending_order|close_position|
  modify_position|modify_order|cancel_pending_order`) across `src/nexus_scalp`:
  the ONLY production call sites that reach an execution verb live inside
  `execution/order_manager.py` (21 sites: execute_order:703, dispatch_order:941/1012,
  execute_ai_reversal:1281, execute_lifecycle_action:1376/1397/1419,
  close_position_manual:1481, modify_position_manual:1505, apply_breakeven_lock:2217,
  _maybe_tighten_protective_sl:2321, enforce_profit_giveback_protection:2569,
  apply_atr_trailing_stop:2664, _execute_position_action:4966/5001/5027/5052/5112,
  _run_protection_chain:5214/5257/5346, _close_sibling_legs:6277) — all are the
  OrderManager itself. The web operator surface (`/api/positions/modify|close`)
  previously called `engine.adapter.*` directly (the only second path) and was
  fixed by Agent-11 BUG-242 (d0a9b6d4): web now routes exclusively through
  `OrderLifecycleManager.{close_position_manual,modify_position_manual}`,
  fail-closed HTTP 400 when the manager is absent. Research/replay paths are
  order-authority-free (EXEC-1/EXEC-2 guards in test_qa_deep_execution_safety).
  No hidden execution helper, background dispatch, retry second path, emergency
  or recovery or shadow second path remains at HEAD.

- SS2 60-scenario router — implemented set is 21 S-codes at HEAD
  (`_resolve_position_management_scenario` order_manager.py:3117):
  S01_CRITICAL_COMPOUND_KILL_SWITCH, S02_TOXIC_FLOW_KILL_SWITCH,
  S04..S13 (structure/toxicity/desync/spread/MAE/hold-score bailout family),
  S21_HARD_STAGNATION_TIMEOUT, S22_EXTENDED_CAPITAL_LOCK_TIMEOUT,
  S32_HIGH_PROFIT_SCALE_OUT, S44_HEALTHY_WINNER_NORMAL_TRAIL,
  S47_STANDARD_BREAK_EVEN_LOCK, S48_LOW_IMPACT_FAST_BREAK_EVEN,
  S52_SPREAD_SPIKE_STOP_DEFER, S56_MISSED_POSITION_STATE_RECONSTRUCTION,
  S60_DEFAULT_CONTROLLED_HOLD. "60" in the brief name is the slot space, not
  the populated count. Precedence: kill-switch (winning-trade shield: never
  closes winners) -> hold-score/MAE/toxicity/desync/spread CLOSE band ->
  timeout band -> scale-out/trail/breakeven ladder -> defer/monitor -> HOLD.
  `_arbitrate_decision` (3446) enforces the hierarchy: L1 legacy emergency +
  rule-matrix CLOSE (RULE_*) -> L2 LOSS_HARD_EXIT -> minimum-loss optimization
  -> PROFIT_GIVEBACK_CRITICAL -> L3 LOSS_EXIT_PRESSURE -> L4 trail/BE/partial
  -> giveback warning -> HOLD. HOLD can never override a protective CLOSE.
  60-second survival grace suppresses non-kill-switch early cuts; S01 is
  exempt. No unreachable branch found; no contradictory routing found;
  fallback S60 is correct.

- SS3 TradeProposal traceability — `execution_id` (EXEC-YYYYMMDD-HHMMSS-xxxxxx)
  stamped once per evaluation in SignalPolicy.evaluate_probabilities (policy.py:125),
  carried on every emitted proposal (including NO_TRADE), threaded through the
  experience/intelligence/news gates, into dispatch_order log lines
  (order_manager.py:931/963/1033 `exec=` embed) and into audit_orders.execution_id
  (migration guard audit_repository.py:291-313). `/api/debug/trace/{execution_id}`
  joins audit_signals + audit_orders on the id. Unique per evaluation (uuid4 hex
  suffix); persists through retries (same request_id keeps its execution_id in
  the audit reason string) and state changes (autopsy rows carry it via
  log_order reason). Verified no execution becomes untraceable: every terminal
  non-fill path emits a terminal outcome keyed by request_id
  (terminal_outcome.py wiring table).

- SS4 Policy / confluence boundary — SignalPolicy is the single proposal
  producer; confidence gate (0.35 base + guardian-aware thresholds), SMC
  confluence matrix, Regime Guardian gate (fail-closed HIGH_SPREAD_CHOP /
  guardian veto) all run BEFORE any sizing/dispatch; the engine drops NO_TRADE
  proposals before any order authority is consulted (live_engine.py:4236+).
  Rejected proposals never reach dispatch: pre-trade gates can only downgrade
  to NO_TRADE; BUG-169 terminal outcomes are emitted for pre-dispatch
  rejections so the decision cannot hang.

- SS5 Risk boundary — RiskEngine authoritative before OrderManager.
  * HARD_MAX_LOTS=10.0 (order_manager.py:77) applied UNCONDITIONALLY in
    `_clamp_dispatch_volume` (769) after the RiskEngine tier clamp
    (get_clamped_position_size: equity tiers 0.02/0.10/1.00/10.0, volume_max cap).
    Zero/negative volume rejected before any broker call.
  * MAX_TOTAL_EXPOSURE=1 enforced engine-wide after BUG-240 (ad06738f):
    `_is_exposure_available` counts `count_total_exposure(symbol=None)`
    (order_manager.py:753-767) — a position on ANY symbol blocks a second
    dispatch. Paper probe: XAUUSD open -> second dispatch blocked.
  * free-margin <= 20% enforced inside calculate_dynamic_volume step 7
    (risk_engine.py:203-212: `(margin_free*0.2*leverage)/(contract*entry)`)
    AND re-checked by evaluate_proposal guards; BUG-239 (152e8ebe) closed the
    micro-account resurrection hole (margin-zeroed volume can never be
    resurrected by the min-lot exception; fail-closed None).
  * 30s pending re-quote lock: manage_pending_orders (order_manager.py:3916
    `age <= PENDING_ORDER_LOCK_SECONDS(30.0)` continue) + drift gate
    (:3926 `dist < required_drift(1.0*ATR)` continue). Cancel/re-quote can
    only fire after BOTH the 30s lock expiry AND >= 1.0 ATR drift.
  * 3 rejections -> SAFE_MODE: see SS14 (BUG-241 fixed the primary path).
  Combination behavior verified: exposure gate sits BEFORE the clamp in
  dispatch_order (fail order: SAFE_MODE -> duplicate guard -> exposure ->
  clamp -> context staging -> broker), so no ordering path lets a later
  constraint be bypassed by an earlier pass.

- SS6 Position state machine — 11 explicit PositionState values
  (execution/position_states.py). transition_with_hysteresis
  (position_state_machine.py): first observation seeds a SAFE neutral state
  (PROFIT_UNPROTECTED for profit-side targets else LOSS_RECOVERY_CANDIDATE)
  EXCEPT the emergency bypass set {LOSS_HARD_EXIT, PROFIT_GIVEBACK_CRITICAL}
  which honors zero-latency transition even on first sight (restart with an
  already-exhausted budget must be honored immediately). Same-state observation
  cancels any staged candidate; normal transitions require BOTH
  min_confirmation_duration (2.5s default, window never resets) AND
  min_observation_count (10 default) — a tick burst cannot confirm instantly
  (probe: 3 sightings at +3s still NOT confirmed). Emergency bypass cannot
  create a second authority path: it only accelerates a state the router was
  already going to request; the broker action still goes through the single
  dispatch chain. Invalid transitions are unrepresentable (candidate filtered
  deterministically); terminal tickets are dropped from both dicts at teardown.

- SS7 Protection ledger — per-ticket dataclass
  (peak_win_usd, was_sl_modified, profit_giveback_triggered, close_requested,
  telemetry/BE-failure/BE-attempt timestamps, breakeven_sl_price) bound to the
  MT5 ticket (never shared). update_peak: monotonic `max(peak, pnl)`;
  TypeError/ValueError/NaN/Inf leave the peak unchanged (guards verified by
  probe). retention_ratio returns 1.0 when peak<=0 (cannot mis-fire giveback
  on an unarmed position) and guards division. Lifecycle: entries live for the
  manager lifetime bounded by open tickets; deliberately NOT in the per-ticket
  cleanup bundle (module docstring documents this as intended). Duplicate
  ticket: `get()` returns the SAME per-ticket object (no cross-contamination).
  Closed ticket: TASK-7 `_is_closed_ticket` invariant blocks protective
  modifications on positively-closed tickets.

- SS8 Atomic per-ticket teardown — `_cleanup_ticket_state` (order_manager.py:6189+)
  is ONE synchronous method that releases: partial-close flag, MFE/MAE +
  excursion timings, execution-quality evidence (expected price/ATR/spread/
  latency/timestamps), tick/duration/peak trackers, LSF state, hold-score
  trackers, rescue registry, modify trackers, entry context (price/SL/TP/
  volume/risk/direction), pending setup times, ledger autopsy context
  (reasons/confidences/regimes/order ids), SL-modified flags, forced exit
  mechanisms, reversal evidence, net PnL/exit mechanism, trajectory history,
  closed-tickets tombstone, exit-pending reason, then RecoveryBudgetLedger.
  drop_ticket (all six dicts), PositionStateMachine.drop_ticket (state +
  candidate), and TicketsCache.pop_ticket under `_live_tickets_lock`.
  Called exactly once per dead ticket from `_sweep_dead_tickets` after the
  autopsy row is written. Dict pops cannot raise for present/absent int keys,
  so a partial teardown is not reachable; failure-injection during the pops
  cannot strand half a ticket (single thread, no awaits inside). Verified
  protection ledger is intentionally excluded (documented design).

- SS9 Recovery budget — RecoveryBudgetLedger: per-ticket isolation (six dicts
  keyed by ticket; allocation is idempotent no-op I1), budget = min(initial_risk
  * recovery_budget_pct_of_r, remaining risk to entry) with ATR*1.5 fallback
  when initial risk is unavailable, consumption = max(0, |loss| - initial_loss),
  remaining never negative (I2), exhaustion is a pure recompute from immutable
  entries (I4: caller closes on the exhausted verdict — failed recovery never
  silently becomes success). Horizon: dynamic (default 180s scaled by
  1.5/ATR, confidence_factor+0.5, adverse-trend 0.70x) clamped to
  [min_recovery_horizon_sec=30, max_recovery_horizon_sec=600] from AlgoConfig
  (config.py:107-110; pydantic-validated). Retry limits: re-allocation is a
  no-op; the caller closes at exhaustion -> no infinite recovery loops.
  NOTE: the brief's "horizon clamp 30600 seconds" does NOT exist anywhere in
  HEAD code (30600 appears in zero source files) — the real, shipped clamp is
  the 600s AlgoConfig max. Recorded as a spec phantom, not a defect.

- SS10 Protective exits — priority chain (deterministic, verified in
  _run_protection_chain 5130+): (0) AI direction-flip fast reversal
  (whipsaw-guarded, closes THEN places the reversal stop via the clamped
  path), (1) close_requested/_closed_tickets guard (no duplicate close on a
  dying ticket), (2) profit giveback (negative-after-peak > tiered retention
  floor breach; VOLATILITY_EXPANSION + already-locked BE suppresses the market
  close in favor of the locked SL + dynamic tighten — never crosses the spread
  to destroy protected profit), (3) breakeven lock (BUG-086: never re-issues
  once confirmed), (4) MFE giveback trailing (locks 70% of peak >= $150,
  monotonic is_sl_improvement floor), (5) ATR trailing. Conflicting conditions
  resolve by priority; a lower-priority mechanism can never override a
  higher-priority verdict (continue on activation). Duplicate/contradictory
  exit orders are prevented by close_requested + _closed_tickets + the
  broker-verified close ordering (BUG-087).

- SS11 Broker truth / reconciliation — INV-011 enforced. refresh_live_tickets_cache
  rebuilds the cache from the BROKER view (positions + pendings); pendings
  present on the broker are added, absent ones dropped, with the BUG-140
  terminal-outcome sweep for vanished pendings (CANCELED/EXPIRED/REJECTED
  classified from _pending_cancel_reasons). reconcile_pending_state compares
  internal vs broker pending counts and repairs on mismatch (broker wins,
  broker_error isolated). reconcile_missed_closes (Phase 14, BUG-045/090)
  discovers broker-closed tickets the internal state never tracked, restores
  entry context from the ledger OPENED row (ORIGINAL_REQUEST provenance), and
  routes them through the same autopsy + experience outcome path — it never
  fabricates broker state (evidence comes only from get_closed_deals_history
  deals; classification via classify_exit_with_evidence on real deal fields).
  Monotonic 60s fetch gate prevents per-tick history storms.

- SS12 Idempotency — INV-005/006. dispatch_order: every request_id SENT to the
  broker once (filled or refused) is terminal via `_processed_orders`
  (duplicate -> warning + False, no broker call). execute_order: same guard on
  order.order_id. Fill-family: `_context_bound_tickets` idempotent family
  binding (BUG-081) so split fills never duplicate contexts; `_reconcile_seen`
  prevents duplicate reconciliation outcomes; `record_terminal_outcome` is
  ledger-idempotent on the idempotency key. Duplicate tick: BUG-169 dedup at
  the policy layer (duplicate ticks re-surface the last real proposal instead
  of fabricating new ones). Concurrent dispatch requests: dispatch runs on the
  single loop thread (sequential by construction); the exposure cache gate is
  lock-protected. Reconnect replay: reconciliation is dedup-guarded. One
  logical order cannot become multiple broker orders.

- SS13 Re-quote / price drift — 30s lock: exact boundary semantics verified
  (age <= 30.0 -> locked/untouchable; strictly greater -> eligible), drift
  gate: dist < 1.0*ATR -> held. manage_pending_orders re-derives drift per
  tick against the LIVE quote (stale orders cannot be dispatched unexpectedly:
  cancel/re-quote requires the fresh tick to breach BOTH gates).
  should_modify_pending_order (compat surface, tests-only callers) mirrors the
  same two gates on its own clock — dead in production, documented in the
  handoff as a residual (no drift risk since no caller).

- SS14 Circuit breaker / SAFE_MODE — post-BUG-241 semantics: dispatch_order
  checks `global_state == "SAFE_MODE"` FIRST (blocked + NOT_DISPATCHED
  terminal outcome + [ENTRY_BLOCKED] layer=SAFE_MODE log), feeds
  `_consecutive_failures` on BOTH market and pending broker refusals
  (ticket==0), transitions to SAFE_MODE at 3 (logger.critical), resets to 0 on
  success. execute_order (hedge) retains its original identical breaker.
  SAFE_MODE cannot be bypassed by another route: there IS no other dispatch
  route (SS1). Persistence: global_state is in-memory by design (a restart
  clears the breaker; reconnect behavior unchanged); rejection counting is
  dedup-safe because the duplicate guard precedes the broker call (a blocked
  duplicate never counts). Probe-verified: 3 refusals -> SAFE_MODE, 4th
  blocked; success after 2 refusals resets the counter.

- SS15 Execution adapter semantics — IMT5Port (ports/mt5_port.py) defines the
  mandatory surface; DirectMT5Adapter (Win32 IPC), RemoteMT5GatewayAdapter
  (HTTP/JSON-RPC bridge), PaperMT5Adapter (simulation) all implement it.
  execute_market_order/place_pending_order return (ticket>0 | 0) with
  identical meaning (0 = refused); send_order returns bool; close/modify
  return bool. MT5 adapter adds broker-truth hardening: ambiguous-fill
  recovery (non-DONE retcode + live position found -> treated as success,
  never blind-retried), pending-order idempotency guard
  (_find_equivalent_pending fingerprint match on retry), cause-aware
  pending recovery (BUG-231: corrected retcode map 10016=INVALID_STOPS etc.,
  TRANSIENT/REPAIRABLE/HARD_REJECT classifier, pre-dispatch validator,
  ONE RR-preserving repair then abort). Paper implements the full port with
  immediate simulated fills (deterministic, seeded per BUG-232). Semantic
  divergence found: NONE that breaks the contract (paper fill-on-accept for
  pendings is documented simulation behavior, not divergence).

- SS16 Partial fills / rejections / errors — full fill: ticket>0 -> audit
  order row + entry-context staging on first sight -> ledger OPENED.
  Partial close: close_position(ticket, volume) via PARTIAL_CLOSE/
  PARTIAL_CLOSE plan branch, _partial_closed_tickets prevents duplicate
  partials, external volume shrink detected by _sync_external_modifications
  (BUG-045: realized PnL computed + notifier, entry SL preserved). Zero
  fill/refusal: ticket==0 -> REJECTED_UNFILLED terminal outcome + breaker
  tick + False. Timeout/malformed: adapter-level None/retcode handling;
  ambiguous responses resolved by broker-truth probes (never assumed).
  Cancellation: broker-verified (orders_get + history_orders_get) before the
  slot is released (BUG-072/073). No "successful" state after a failed broker
  operation: success flags are set only on confirmed broker success
  (`close_requested` only after close_position truthy; `_last_modify_sl`
  only after modify truthy — BUG-085).

- SS17 Exit classification — EXIT_CLASSIFICATION v3
  (experience/outcome_recovery.py classify_exit_with_evidence): evidence
  sources ENGINE_FORCED / BROKER_DEAL_REASON / BROKER_DEAL_COMMENT /
  SL_GEOMETRY / TP_GEOMETRY / FALLBACK_HEURISTIC with confidence 0.2..1.0.
  reason=4 (DEAL_REASON_SL) and 6 (SO) classify via _classify_sl_geometry
  (RISK_FREE vs HARD_SL by was_sl_modified/BE proof — BUG-081/083 rule);
  reason=5 TP with geometry/comment corroboration; reason 1/2 client ->
  MANUAL_CLOSE; reason=0 with NO corroborating evidence stays UNKNOWN
  (INV-012 — never promoted to MANUAL); reason=3 EA close resolved by
  comment/geometry, defaulting SYSTEM_CLOSE. UNKNOWN remains UNKNOWN;
  classification never alters historical truth (the autopsy persists the raw
  deal + evidence alongside the label; DEC-0021 honored).

- SS18 Execution trace / forensics — [EXEC_TRACE] log line (policy.py:1171)
  carries execution_id, request_id, action, stage, blocked_by, reason,
  conf_before/after, regime — one line per evaluation under the telemetry
  throttle. /api/debug/trace/{execution_id} joins audit_signals (execution_id
  column) + audit_orders (reason LIKE) and returns the full decision chain.
  audit_orders carries execution_id since the guarded migration
  (audit_repository.py:306-313). No secrets in the trace payload (structured
  decision fields only).

- SS19 Accounting consistency — execution -> position state -> TradeOutcome
  -> accounting ledger -> autopsy: log_order rows written only on confirmed
  broker outcomes; log_ledger_opened at first sighting with provenance
  (account_source read live from the adapter — BUG-226); autopsy row written
  exactly once per ticket (single data-rich row; reconciliation dedup);
  TradeOutcome/experience rows are idempotent on the idempotency key; ledger
  history is immutable (INV-007). A failed execution produces
  REJECTED_UNFILLED/NOT_DISPATCHED/EXECUTION_FAILED terminal outcomes with
  is_executed=False — never a successful ledger result; a successful
  execution cannot disappear (opened row + autopsy + experience chain).

- SS20 PAPER / SHADOW / LIVE — launcher resolves the EFFECTIVE mode before
  adapter binding (BUG-232: explicit --mode > settings DB > YAML) and binds
  the matching adapter; align_adapter_to_boot_mode mirrors the guard (LIVE
  boot with a paper adapter is replaced BEFORE the first tick). PAPER cannot
  reach the real broker (PaperMT5Adapter is the hard simulation boundary,
  BUG-212 fix; injection endpoint is SIMULATION/PAPER-gated). SHADOW: the
  decision path downgrades every non-NO_TRADE action to a logged
  NO_TRADE observation (SHADOW_OBSERVATION_ONLY) BEFORE any order authority
  is consulted, and the hedge path skips the broker write with
  ORDER_MUTATION_SUPPRESSED (INV-014 verified). LIVE uses the real adapter.
  Mode cannot silently change during execution: set_execution_mode swaps the
  adapter synchronously on the loop thread (never mid-order) and
  _invalidate_cross_mode_state clears all staged paper state (BUG-232c);
  data_source watermark surfaces any mismatch in the UI.

- SS21 Concurrency / races — single-threaded decision core: dispatch and
  position management run on the loop thread (_process_tick_pipeline);
  asyncio.to_thread workers (news/training/accounting/research) never touch
  broker mutation surfaces (INV-002 + structural EXEC-2 guard). The only
  shared mutable surface (live tickets cache) is guarded by
  `_live_tickets_lock` with a deterministic lock order (cache lock never
  nests inside another lock); create_task sites (stop handler, retrain task)
  do not touch dispatch. Duplicate ticks: BUG-169 dedup (policy + regime
  classifier). Retries: adapter-level bounded retry with idempotency probes
  (pending fingerprint match; ambiguous-fill broker-truth check). Simultaneous
  close/open: reversal protocol closes FIRST and refuses to stack on failure.
  Double closes: close_requested + _closed_tickets + broker-verified close
  ordering. No lock inversion, no deadlock found.

- SS22 Safe failure — fault-injection review of every failure surface:
  broker unavailable -> adapter connect failure blocks dispatch (fail-closed;
  account-identity fail-safe BUG-142 shuts the adapter down on login
  mismatch); adapter exception -> try/except with isolated error logs, state
  unchanged; malformed broker result -> retcode classification, never a
  fabricated success; invalid proposal -> pydantic validation at the domain
  boundary (frozen models); risk rejection -> NOT_DISPATCHED terminal
  outcome; router failure -> S60 controlled HOLD (never an accidental close);
  state-machine failure -> candidate simply not confirmed (position stays in
  current safe state); protection failure -> close_position raised is caught,
  mechanism tag rolled back, retry next pass; accounting failure ->
  experience emission isolated (learning never disturbs execution). No
  silent success: every success flag requires a confirmed broker predicate.

- SS23 Real safe execution test — executed: the full pipeline was exercised
  with REAL TradeProposal objects through the REAL OrderLifecycleManager over
  PaperMT5Adapter (no real capital, deterministic): direction-correct
  BUY/SELL proposals, exposure blocking, idempotency blocking, breaker
  trips/resets, state-machine transitions, protection peak guards, recovery
  budget exhaustion + horizon clamp, teardown coverage, single-authority
  census. Now pinned as tests/unit/test_agent12_execution_forensic.py
  (18 tests, offline, CI-runnable).

- SS24 Development / fix — every confirmed defect in scope was already FIXED
  at HEAD by the parallel execution forensics and is now regression-pinned by
  this mission: BUG-239 (micro-account margin hole + UnboundLocalError,
  152e8ebe), BUG-240 (engine-wide exposure gate, ad06738f), BUG-241 (SAFE_MODE
  dead on the primary dispatch path, ad06738f), BUG-242 (web operator bypass,
  d0a9b6d4). No safety mechanism was weakened; no threshold changed.

- SS25 Testing — focused suites run green on this checkout: 
  test_agent12_execution_forensic.py (18/18 NEW),
  test_execution_architecture, test_order_lifecycle,
  test_qa_deep_execution_safety, test_s6_dispatch_parity_golden,
  test_s3_recovery_budget_golden, test_hardened_protocol,
  test_pending_recovery_cause_aware (35), test_outcome_recovery_sweep_bug140.
  FAIL-BEFORE/PASS-AFTER evidence for the pinned defects lives in the
  Agent-11 commits (ad06738f/d0a9b6d4 bodies: executable repros on paper
  fixtures). No real capital used anywhere.

- SS26 Final execution audit — simultaneously proven by the combination of
  the SS1-SS25 evidence + the new regression battery: ONE dispatch authority;
  RiskEngine authoritative before dispatch; HARD_MAX_LOTS / MAX_TOTAL_EXPOSURE
  / free-margin clamp never bypassed; re-quote lock + ATR drift enforced;
  SAFE_MODE enforced on all dispatch paths; 11-state machine coherent;
  protection state coherent; recovery bounded; broker truth wins; duplicate
  execution blocked; per-ticket teardown atomic; every order traceable by
  execution_id; accounting consistent with broker outcome.

## Verified findings (classification)

- DIRECT_EVIDENCE: single-authority census; clamp/lock/drift/breaker code at
  HEAD; teardown bundle contents; recovery ledger semantics; idempotency
  guards; adapter ambiguous-fill + pending-recovery hardening; BUG-239..242
  fixes present at HEAD (code + commit bodies + bugs.md rows).
- STRONG_INFERENCE: race-freedom (single-thread ownership + lock audit);
  no-deadlock (deterministic lock order, no nesting).
- NOT TESTED (deferred, registered as residuals): live MT5 smoke (safety
  contract — never during verification), malformed-network fault injection
  at the adapter transport layer (partially covered by BUG-231 tests),
  full-suite run (parallel swarm churn; focused gates green).

## Root causes (of the defects this mission closed out)

- BUG-239 ROOT_CAUSE_STATUS: PROVEN — slippage_usd bound only inside the
  impact loop + micro-exception resurrecting margin-refused volume.
- BUG-240 ROOT_CAUSE_STATUS: PROVEN — symbol-scoped count vs engine-wide
  contract.
- BUG-241 ROOT_CAUSE_STATUS: PROVEN — breaker existed only on the hedge path.
- BUG-242 ROOT_CAUSE_STATUS: PROVEN — web endpoints were the only broker
  mutation surface outside the manager.

## Residual risks / unresolved

1. `should_modify_pending_order` (order_manager.py:635) is production-dead
   (compat surface; two legacy test callers). The live 30s+drift gates live in
   manage_pending_orders with its own clock. Low risk (no caller), documented
   to prevent future misuse as a second gate.
2. Brief spec phantom: "horizon clamp 30600 seconds" has no realization in
   code (max_recovery_horizon_sec=600.0 is the shipped clamp). Recorded here
   and in the handoff; do NOT "fix" by inventing a 30600 constant.
3. Agent-11's dedicated regression file
   tests/unit/test_agent11_execution_risk_forensic.py (promised in its commit
   handoffs) had not landed on this checkout at handoff time — the
   test_agent12 battery covers the same invariants; coordinate via
   agents/locks.yaml before duplicating.
4. Live MT5 smoke not executed (safety contract). Offline verification only.
5. 30600-vs-600 spec correction should be appended to agents/bugs.md by the
   registry owner in a quiet window (append-only; not done here to avoid
   racing the parallel bugs.md writer).

## Files changed (this mission)

- tests/unit/test_agent12_execution_forensic.py (NEW, 18 tests, 238 lines)
- docs/agent_handoffs/2026-09-05_agent12_execution_forensic.md (this file)

## Verification commands

    .venv/Scripts/python.exe -m pytest tests/unit/test_agent12_execution_forensic.py -q
    # 18 passed

## Next-agent instructions

1. Run the battery above before touching execution surfaces; keep it green.
2. Do not duplicate the Agent-11 regression file; check locks.yaml first.
3. Append the 30600-vs-600 spec note to agents/bugs.md in a quiet window.
4. If adding a dispatch path, update the single-authority census test
   (TestAgent12SingleAuthority) — it must stay empty of violations.
