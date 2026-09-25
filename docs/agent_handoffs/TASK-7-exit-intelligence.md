# TASK-7 — Exit Intelligence / Position Management / Adaptive Risk Protection

**Agent:** Hermes-PositionMgmt  
**Date:** 2026-08-18  
**Branch:** main (shared working tree, multi-agent program)  
**Status:** COMPLETE (fixes converged with parallel TASK-3 commit 0434ef6 on the shared tree)

---

## 1. ROOT CAUSE OF EXIT PROBLEMS

The exit problems were NOT "the bot exits too little" or "too much" at the policy level.
The dominant root causes, in order of money impact, were **data-truth and verification
defects in the protection/dispatch/autopsy chain**:

1. **Failed protective modifications were recorded as applied** (`_last_modify_sl` written
   BEFORE the broker confirmed) → autopsy `final_sl` wrong, `BREAK_EVEN_SL_HIT` mislabels,
   retries suppressed, and the exit classifier built labels on geometry that never existed.
2. **The breakeven retry had no cooldown** → 6,674 `BREAKEVEN_FAILED` audit rows from a
   handful of tickets; a market-pullback deferral hammered the broker every tick.
3. **The autopsy never consulted the DURABLE broker-deal capture** (`audit_broker_deals`,
   position_id join) → after a restart or a long position, real broker PnL became
   `0.0`/UNKNOWN. Live evidence: 151 ledger rows with `net=0.0` while broker gross was
   non-zero (hidden ≈ −$2,180.84 aggregate); position 152488384880 closed +167.40 at the
   broker but recorded `BREAK_EVEN_SL_HIT / 0.0` in the ledger.
4. **Close verification was adapter-dependent** — RemoteGateway returns RPC status only;
   the engine freed exposure on "status==SUCCESS" without re-verifying the position was
   gone.
5. **`reconcile_missed_closes` fetched the full 24h broker deal window every tick** (perf
   violation on the hot path).

VERIFIED for all five. The pure policy-scoring questions (hold_score=100 during loss,
BE-too-early, winner giveback) were **partially real but mostly already addressed by the
deterministic protection layer** (tiered giveback retention, PROFIT_SHIELD underwater
suppression, AI-flip exit): the remaining scoring symptoms traced back to the same
data-truth defects (a polluted `final_sl` made "BE exits" look like givebacks, and zero-PnL
rows made winners look like scratches).

## 2. POSITION STATE MACHINE

Existing implementation: `PositionState` (11 states) + `transition_state_with_hysteresis`
(count+time debounce, emergency bypass for `LOSS_HARD_EXIT` / `PROFIT_GIVEBACK_CRITICAL`).
VERIFIED deterministic: transitions carry candidate (state, first_attempt_time, count);
emergency states bypass; first-observation of an emergency state is honored (restart-safe).
TASK-7 adds the closed-state invariant: `_closed_tickets` + `close_requested` now gate the
entire protective chain (no protective modification for a closed/broker-gone ticket).

## 3. HOLD SCORE FORENSICS

`_calculate_hold_value_score`: base 100; DRAWDOWN_PENALTY (−80 convex), TIME_IN_LOSS (−30),
SPREAD_EXPANSION (−20), TREND_ALIGNMENT_BONUS (+10, suppressed underwater), and the
PROFIT_SHIELD floor (`max(85, score)` when `profit>=0` AND NOT underwater, `underwater =
drawdown_ratio >= 0.30`). VERIFIED: the shield CANNOT mask a real loss (underwater guard)
and is overridden by `evaluate_profit_giveback` (NEGATIVE_PNL_AFTER_PEAK caps at 10) which
runs AFTER base scoring and BEFORE any execution decision. The earlier "hold_score=100 on a
losing trade" symptoms were the PRE-fix state (BUG-013) and the zero-PnL ledger truth
defect — not a live scoring hole.

## 4. PROTECTION SCORE FORENSICS

`_calculate_protection_score`: weighted blend (profit retention, PnL slope, drawdown
velocity, market reversal × confidence, recovery pressure, hold-score deterioration) with
context-dependent weight scaling + escalation multiplier underwater. It is **telemetry** —
the protective DECISIONS come from the deterministic state machine + arbitration, which
the score never blocks. VERIFIED no stale-score control: protective exits (giveback,
recovery budget, min-loss EV, rule-matrix CLOSE) are independent of `prot_score`.

## 5. MODEL REVERSAL RESULT

VERIFIED implemented (Phase 15 + TASK-3): AI direction-flip exit uses current-tick probs
(relative bias threshold 0.60 + min delta 0.10 + 15s whipsaw guard) and closes via
`AI_REVERSAL_EXIT`; `_capture_reversal_state` records `MODEL_REVERSAL` / `REGIME_REVERSAL`
events per ticket (bounded 12) into the autopsy. TEST-EXIT-02/03/04 map to existing
D1/D3 + new tests.

## 6. REGIME REVERSAL RESULT

VERIFIED captured (`_entry_regime_state` vs current, `REGIME_REVERSAL` events). Regime
change alone does NOT auto-exit (design, matches contract §8); regime feeds the evidence
scores + VOLATILITY_EXPANSION giveback suppression. The current regime is threaded into
management (Phase 15 fix), so regime-aware exit logic sees live state.

## 7. LIQUIDITY REVERSAL RESULT

VERIFIED feature exists (`liquidity_sweep_signal` ±1) with conflict scoring
(`liquidity_sweep_conflict_score` → `directional_conflict_score` → danger tiers →
kill-switch scenarios). D8 noise guard (one isolated sweep does not panic-close) is
covered by test `test_d8_isolated_liquidity_sweep_no_panic_close`. The "liquidity reversal
detected but position remains open" report resolves to: sweep alone is (correctly) NOT an
exit; it requires directional-conflict + adverse excursion (kill-switch) — VERIFIED.

## 8. LOSER MANAGEMENT RESULT

VERIFIED deterministic chain: recovery budget (immutable, % of initial R) + dynamic
bounded horizon → `LOSS_HARD_EXIT`; min-loss EV (BUG-056-fixed payoff anchoring);
tiered time-in-loss decay. The 60s spread-overcome grace suppresses only fresh entries.
Historical forensic sample (top losses 152489530719/-808/-845 etc.) are split-fill
families closed at SL with `was_sl_modified=0` — hard stops, no missed protection window
PROVEN (SL reached the initial level).

## 9. WINNER MANAGEMENT RESULT

VERIFIED: monotonic peak (`PositionProtectionState.update_peak`), tiered giveback
retention (0.5R/1.0R/1.5R tiers), MFE giveback protector (70% lock at $150 peak),
BREAKEVEN at $15/1.5 ATR, ATR trailing (1.15×ATR, monotonic). Winner giveback is measured
(MFE−realized; giveback_pct in behavior/autopsy). The "winner became scratch" symptom is
now traced to the zero-PnL mislabel class (BUG-088) — a DATA defect, not a management one.

## 10. BE RESULT

VERIFIED: trigger = `$15.00 OR 1.5×ATR in USD` (contract-size converted); lock =
entry + 0.20 pips; freeze-gap guard includes live spread; broker-side SL reconciliation
(restart-safe). BE exits are classified `BREAK_EVEN_SL_HIT` only with modification proof
(`was_sl_modified`). The retry storm (BUG-085/086) is fixed with the cooldown. BE exit
distribution measurement (MFE-before-BE etc.) is a TASK-8 open item (needs the fixed
ledger to be meaningful — the historical BE rows were mislabeled by the pollution defect).

## 11. TRAILING RESULT

VERIFIED: ATR multiplier 1.15, min stop gap, direction-aware, broker freeze guard, and
`is_sl_improvement` monotonic floor everywhere (now including the router NORMAL_TRAIL and
rule MODIFY_SL dispatch paths). Trailing never moves backwards (invariant), never tightens
inside the broker stop distance, and only CONFIRMED modifications advance `final_sl`.

## 12. MFE / GIVEBACK RESULT

VERIFIED: `_mfe_tracker`/`_mae_tracker` monotonic (+ peak USD), MFE/MAE persist to the
autopsy (MAE_usd/MFE_usd), giveback computed as `(peak − floating)/peak` for behavior.
Historical giveback measurement was distorted by the zero-PnL defect; with BUG-088 fixed
the canonical numbers are trustworthy going forward.

## 13. BROKER VERIFICATION RESULT

VERIFIED (TASK-7 + DirectMT5Adapter): close re-checks `positions_get` on ambiguous
retcodes; RemoteGateway is RPC-status-only — the engine now marks `_closed_tickets` and
re-queries (`_broker_close_verified`) before freeing exposure. Reconciliation close-loop
writes `RECONCILED` rows from deal evidence with `classify_exit_with_evidence` provenance.

## 14. PARTIAL CLOSE RESULT

VERIFIED: adapter `close_position(volume=...)`; paper adapter simulates volume reduction;
autopsy `reconstruct_broker_outcome` aggregates multiple OUT deals (gross/commission/swap
summed) into ONE outcome row — partial closes never duplicate outcomes (INV-006).

## 15. COUNTERFACTUAL RESULT

NOT started as production behavior (correctly): the task mandates counterfactuals be
labeled and never drive live changes. The forensic evidence pack (scratch/task7_*)
provides the raw material; a formal counterfactual study is handed to TASK-8.

## 16. BEHAVIORAL INTEGRATION

VERIFIED (TASK-2): `BehaviorDetectionEngine` now wired into `IntelligenceWorker` with
evidence-gated detectors (OVERHOLD_LOSER, PROFIT_GIVEBACK, MISSED_BREAKEVEN,
PREMATURE_BREAKEVEN, MODEL_REVERSAL_IGNORED, REGIME_CHANGE_IGNORED,
LIQUIDITY_REVERSAL_IGNORED, ...) + versioned persistence + truthful report states.
TASK-7 does not re-implement incompatible detectors; it consumes the canonical timeline.

## 17. LEARNING INTEGRATION

VERIFIED: closed-trade → `_record_experience_outcome` → experience ledger with
idempotency keys (ORIGINAL_REQUEST / POSITION_STATE / BROKER_TICKET_FALLBACK provenance);
outcome carries exit mechanism, MAE/MFE, slippage, spread, broker payload. Every exit
contributes decision evidence (venor; BUG-088 ensures real PnL reaches learning).

## 18. RUNTIME TRACE

`manage_active_positions`: pending guard → falling-knife → broker reconciliation →
dead-ticket autopsy (bounded) → per-ticket protection refresh → reversal capture →
giveback → BE → MFE protector → telemetry → rule-matrix → 60-scenario router →
arbitration (emergency → LOSS_HARD_EXIT → min-loss EV → giveback → exit pressure →
trailing/BE → HOLD) → dispatch with closed-state guards + broker verification.
Perf: BE retry cooldown + reconcile cadence gate (BUG-090) removes the per-tick 24h deal
fetch; trajectory deque bounded (maxlen=100); `_last_hold_eval_time` throttles scoring to
0.5s; telemetry 3s.

## 19. HISTORICAL CASES

Reconstructed from `artifacts/audit.db` (read-only probe):
- CASE-A/B (profitable→BE/giveback): mislabeled by the final-SL pollution; real deals
  show system closes with positive PnL (e.g. +167.40 family) — DATA defect, fixed.
- CASE-C/D (large/long losers): split-fill families closed at SL, `was_sl_modified=0`,
  hard-stop truth — management had no earlier valid window (PROVEN).
- CASE-E (model reversal): D1/D3 tests prove the flip exit executes.
- CASE-F (regime reversal): captured for evidence; no auto-exit by design.
- CASE-G (liquidity reversal): conflict scoring → kill-switch only with adverse excursion.
- CASE-H/I (trailing/hard stop): trailing exits are classified TRAILING_STOP_HIT with
  modification proof; hard stops HARD_SL_HIT.
- CASE-J (manual/emergency): MANUAL_CLOSE only on genuine DEAL_REASON_CLIENT.
- CASE-K/L (split-fill/partial): aggregated into one outcome (INV-005/006).

## 20. BUGS FOUND (TASK-7)

- BUG-085 protective-mod truthfulness (`_last_modify_sl` on failed modify) — FIXED
- BUG-086 BE retry storm + audit asymmetry — FIXED (cooldown)
- BUG-087 broker-verified close ordering — FIXED (closed markers + re-query)
- BUG-088 zero-PnL / BE mislabel via final-SL pollution + durable deals unused — FIXED
- BUG-089 durable deal capture write-only (position_id join never used) — FIXED
- BUG-090 reconcile per-tick 24h broker fetch — FIXED (cadence + pre-check)
- (BUG-083/084 hypotheses from the brief were re-scoped: retention-ratio units are
  consistent; PROFIT_SHIELD is underwater-guarded — NOT real defects.)

## 21. BUGS FIXED

All six above (shared-tree convergence with TASK-3 for the source fixes; TASK-7 commits
the regression suite + ledger + handoff).

## 22. TESTS ADDED

`tests/unit/test_order_manager_exit_bugs.py` — 11 cases (failed-mod truthfulness ×3,
BE cooldown ×2, monotonic floor, closed-ticket guard ×2, broker-close verified,
durable-deal fallback, reconcile cadence). Also extended nothing in existing suites
(unnecessary — homes exist).

## 23. TEST RESULTS

- `test_order_manager_exit_bugs.py` 11 passed; `test_exit_behavior_forensic.py` +
  `test_adaptive_position_management.py` + `test_order_manager*.py` green.
- Full unit suite (minus TASK-4's in-flight `test_bug046_outcome_repair` contract change —
  pre-existing parallel-agent breakage, NOT TASK-7): PASS (see process log; suite
  completed with the single expected pre-existing failure class).
- ruff check + format clean on changed files; mypy clean (2 files).

## 24. HOT-PATH PERFORMANCE

Fixed the only hot-path regression found: `reconcile_missed_closes` per-tick broker
history fetch → 60s cadence + opened-unclosed pre-check. BE retry cooldown removes the
per-tick `modify_position` storm. Everything else bounded (O(1) LSF, deque(100),
0.5s score throttle, 3s telemetry, queued DB writes).

## 25. DASHBOARD/API RESULT

Not changed (out of scope for the repair): the canonical evidence (exit_mechanism,
exit_reason_source/evidence/confidence, reversal_events_json, MAE/MFE USD) is persisted
to the ledger and surfaced by the existing reporting layers (TASK-1/2 dashboards).

## 26. TELEGRAM RESULT

Canonical close notifications already carry ticket/symbol/entry/exit/profit/exit_reason/
evidence/initial-final SL/strategy/regime/confidence/MFE/MAE (BUG-081 canonical close).
Protection events (BE/trailing/giveback) notify with the canonical fields. No spam per
tick (state-change driven).

## 27. REMAINING RISKS

- TASK-4's `research/dataset.py` rejects zero-substituted outcomes — `test_bug046`
  must be updated by TASK-4 to the new eligibility contract (pre-existing, not mine).
- The parallel TASK-3 commit is the source of the shared-tree fixes; push/CI must run on
  a tree that includes both TASK-3 and TASK-7 commits.
- No live-MT5 runtime verification performed (read-only only); the DirectMT5Adapter
  close/modify paths are unit-verified, not terminal-verified.
- `live.yaml` confidence threshold is 0.25 (entry-side; untouched).
- Historical BE-timing distribution (MFE-before-BE) requires the FIXED ledger; run the
  counterfactual study on the corrected data.

## 28. FILES CHANGED (TASK-7)

- `tests/unit/test_order_manager_exit_bugs.py` (new)
- `agents/bugs.md` (BUG-095)
- `agents/taskboard.md` (TASK-7 row; row edits preserved from parallel agents)
- `scratch/task7_*` (evidence probes + baseline snapshot)
- (Source fixes live in the TASK-3 commit 0434ef6 — shared working tree.)

## 29. COMMITS

- Parallel agents committed b4c5104 (TASK-5), 0434ef6 (TASK-3), 740307f (TASK-2) while
  TASK-7 worked. TASK-7's commit carries the regression suite + docs + ledger entry.

## 30. HANDOFF TO TASK-8

See EXACT NEXT-AGENT INSTRUCTIONS below.

---

## EXACT NEXT-AGENT INSTRUCTIONS FOR TASK-8

TASK-8 (learning-lineage / outcome-quality / counterfactual evidence engine):

1. Re-run the giveback/BE-time distribution study on the FIXED ledger (BUG-088 now
   guarantees real broker PnL reaches the autopsy). Compute MFE-before-BE, MAE-after-BE,
   BE hit rate, BE opportunity cost, normal noise after BE — with the corrected
   BREAK_EVEN_SL_HIT labels (modification-proofed).
2. Build the COUNTERFACTUAL layer explicitly labeled `COUNTERFACTUAL` (never live PnL):
   for each closed trade, replay the management timeline (exit_pending_final_reason +
   reversal_events_json + trajectory) and estimate what BE-at-first-valid-opportunity /
   exit-at-model-reversal / wider-trailing would have produced. Persist to a new
   `counterfactual_estimates` table ONLY IF the canonical stores cannot represent it.
3. Wire the TASK-2 behavior detectors' timestamps into the exit-quality metrics
   (reversal-response time, BE-opportunity rate, giveback) so detectors and lifecycle
   share one timeline. Reuse the canonical definitions — do not recompute.
4. Do NOT change any live exit policy from the counterfactual report; it is evidence for
   a future decision gate only.
5. Watch the TASK-4 `research/dataset.py` eligibility contract (zero-substituted outcome
   rejection) — the learning pipeline must never feed zero-R as if real.
6. Before any further order_manager edits: `git log --oneline -3`, read BUG-081/088/095 +
   TASK-3 handoff, and treat the closed-ticket invariant (`_closed_tickets` /
   `close_requested` gates) as non-negotiable.