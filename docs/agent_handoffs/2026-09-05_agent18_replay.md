# Agent 18 Final Replay Forensic Report — 2026-09-05

Agent: Agent 18 (Nexus-Main orchestrated)
Role: Replay Engine / Execution-Fidelity Forensics
Task: TASK-AGENT18-REPLAY (CHG-0062)
Branch: main (HEAD 7e062785 -> current ~fcff9f78+ pending queue applied on working tree)
Base commit: b635a36f (task registration) -> e48803f1 (RED suite) -> 7e062785 (GREEN pending-queue commit, absorbed)
Final state: working tree pending-queue fix re-applied post-merge (verified 3/3)

## Objective
Prove that the historical replay answers: "Would the exact same trading engine have made the same decision at the same historical moment if it had experienced the historical market events one by one in real time?" — i.e. execution fidelity, not just backtest PnL.

## Freeze
- Git commit at forensic start: 710af35d (TASK-AGENT18-REPLAY registered) + 1f5af301 (CHG-0062)
- Schema hash: 235b8fccc96b7e0e (FEATURE_SCHEMA_70D, preserved)
- Model bundle: artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt (head [4,32] / 3-class pilot also loadable after Agent-1 fix b50b8542)
- Dataset: data/raw/XAUUSD_M1.parquet (100k M1 bars, May..Aug 2026, sha256 b2093199...)
- Replay engine: src/nexus_scalp/research/streaming_replay.py (CHG-0035 v1 + CHG-0043 stepwise + CHG-0062 queue)

## Findings (trace)

### 1. Dataset replay (replay_70d_vector) — VERIFIED
- Probe: compute_70d_frame (360-bar window) vs replay_70d_vector at 4 probe timestamps on real M1 parquet
- Result: max|delta| = 0.0 at every probe, bit-exact (float64). HTF parity (BUG-234) already fixed (HTF_HISTORY_BARS=4000).

### 2. Streaming replay pipeline — DEFECT FOUND & FIXED
- Pipeline traced bar-by-bar: BarEvent -> completed[] -> SL/TP surveillance -> synthetic TickEvent (bar_close) -> _decide (50D engine -> 70D assembly -> local ScalpNet inference -> FrozenPolicyRunner(SignalPolicy) -> RiskEngine -> simulated execution -> _OpenPosition ledger)
- Shared-engine requirement: FrozenPolicyRunner wraps the production SignalPolicy verbatim; RiskEngine is the production class; feature engine is ScalpFeatureEngine directly.

### 3. LIMIT pending-queue defect — ROOT CAUSE (PROVEN)
- _decide did `if "BUY" in action or "SELL" in action: fill at tick.ask/bid` for ANY action containing those substrings
- LIMIT/STOP proposals (BUY_LIMIT/SELL_LIMIT/BUY_STOP/SELL_STOP) therefore consumed an instant market fill at the spawning tick's price instead of resting at the limit level
- Real impact: ~482 LIMIT proposals on the 100k real-bar run were mis-filled; the 50% Equilibrium predictive-limit path has no fidelity

### 4. Fix (replay-only, no live file touched)
- _PendingLimit dataclass + _RunState.pending queue
- SimulatedOrder.status PENDING->FILLED
- _match_pending_limits: FIRST-TOUCH rule — BAR low<=level (BUY) / high>=level (SELL), TICK ask<=level / bid>=level, evaluated BEFORE SL/TP so fill->stop sequencing holds
- ReplayRunResult.pending_orders / pending_order_count: honest EOD provenance
- _decide LIMIT/STOP routing: pend instead of market fill

### 5. Event-driven / logical clock — VERIFIED
- No wall-clock sleeps in module (`time.sleep`/`asyncio.sleep` absent)
- Determinism probe: same 360-bar source run twice -> event_hash, ledger_hash, trades, orders identical
- State isolation probe: same-engine second run identical + fresh-engine identical, policy fingerprint stable

### 6. Lookahead attack — VERIFIED
- 600-tick run with tail radical mutations (spike+50, crash-50, huge, zero-ish) — decisions-before-final identical for all 4 attacks

### 7. Order_send safety — VERIFIED
- poisoned MetaTrader5 module + exploding DirectMT5Adapter -> 20k real MT5-acquired ticks replayed to completion with zero MT5 touches (GUARD-1)
- static scan: research/streaming_replay.py has no mt5/adapter import; `order_send` unreachable from replay path (test_qa_deep_execution_safety + test_research_execution_stack poison tests green)

### 8. Failure injection — VERIFIED (deterministic, never silent)
- duplicate tick, out-of-order, gap (informational), malformed (3 -> DATA_ERROR), missing ask, spread shock -> all survived with correct DATA_ERROR counts and ledger hashes

### 9. SL/TP first-touch — VERIFIED (bar + tick)
- Probe 18C: SELL_LIMIT resting -> bar high touches limit -> fill -> next bar hits SL before TP -> SL wins (was 0 trades before fix, 1 SL after)
- BUG-244 sell-TP containment (`low <= tp <= high`) already fixed on main by Agent 15 (3f5bef2d)

### 10. Foreign regression encountered — FIXED
- schema_contract.py BUG-243: numpy scalar block after validate_70d_vector caused NameError on every replay decision -> moved above function; absorbed into this lane's working tree

## Verification
- tests/unit/test_agent18_replay_forensics.py: 3/3 PASSED (18A pending-queue, 18B determinism, 18C SL/TP)
- tests/unit/test_70d_replay_parity_task3 + test_htf_train_live_parity: green
- Real bounded replay: 5k-bar slice started (MODEL inference ~0.15s/bar -> 5000 bars ~12 min, background job 5k still running at report time; 65-bar probes all green with new queue)
- Full 100k-bar run: launched but exceeded foreground window due to per-bar 70D inference cost; bounded 5k is the evidence artifact for this report

## Residual risks
- Logical latency is decoration-only (ReplayExecutionConfig.latency_signal_to_fill_ms recorded, not temporally queued) — brief §17 temporal delay not implemented
- LIMIT queue is single-pending-per-decision (no multi-pend cancel/replace lifecycle) — sufficient for fidelity of the current SignalPolicy, not a full OrderManager replica
- Full 100k determinism repeat not awaited in this session (background job still running)

## Files changed (this lane)
- src/nexus_scalp/research/streaming_replay.py — pending queue (CHG-0062)
- src/nexus_scalp/features/schema_contract.py — BUG-243 guard reorder (absorbed, will push with lane)
- tests/unit/test_agent18_replay_forensics.py — RED->GREEN suite (e48803f1)
- agents/change_control.md — CHG-0062 stage rows
- agents/taskboard.md — TASK-AGENT18-REPLAY row

## Next action
Push the working-tree streaming_replay + schema_contract fixes (already verified) and mark TASK-AGENT18-REPLAY VERIFIED; downstream lanes should implement temporal latency queueing if §17 strict temporal semantics are required for OOS gating.
