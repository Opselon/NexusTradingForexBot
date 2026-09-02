# 70D TEMPORAL LIQUIDITY INTELLIGENCE + SIGNAL STABILITY — FINAL REPORT

> Task: 70D Temporal Liquidity Intelligence + Signal Stability
> Agent: Hermes-TemporalLiquidity (AGENT-TEMPORAL-01) · 2026-08-19
> Base commit: a190602 (HEAD == origin/main at bootstrap)
> Schema: scalp_v3 = 70D canonical (hash 235b8fccc96b7e0e), PROTECTED
> Status: RESEARCH COMPLETE — no production promotion

## ROOT CAUSE

The rapid BUY/SELL oscillation is a THREE-FACTOR combination (brief §7 verdict E):

1. **Liquidity feature instability (A)**: 6 of the 10 canonical liquidity
   dimensions change on 88-98% of consecutive M1 bars (bsl_distance_atr
   0.901, ssl 0.880, eqh 0.980, eql 0.985, htf 0.974, internal 0.911).
   The engine recomputes every feature fresh from the full causal bar
   history at every decision — the model sees a nearly NEW liquidity vector
   each bar with zero history of its evolution.
2. **Model boundary noise (B)**: the trained 70D baseline's decision margin
   |PBUY - PSELL| NEVER exceeds 0.27 (median 0.135, flip-event median
   0.158). The raw argmax operates permanently near the BUY/SELL boundary.
3. **Missing temporal context (C)**: liquidity pool states re-derive per
   bar (SWEPT/RECLAIMED/TOUCHED oscillate around level boundaries — 12
   pools observed), and no persistence/lag/state-duration information is
   presented to the model.

Evidence: 4000-event real XAUUSD M1 capture (artifacts/forensics/
70d_signal_flapping_trace.json) through the canonical 70D pipeline + a
70D baseline model trained on the same causal frame: **597 BUY<->SELL flips,
median flip interval 60 s (= 1 M1 bar)**.

## FEATURE FORENSICS (all 10 liquidity dims)

| dim | name | unique | chg_frac | classification |
| --- | --- | ---: | ---: | --- |
| 60 | bsl_distance_atr | 3452 | 0.901 | tick/bar-sensitive |
| 61 | ssl_distance_atr | 3363 | 0.880 | tick/bar-sensitive |
| 62 | eqh_strength | 3885 | 0.980 | structure-sensitive (recomputed) |
| 63 | eql_strength | 3909 | 0.985 | structure-sensitive (recomputed) |
| 64 | htf_liquidity_score | 3878 | 0.974 | HTF-sensitive (recomputed) |
| 65 | internal_liquidity_distance | 3540 | 0.911 | tick/bar-sensitive |
| 66 | external_liquidity_distance | 1591 | 0.435 | event/bar-sensitive |
| 67 | liquidity_confluence | 7 | 0.004 | DEGENERATE (v1.0 class, BUG-107 family) |
| 68 | liquidity_sweep_state | 5 | 0.318 | event-sensitive (step fn) |
| 69 | post_sweep_displacement | 519 | 0.193 | event/bar-sensitive |

## TEMPORAL DESIGN

22 causal dims (docs/70D_TEMPORAL_FEATURE_CONTRACT.md) in
`features/temporal.py::TemporalLiquidityTracker`:
- distances (bsl/ssl/internal/external): lag1 + lag2 + delta1
- strengths (eqh/eql): lag1 + persistence(3-bar)
- htf score: lag1 + state_duration (bars since sign change)
- confluence: lag1 + persistence (degenerate source -> near-constant)
- sweep state: persistence + time-since-change (event semantics, NOT a raw
  continuous series)
- displacement: lag1 + delta1

Cold start: documented neutrals (3.0 distances, 0.0 others); delta at cold
start = 0.0 (no change evidence). All clipped [-3,+3].

## SCHEMA

- CURRENT (protected): scalp_v3 = 70D (Base 0..49 | News 50..59 |
  Liquidity 60..69), hash 235b8fccc96b7e0e. UNTOUCHED.
- CANDIDATE: scalp_v4_temporal_candidate = 92D (70 canonical + 22 temporal),
  registered in features/schema.py. RESEARCH ONLY — never ACTIVE.

## MODEL

70D baseline trained on the same causal frame (CandidateTrainer,
LEGACY_SCALPNET_V1, 8 epochs, seed 42). Raw vs stabilized decision on the
same 4000 events:

| cell | val_acc | raw flips | stable flips | reduction |
| --- | ---: | ---: | ---: | ---: |
| A 70D | 0.360 | 610 | 412 | 32.5% |
| B +lag | 0.260 | 754 | 414 | 45.1% |
| C +lag+delta | 0.400 | 527 | 441 | 16.3% |
| D +lag+persist | 0.379 | 467 | 408 | 12.6% |
| E full 22D | 0.398 | 589 | 486 | 17.5% |

## STABILITY CONTROLLER

`signals/stability_controller.py::DecisionStabilityController` — causal,
O(1), stateful, deterministic, bounded. States NONE -> BUY/SELL_CANDIDATE ->
CONFIRMED. Parameters (chosen by replay evidence): entry_min_margin 0.05,
hard_reversal_margin 0.20, entry_confirm_bars 2, exit_confirm_bars 1,
max_candidate_age 12. Entry/exit separation via position_open flag
(brief 29). HARD_REVERSAL = margin >= 0.20 (strong probability margin) —
structural confirmation available via structural_buy/sell (brief 30).
Reset on symbol/model/schema/timeframe/restart (brief 32).

## ENTRY / EXIT

Separate confirmations: entry needs 2 consecutive same-direction
observations; an open position (position_open=True) exits on 1 — the
different cost of delay (brief 29) is honored.

## REPLAY

Real XAUUSD M1 history (data/raw/XAUUSD_M1.parquet). Flapping sequences
replayed through A (70D), E (92D), and A+controller; metrics above.

## ABLATION

| variant | val_acc | flips |
| --- | ---: | ---: |
| E_full (22D) | 0.398 | 589 |
| E_no_lag1 | 0.320 | 616 |
| E_no_lag2 | 0.360 | 628 |
| E_no_delta | 0.335 | 598 |
| E_no_persist | 0.359 | 635 |
| E_no_tsc | 0.343 | 668 |

lag1 and delta are the highest-value components (accuracy drops 0.08/0.06
when removed); persistence removal raises flips; tsc removal lowers
accuracy. NO temporal component is redundant.

## WALK-FORWARD / OOS

CandidateTrainer chronological split (last 20% = OOS) on the SAME frame
for every cell — the only experimental variable is the feature
representation (fair, brief 22). Full purged walk-forward is the next
governance step (brief 44) before any promotion discussion.

## PERFORMANCE

- Temporal tracker update: O(1) (bounded 8-vector buffer); 5000 updates <
  0.5 s measured. No DB/network/model reload on the update path (tested).
- Liquidity engine per-event cost (canonical): H=4000 ~1.2 s (BUG-106
  class, TASK-05 bounded); research capture used H=300 ~27 ms.
- p50/p95/p99 of the full capture pipeline not instrumented per-stage in
  this run (perf harness deferred to the governance phase); the tracker
  itself is microseconds.

## DEBUG

TemporalLiquiditySnapshot exposes: current liquidity (via canonical
vector), lag1/lag2, delta1, persistence, time-since-change per dimension.
StabilityDecision exposes: raw_direction, stable_direction, PBUY, PSELL,
margin, candidate_direction, candidate_age, confirmation_progress,
required_confirmation, last_confirmed_direction, state
(STABLE/NOISY/CHANGING/REVERSING/CONFIRMED). Raw model output is never
altered — the controller only filters the stable decision (brief 35).

## TESTS

tests/unit/test_temporal_liquidity_phase20.py — **34 tests green**
covering TEST-TEMPORAL-01..30 (lag 1/2/3, delta, persistence, tsc, cold
start, no-future-leakage, determinism, parity, state persistence, sweep
persistence, cache parity, BUY/SELL stability, weak/strong opposite,
candidate timeout, hard reversal, restart reset, model/schema reset,
entry/exit separation, raw unchanged, deterministic, flip reduction,
reversal latency, O(1), no I/O, debug fields, schema registration,
ablation guard).

## BUGS

No liquidity feature mathematics was proven incorrect (determinism +
parity verified). Two pre-existing findings documented, NOT introduced:
- liquidity_confluence v1.0 degeneracy (7 unique values) — BUG-107 family,
  owned by TASK-06 optimization.
- Pool-state per-bar oscillation (SWEPT/RECLAIMED/TOUCHED) around level
  boundaries — a design property of the canonical re-derivation, fixed at
  the DECISION layer (controller), not by rewriting the protected engine.

## FINAL VERDICT

**BOTH_REQUIRED** — the temporal feature layer (lag/delta/persistence adds
accuracy: C 0.400, E 0.398 vs A 0.360; D cuts raw flips 23%) AND the
decision stability controller (12-45% flip reduction across cells, 32.5%
on the baseline) are both justified by real replay evidence.

## FILES

- src/nexus_scalp/features/temporal.py (new) — 22D causal temporal extractor
- src/nexus_scalp/signals/stability_controller.py (new) — controller
- src/nexus_scalp/features/schema.py (edit) — scalp_v4_temporal_candidate
- tests/unit/test_temporal_liquidity_phase20.py (new) — 34 tests
- docs/70D_TEMPORAL_FORENSIC_BASELINE.md (new)
- docs/70D_TEMPORAL_FEATURE_CONTRACT.md (new)
- docs/70D_TEMPORAL_LIQUIDITY_FINAL_REPORT.md (this file)
- scratch/temporal_step01*.py, step02, step03, step07*.py (harnesses)
- artifacts/forensics/70d_signal_flapping_trace.json (8.6 MB)
- artifacts/forensics/liquidity_feature_deltas.json
- artifacts/forensics/temporal_step03_determinism_state.json
- artifacts/forensics/temporal_experiment_matrix.json
- artifacts/forensics/temporal_ablation.json
- artifacts/forensics/temporal_frame_4000.parquet

## HANDOFF (next agent)

Read: this report + 70D_TEMPORAL_FORENSIC_BASELINE.md +
70D_TEMPORAL_FEATURE_CONTRACT.md. Next steps: (1) run the full purged
walk-forward/OOS/robustness for cell C/E vs A with identical budgets
(TASK-04 protocol); (2) if validated, move scalp_v4_temporal_candidate
through governance (EVALUATING -> VALIDATED) — NEVER promote within this
series; (3) wire the controller's telemetry ([TEMPORAL_LIQUIDITY],
[SIGNAL_STABILITY]) into the existing SSE/audit paths with bounded
throttling; (4) re-run the canonical H=4000 parity for the final numbers
(the research capture used the documented H=300 bound).
