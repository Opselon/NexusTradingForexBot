# TASK-TEMPORAL-01 — Handoff (Hermes-TemporalLiquidity)

> Agent: Hermes-TemporalLiquidity (AGENT-TEMPORAL-01) · 2026-08-19
> Task: 70D Temporal Liquidity Intelligence + Signal Stability
> Starting HEAD: a190602 (main == origin/main) · Status: RESEARCH COMPLETE

## 1. Deliverables

- **Forensics**: real flapping reproduced (4000 XAUUSD M1 events, 597
  flips, median interval 60 s); root cause = E combination (liquidity
  feature volatility + model boundary noise + missing temporal context).
  docs/70D_TEMPORAL_FORENSIC_BASELINE.md.
- **Temporal layer**: src/nexus_scalp/features/temporal.py — 22 causal
  dims (lag1/lag2/delta1/persistence/time-since-change/state-duration),
  cold-start policy, O(1) bounded tracker, NO_FUTURE_LEAKAGE verified.
- **Candidate schema**: scalp_v4_temporal_candidate (92D) registered in
  features/schema.py. RESEARCH ONLY — never ACTIVE.
- **Stability controller**: src/nexus_scalp/signals/stability_controller.py
  — NONE/CANDIDATE/CONFIRMED state machine, entry(2)/exit(1) separation,
  HARD_REVERSAL (margin>=0.20), max_candidate_age=12, reset().
- **Experiment matrix**: A=70D / B=+lag / C=+lag+delta / D=+lag+persist /
  E=full; C best accuracy (0.400), D fewest raw flips (467); controller
  reduces flips 12-45% (32.5% on baseline A).
- **Ablation**: lag1 + delta highest value; persistence cuts flips; tsc
  helps accuracy; no component redundant.
- **Tests**: tests/unit/test_temporal_liquidity_phase20.py — 34 green
  (TEST-TEMPORAL-01..30).
- **Artifacts**: artifacts/forensics/{70d_signal_flapping_trace.json,
  liquidity_feature_deltas.json, temporal_step03_determinism_state.json,
  temporal_experiment_matrix.json, temporal_ablation.json,
  temporal_frame_4000.parquet}.

## 2. Registry rows

- agents/taskboard.md: TASK-TEMPORAL-01 row added (IN_PROGRESS -> update to
  READY_FOR_REVIEW in the commit).

## 3. Known findings (not introduced by this task)

- liquidity_confluence v1.0 degeneracy (7 unique values over 4000 bars) —
  BUG-107 family, upstream TASK-06 optimization scope.
- Pool-state per-bar oscillation around level boundaries — canonical
  re-derivation design property; the stability controller is the DECISION-
  layer fix; the engine itself is deterministic + parity-correct.

## 4. Exact next-agent instructions

1. Read docs/70D_TEMPORAL_LIQUIDITY_FINAL_REPORT.md + the two temporal
   docs.
2. Run the full purged walk-forward / OOS / robustness for C and E vs A
   with identical budgets (TASK-04 protocol) before any promotion
   discussion. The research used a chronological last-20% split.
3. Move scalp_v4_temporal_candidate through governance ONLY if
   walk-forward confirms (EVALUATING -> VALIDATED). NEVER ACTIVE/CHAMPION
   in this series (brief 45).
4. Wire [TEMPORAL_LIQUIDITY] + [SIGNAL_STABILITY] telemetry into the
   existing SSE/audit paths with bounded throttling (brief 36).
5. Re-verify parity at the canonical H=4000 bound (research used the
   documented H=300 research bound).
6. Do NOT modify the protected 70D contract (scalp_v3) — temporal features
   are additive under the candidate schema id only.

## 5. Verification state

- Feature determinism: VERIFIED (3x identical).
- Anti-leakage: VERIFIED (NO_FUTURE_LEAKAGE).
- 34/34 temporal tests green; ruff/mypy on the new modules PASSED.
- Full beforePush gate: run by the next agent (parallel WIP in the tree
  must not be committed by this task — only the files listed above).
