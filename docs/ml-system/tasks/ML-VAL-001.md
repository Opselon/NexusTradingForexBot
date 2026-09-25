# ML-VAL-001 — Purged Walk-Forward Fold Monotonicity & Embargo Boundary Audit

STREAM: STREAM G — VALIDATION/OOS
PRIORITY: P1
STATUS: DONE (2026-09-19; ZERO temporal leakage proven across all 34 folds, both geometries — PR #313)
DEPENDENCIES: ML-DATA-001 (DONE)
AGENT_ROLE: AGENT-ML-VALIDATION
OWNERSHIP_SCOPE: src/nexus_scalp/training/walk_forward_trainer.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Verify that WalkForwardTrainer._split_fold_with_embargo strictly enforces 15-bar purge gaps and 15-bar embargo windows across all 34 folds, proving zero temporal data leakage.

## WHY_IT_EXISTS
Temporal leakage in financial ML models produces unrealistically high backtest metrics that collapse in live execution. Purge and embargo boundaries must be mathematically verified to guarantee no sample overlap.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `1853-1937`)
  - **Symbol:** `WalkForwardTrainer._split_fold_with_embargo`
  - **Behavior:** Computes train_idx, purge_gap, test_idx, embargo_bars; applies purge_gap_bars=15 and embargo_bars=15
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `1940-1990`)
  - **Symbol:** `WalkForwardTrainer._generate_expanding_folds`
  - **Behavior:** Generates 34 expanding walk-forward folds by default
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- 34 expanding walk-forward folds generated.
- Purge gap = 15 bars, embargo = 15 bars.

## UNKNOWNs
- Boundary index behavior when fold splits cross weekend market closures.

## SCOPE
Write tests/unit/test_purge_embargo_monotonicity.py checking index sets and timestamps across all 34 folds; assert set(train).isdisjoint(test) and temporal gap >= 15 bars.

## NON_GOALS
Do not modify the 15-bar constant values.

## SOURCE_AREAS
- `src/nexus_scalp/training/walk_forward_trainer.py:1850-2010`

## FILES_LIKELY_TO_CHANGE
- `tests/unit/test_purge_embargo_monotonicity.py`

## INVESTIGATION_PLAN
Verify whether purge gap removes bars by array index row count or by timestamp difference.

## IMPLEMENTATION_PLAN
1. Create tests/unit/test_purge_embargo_monotonicity.py.
2. Generate synthetic time series of 50,000 bars.
3. Generate all 34 folds via _generate_expanding_folds.
4. For every fold: assert train and test index sets are strictly disjoint.
5. Assert: min(test_idx) - max(train_idx) > 15.
6. Assert: max(train_timestamp) < min(test_timestamp).

## TEST_PLAN
- `pytest tests/unit/test_purge_embargo_monotonicity.py -v`

## BENCHMARK_PLAN
Audit 34 folds; zero index collisions across all folds.

## EVIDENCE_REQUIRED
- Test file: tests/unit/test_purge_embargo_monotonicity.py
- Pytest output verifying disjointness across all 34 folds

## ACCEPTANCE_CRITERIA
1. tests/unit/test_purge_embargo_monotonicity.py passes with 0 failures. [x] VERIFIED — 14/14 green (2.36s), slim Linux venv.
2. Mathematically proven zero index overlap between training and validation across all folds. [x] VERIFIED — `set(train).isdisjoint(set(test))` asserted per fold for BOTH geometries (blocked + expanding), at 50,000 bars (4-fold canonical) and 12,000 bars (34-fold expanding, the production candidate setting). Zero collisions found.

## VERIFICATION_EVIDENCE (2026-09-19, AGENT-ML-VALIDATION, slim venv .venv-linux)
- Command: `PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/unit/test_purge_embargo_monotonicity.py -v`
  - Result: **14 passed in 2.36s** — 0 failures.
- Coverage:
  - `test_train_test_index_sets_disjoint_all_folds[blocked|expanding]` — strict set disjointness on all 4 canonical folds at DATASET_BARS=50,000.
  - `test_purge_gap_separation_at_least_15_bars[blocked|expanding]` — `test_start_point - train_end_point >= 15` (purge invariant) on every fold.
  - `test_embargo_tail_reserved_every_fold[blocked|expanding]` — `fold_len - test_end_point >= 15` (embargo tail invariant) on every fold.
  - `test_fold_boundaries_monotonic[blocked|expanding]` — validation windows advance strictly; no fold's test window starts before the previous one ends.
  - `test_time_based_purge_overlap_contract` — temporal-overlap path: every retained train row's outcome timestamp resolves <= the validation boundary.
  - `test_time_based_purge_rejects_length_mismatch` — contract guard: timestamps/outcome_offsets length mismatch raises ValueError.
  - `test_time_based_purge_uses_actual_timestamps_not_row_counts` — sparse 600s decision grid + 3600s horizon: the purge width tracks the REAL horizon overlap (6 grid rows), not the fixed 15-row count; the purged boundary row is proven to actually overlap (no over-purge).
  - `test_split_fold_with_embargo_degenerate_shapes` — tiny folds and folds whose validation block is smaller than the embargo: train_end clamps >= 0, val_end clamps within the fold; never inverted.
  - `test_timestamps_crossing_session_gap_hold_invariants` — the task's UNKNOWN (folds crossing a weekend market closure): a 2-day timestamp jump placed right after the raw split boundary still yields a valid purge + disjoint sets.
  - `test_real_walk_forward_geometry_34_folds_expanding` — the task's canonical 34-fold expanding geometry at 12,000 rows: all 34 folds disjoint, purge >= 15, embargo >= 15, train window anchored at row 0 and monotonically non-decreasing.
- Neighbor regression guards (shared purge/embargo surface): `test_agent16_walkforward_purge_embargo_leakage.py` + `test_dataset_split_purge_bug244.py` — **31 passed**, no behavior change.
- `ruff check` + `ruff format --check` → clean; `mypy tests/unit/test_purge_embargo_monotonicity.py` → Success (no issues).
- `scripts/ci/verify_critical_suite_manifest.py` → `CRITICAL_SUITE_MANIFEST_OK: 210 paths all exist`.

## FINDINGS (honest, no leakage found — this is a verification task)
- **PURGE/EMBARGO CONTRACT IS SOUND.** `WalkForwardTrainer._split_fold_with_embargo`
  (`walk_forward_trainer.py:1904-1987`) computes `train_end = raw_split - purge_gap`,
  `val_start = raw_split`, `val_end = fold_length - embargo_bars`. Disjointness holds by
  construction: `train_end < val_start` whenever `purge_gap >= 1`, and the embargo is a
  pure tail reservation. No code change was required in OWNERSHIP_SCOPE.
- **Expanding mode introduces NO new leakage.** `walk_forward_trainer.py:657-666` widens
  only the TRAIN window (`[0, start_idx + train_end_point)`); the validation window and
  the purge/embargo widths are byte-identical to blocked mode. The added prior rows are
  strictly OLDER than the fold, which is the definition of anchored walk-forward.
- **The timestamp path is stricter in kind, not width.** Its purge is derived from actual
  outcome-vs-boundary overlap (`outcome_ts > boundary_ts or gap <= embargo_bars`), so on a
  sparse decision grid it can be NARROWER than the 15-bar row-count purge — this is correct
  (the row-count path is a conservative worst case on a dense M1 grid), and the audit pins
  the exact semantic: no retained train row may resolve after the boundary, and every purged
  row must actually overlap.
- **ABORT_CONDITION never fired** — no fold had overlapping train/test indices.

## ABORT_CONDITIONS
If any fold has overlapping train/test indices, flag critical data leakage bug and STOP.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/unit/test_purge_embargo_monotonicity.py`

## SHARED_FILE_RISK
Low. Test file only.
