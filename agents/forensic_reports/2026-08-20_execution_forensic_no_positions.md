# Trading Execution Forensic Report — "Bot Does Not Open Positions"

Audit date: 2026-08-20 (IST, UTC+3:30) · Agent: Hermes-Forensic-ExecAudit
Branch: main · Audit window: 2026-08-19T18:41Z → 2026-08-20T00:00Z (engine LIVE session 22:11 IST → 02:59 IST)

## Executive summary

The bot IS alive and the full pipeline (market data → features → model → policy →
risk → dispatch) runs. **No position was opened because every evaluation is
converted to NO_TRADE by an honest stack of entry filters**, and — separately —
the engine process EXITED at ~03:00 IST during this audit (log frozen at
02:59:56, PID 13380 gone, API port 8080 dead, audit_signals last row
23:29:00Z). The last real broker trade was a SELL position ticket
152508395848, opened 02:20:50 IST and closed 02:22:07 IST (-$5.44,
HOLD_SCORE_DECAY). Since that close, 0 orders dispatched.

## Phase-by-phase findings

| Phase | Component | Status | Evidence |
|---|---|---|---|
| 1 | Process/workers | PASS (then DOWN at 03:00) | workers RUNNING cycles 68-79; engine PID 13380 alive at 02:59, gone at 03:25; port 8080 closed |
| 2 | Market data | PASS | tick_age 1.78s, LIVE/CONNECTED, spread $0.31-0.33, bars loaded 900 M1 |
| 3 | Features | PASS (50D contract) | live vector 50, model 50, classes 4 normalised |
| 4 | Liquidity | PASS (info-only) | 10 features computed, `LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE` informational not a trade block |
| 5 | Strategy signal | PASS | every eval produces a proposal (228 radar lines), all NO_TRADE |
| 6 | AI model | PASS | inference 0.78ms, probs produced per tick |
| 7 | Rules | PASS | rule matrix inert (30 rules disabled in DB); built-in policy gates do the filtering |
| 8 | Confidence | **BLOCK** | RAW 0.22-0.33 → EXPERIENCE 0.0 → FINAL 0.0; effective gate 0.35 in RANGING |
| 9 | Risk | PASS | account balance 33,305.07, free margin 33,305.07, no risk rejection observed |
| 10 | Order creation | **BLOCK** | dispatch_order called once (02:20:50) in session; 0 after |
| 11 | Broker | PASS | MT5 CONNECTED, trade_allowed=True, Fast-Act pending placed OK on attempt 1 |
| 12 | Logs | PASS | no ERROR/EXCEPTION/MT5_ERROR in 10,135 lines; 6,742 REJECT-class lines |

## The funnel (24h, audit_signals)

- 1,191 NO_TRADE / 1,297 signals (92%) — top reasons:
  - REGIME_RANGING_MEAN_REVERSION 188 (standard eval, regime filter)
  - ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD 84 (min R:R 1.8)
  - INSUFFICIENT_CONFIDENCE 0.24-0.34 < **0.35** (0.25 base + 0.10 range penalty)
  - PREDICTIVE_OB_BUY/SELL_LIMIT_EQUILIBRIUM 52 (predictive-limit candidates)
- 85 directional signals (BUY_LIMIT/SELL_LIMIT/BUY_MARKET): ALL converted to NO_TRADE by
  EXPERIENCE_INTELLIGENCE_GATE (DEGRADED_CONFIDENCE_BELOW_THRESHOLD 0.37-0.39 < 0.40 floor,
  after 0.70 degraded multiplier) or TRADE/PREDICTIVE gates.
- A BUY_LIMIT at confidence 0.464 with risk_allowed=true reached FINAL_DECISION
  (03:24:46Z 08-19) with no blocker, but **no order was dispatched from it** — the
  record predates the current session; within the audited session the only dispatch
  was the 02:20 SELL_LIMIT.

## Root cause

**FIRST BLOCKING POINT: `SignalPolicy.evaluate_probabilities` (src/nexus_scalp/signals/policy.py)**

Chain: model prob (0.22-0.33) → effective confidence gate (0.35 in RANGING) rejects
~62% of candidates → regime filter (RANGING_MEAN_REVERSION) rejects ~30% → remaining
directional candidates hit the experience gate: all strategy families are
DEGRADED (win_rate 0.24-0.31, expectancy_r -0.17..-0.26, replay_validated=False)
or RETIRED → confidence × 0.70 < 0.40 floor → NO_TRADE.

Classification: **E) STRATEGY FILTER (+ D) CONFIDENCE BLOCK** — the gates are working
as designed; they are stacked high relative to a weak model and a losing experience
history (the 2026-08-18 $-4.7k losing regime poisoned the strategy registry).

Additional finding: engine process exited ~03:00 IST (maintenance window?) — no
crash marker in the log.

## Fix applied (Phase 13/15)

No safety gates were lowered. Added a **single-trace-id observability layer**:

1. `TradeProposal.execution_id` (optional field, default None) — EXEC-YYYYMMDD-HHMMSS-xxxxxx
2. `SignalPolicy.evaluate_probabilities` stamps ONE id per evaluation before any gate;
   carried into every proposal (NO_TRADE, dedup, guardian, sweep, predictive-limit, final).
3. `[EXEC_TRACE]` structlog line at every finalized decision carrying
   execution_id + action + stage + blocked_by + reason + conf_before/after + regime.
4. `dispatch_order` embeds `| exec=<id>` into audit_orders.reason (both market and
   pending paths) so the trace joins audit_signals → audit_orders → broker ticket.
5. New read-only endpoint `GET /api/debug/trace/{execution_id}` — joins audit_signals
   + audit_orders for one EXEC id (never mutates).

Tests: `test_execution_id_stamped_on_no_trade_confidence_block`,
`test_execution_id_unique_across_evaluations`,
`test_execution_id_stamped_on_actionable_proposal` (test_policy.py);
`test_trade_proposal_execution_id_default_none` (test_domain_models.py).
Verified: 12 policy/domain tests + 38 debug-snapshot tests pass; live probe printed
`[EXEC_TRACE] execution_id=EXEC-20260820-002033-d783c9 action=NO_TRADE
blocked_by=ASYMMETRIC_RR_LIMIT stage=STANDARD_EVAL`.

## Next steps (recommended, NOT done)

1. Restart the engine (command in report): `python -m nexus_scalp.cli.main run --mode LIVE`
   from repo root — the process exited; nothing trades while it is down.
2. Investigate the ~03:00 exit (maintenance window / scheduler / watchdog).
3. If the user wants MORE trades: improve/retrain the model or reset the poisoned
   strategy registry — do NOT lower the confidence gates (that returns the
   $4.7k-loss regime per trading-performance-forensics).
4. Watch the new EXEC_TRACE lines to attribute every future rejection precisely.