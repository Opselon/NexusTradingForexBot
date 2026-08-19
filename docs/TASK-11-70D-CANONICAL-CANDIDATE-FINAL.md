# TASK-11 — 70D CANONICAL CANDIDATE — FINAL REPORT

> Agent: AGENT-11 · TASK-11-70D-CANONICAL-CANDIDATE · 2026-08-19

## Git

- BASE_HEAD / REMOTE_HEAD: `3f3f3d9` → TASK-11 commits landed:
  `8635c66` (schema canonicalization), `45515d5` (fairness gates),
  `d52b893` (walk-forward evidence) — pushed and verified on origin/main.
- Parallel swarm active (TASK-02/03/04/05/06/07/08/09/12/13 all wrote
  into the shared tree; wf_candidate dir is contended).

## Schema

- **CANONICAL_70D_SCHEMA = `scalp_v3`**, dimension 70, hash
  `235b8fccc96b7e0e`.
- Full rationale + explicit diff vs scalp_v4: docs/70D_SCHEMA_RECONCILIATION.md.
- Legacy `scalp_v4` classified LEGACY; governance alignment excludes it;
  TEST-SCHEMA-70D-01..08 enforce the canonical contract.

## scalp_v3 vs scalp_v4 (exact)

Both 70D; v3 = canonical news@59 + liquidity 60..69 + hash + contract
module + dataset + shadow + governance. v4 = legacy TASK-02 "Family"
variant, no hash, no dataset, no governance allowance. See reconciliation
report for the full table.

## Dataset

- `ds_task5_real70d_2500`: schema scalp_v3, hash 235b8fccc96b7e0e,
  XAUUSD M5, 2025-03-12 06:35 → 2025-03-25 03:20 UTC, 2,446 rows,
  train/val/test 1712/366/368, labels triple_barrier_3class_v1,
  purge 3 / embargo 3, dataset_hash f27e987f….
- Source: data/raw/XAUUSD_M5.parquet (100,000 real M5 bars, zero nulls,
  proper time_utc; only market-close/rollover gaps — validated).
- Quality audit: 70/70 feature columns finite, in [-3,3], news family
  uniformly 0 = FEATURE_DISABLED (news DB only covers 2026-08-17..18, no
  overlap with the training window — honest zero family, documented).

## Candidate

- `task5_abc_C_v1` (existing, TRAINED, scalp_v3/70D, input_dimension 70,
  dataset ds_task5_real70d_2500, artifact hash 2d4f8deb…, params 267,492).
- TASK-11 walk-forward run (wf_candidate path): model.pt hash
  80b6d159…, scaler 0bbe342…, 4 folds, purge/embargo 15/15.
  NOTE: wf_candidate is parallel-swarm contended — a parallel agent
  overwrote the artifact after my hash capture (meta now scalp_v4, bytes
  9265e4b7…); my run's evidence is frozen in scratch/task11_3_*.

## Benchmark / Walk-Forward / OOS

- A/B/C/D arms: ds_task5_real70d_2500 (70D) + 50D/60D comparison arms
  share the same source slice (timestamps/labels identical — proven).
- Walk-forward OOS (4 folds, 117 OOS samples): **PF=0.37, win_rate 23.7%,
  all folds negative sharpe** — the 70D candidate does NOT beat the
  no-trade baseline on this small real sample.

## Robustness / Calibration

- ValidationFactory gates on the candidate: CHALLENGER_ELIGIBLE (label
  integrity, class collapse 89.8% dominant, regime coverage, OOS floors
  pass structurally) — but OOS metrics are 0.0 when no real OOS
  probabilities are supplied (the honest empty-evidence state).
- Robustness/calibration evidence on the real OOS: NOT produced (the
  walk-forward OOS is negative → no point claiming robustness).

## Shadow

- NO_VALIDATED_CANDIDATE (the candidate is NOT_ELIGIBLE on OOS) → shadow
  stays idle by design (INV-018, truthful).

## Governance

- Load gate + promotion state machine intact; no auto-promotion.
- POST /api/models/promotion/execute NOT called (task §33).

## Champion

- UNCHANGED: artifacts/models/scalp/XAUUSD/v1.0.0 (RESTORED_CANDIDATE
  bench_a_v1-derived, hash 9105cef7…, scaler 6ed24425…, 50D scalp_v1).
  Original f0f70efb… unrecoverable (BUG-104) — operator decision pending.

## Latency

- 70D inference (task5_abc_C_v1, 200 vectors): p50/p95/p99 measured in
  TEST-70D-MODEL-24 (budget 50ms shadow; suite green).

## Bugs

- No new BUG-NNN created (all fixes in-task with regression guards).
- Observed: wf_candidate swarm contention (documented, not a code bug).

## Final verdict

**70D_CANDIDATE_NOT_ELIGIBLE** — the canonical schema is resolved
(scalp_v3) and a real dataset + candidate exist, but the walk-forward OOS
is negative (PF 0.37 / win 23.7% on 117 OOS samples). Per TASK-11 §33 the
final state is NOT_ELIGIBLE; no promotion, no shadow deployment, Champion
untouched. The evidence is honest and reproducible (scratch/task11_*).