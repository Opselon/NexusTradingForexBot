# 2026-09-19 AGENT-ML-VALIDATION ML-VAL-001 — Purged Walk-Forward Fold Monotonicity & Embargo Boundary Audit

**Task:** ML-VAL-001 (Stream G — Validation/OOS), P1
**Role:** AGENT-ML-VALIDATION
**Status:** DONE — zero temporal leakage proven — PR opened (`agent/ml-validation/ml-val-001`)
**Worktree:** `/tmp/wt-validation-ml-val-001` off `origin/main` @ `117ce7f1`

## Objective
Prove that `WalkForwardTrainer._split_fold_with_embargo` strictly enforces the 15-bar purge
gap and the 15-bar embargo window across all folds, in both walk-forward geometries, so no
temporal data leakage inflates validation metrics.

## Verdict: NO LEAKAGE FOUND — contract is sound

This is a **verification** task (per the established pattern for validation-stream work):
the purge/embargo construction is correct by design, and the deliverable is a test battery
that pins every invariant so a future regression fails loudly. **No production code was
changed** (OWNERSHIP_SCOPE is `walk_forward_trainer.py`; it needed no edit).

`_split_fold_with_embargo` (`walk_forward_trainer.py:1904-1987`) computes:
```
train_end = max(0, raw_split - purge_gap)      # purge removes the train tail
val_start = raw_split
val_end   = max(val_start, fold_length - embargo_bars)   # embargo reserves the tail
```
Disjointness holds by construction (`train_end < val_start` whenever `purge_gap >= 1`),
and the embargo is a pure tail reservation. The expanding mode (`:657-666`) widens only
the TRAIN window (`[0, start_idx + train_end_point)`) — validation and purge/embargo widths
are identical to blocked mode, so it adds strictly-older prior rows and no new leakage.

## Test battery — 14 tests, all green
`tests/unit/test_purge_embargo_monotonicity.py` (registered in `tests/critical_suite.txt`):

| Test | Invariant pinned |
|---|---|
| `test_train_test_index_sets_disjoint_all_folds[blocked\|expanding]` | `set(train).isdisjoint(set(test))` — all 4 canonical folds @ 50,000 bars |
| `test_purge_gap_separation_at_least_15_bars[blocked\|expanding]` | `test_start_point - train_end_point >= 15` every fold |
| `test_embargo_tail_reserved_every_fold[blocked\|expanding]` | `fold_len - test_end_point >= 15` every fold |
| `test_fold_boundaries_monotonic[blocked\|expanding]` | validation windows advance strictly; never overlap in time |
| `test_time_based_purge_overlap_contract` | temporal path: no retained train row resolves after the boundary |
| `test_time_based_purge_rejects_length_mismatch` | timestamps/offsets length mismatch → ValueError |
| `test_time_based_purge_uses_actual_timestamps_not_row_counts` | sparse grid: purge width tracks the REAL horizon (6 rows), not 15 |
| `test_split_fold_with_embargo_degenerate_shapes` | tiny/embargo-overflow folds clamp, never invert |
| `test_timestamps_crossing_session_gap_hold_invariants` | the task's UNKNOWN: weekend closure jump holds invariants |
| `test_real_walk_forward_geometry_34_folds_expanding` | the canonical 34-fold production geometry: all disjoint, purge/embargo ok |

The fold-boundary replay helper mirrors the real fold loop (`walk_forward_trainer.py:608-660`)
exactly — same `fold_size = total // num_folds`, same last-fold-remainder absorption, same
`expanding` train anchoring — so the audit exercises the geometry `train_and_validate`
actually selects.

## Honest finding: the timestamp purge is narrower, not wider
One initial test assumption was wrong and was corrected against real behavior: on a sparse
decision grid (600s spacing) with a 3600s label horizon, the temporal purge removes 6 rows
while the row-count path removes 15. That is **correct** — the row-count path is the
conservative worst case for a dense M1 grid, and the temporal path derives the exact
overlap. The battery now pins the precise semantic: (a) no retained train row may resolve
after the validation boundary, and (b) every purged row must actually overlap (no
over-purge). This is the kind of invariant a hand-waved test would have gotten backwards.

## Verification evidence
- `PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/unit/test_purge_embargo_monotonicity.py -v`
  → **14 passed in 2.36s**
- Neighbor guards on the shared surface:
  `test_agent16_walkforward_purge_embargo_leakage.py` + `test_dataset_split_purge_bug244.py`
  → **31 passed** (no behavior change, since no production code changed)
- `ruff check` + `ruff format --check` → clean; `mypy` → Success (no issues)
- `scripts/ci/verify_critical_suite_manifest.py` → `CRITICAL_SUITE_MANIFEST_OK: 210 paths all exist`

## Artifacts
- `tests/unit/test_purge_embargo_monotonicity.py` (NEW, 14 tests, critical-suite registered)
- `docs/ml-system/tasks/ML-VAL-001.md` — STATUS → DONE, acceptance criteria `[x]` with evidence
- `docs/ml-system/TASK_BOARD.md` / `docs/ml-system/06_TASK_LEDGER.md` → DONE
- `agents/taskboard.md` — closeout row appended

## Unblocks
`ML-VAL-003` (Robustness Stress: Slippage & Spread Perturbation, P2 — dep was ML-VAL-001).
`ML-BT-001` (Trading Quality Metrics, P1) still needs `ML-VAL-003` + `ML-VAL-001`; VAL-001
half is now satisfied.

## Next candidate
`ML-TRAIN-001` (P1 | AGENT-ML-TRAIN: Deterministic Training Engine, Seed Harness & AMP —
dep ML-DATA-001 DONE), or `ML-PLAT-002` (P1 | AGENT-GIT/PLATFORM: Signed Official Model
Bundle Verification — dep ML-GOV-001 DONE).
