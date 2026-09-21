# 2026-09-21 — AGENT-ML-EXP — ML-EXP-003 Bounded Hyperparameter Grid Search Runner

**STATUS: DONE** (PR opened from branch `agent/ml-exp/ml-exp-003`, stacked on `agent/ml-exp/ml-exp-001` = PR #335)

## Objective

Controlled, reproducible hyperparameter search evaluating bounded grids of
learning rate, weight decay, dropout, hidden dimension and loss, logging every
run to the immutable `ExperimentRegistry` from ML-EXP-001.

## Delivered

- `scripts/experiments/hyperparam_search.py` (new) — the runner + CLI.
- `scripts/experiments/grid_search.example.yaml` (new) — a valid, bounded
  16-trial example spec, so the entrypoint is usable on day one.
- `tests/unit/test_hyperparam_search.py` (new) — 47 tests, critical-suite
  registered (`tests/critical_suite.txt` 216 → 217).
- Docs: `docs/ml-system/{TASK_BOARD.md,06_TASK_LEDGER.md,tasks/ML-EXP-003.md}`,
  `agents/taskboard.md`, `agents/locks.yaml` (lock released).

## Design contract

The runner owns the **search**; a caller-injected `TrialRunner` owns training.

- `GridSearchSpec` parsed from YAML (or dict). Canonical form is a nested
  `axes:` mapping; bare top-level axis keys are accepted as flat shorthand.
- Deterministic Cartesian grid: sorted axis names, sorted values. Two runs of
  the same spec produce the identical trial sequence.
- **`MAX_TRIALS = 20` hard ceiling.** An over-specified grid raises
  `GridTooLargeError` at spec-parse time instead of scheduling a week of work —
  this is the NON_GOALS guard against unbounded search / Bayesian optimization.
- Every trial is registered **PENDING before its work starts**, then finalized
  COMPLETED (metrics + artifact hash + manifest) or FAILED (error). A crash
  mid-sweep leaves a permanent, queryable record of what was attempted.
- **ABORT_CONDITIONS**: a trial whose metrics contain NaN/inf is marked FAILED
  and skipped; the sweep continues and the leaderboard never ranks a poisoned
  result. A `trial_runner` exception is caught and recorded, never raised.
- Per-trial isolated output directories (no checkpoint overwrites between trials).
- Seeds are unique and stable: trial `i` uses `seed + i`.
- Re-running the same spec against the same registry is **idempotent** —
  finalized records are reused, never re-written (the registry is write-once).
- `leaderboard.json` written atomically per sweep; ranking direction is derived
  from the metric name (`val_loss` descends, `fold_sharpe`/`minority_f1` ascend)
  by `ExperimentRegistry.top_n`, never assumed.

## Honest non-goals / limits found while implementing

- `build_loss` from ML-TRAIN-002 lives in `src/nexus_scalp/training/losses.py`,
  which is still only on branch `agent/ml-train/ml-train-002` (PR #333), not on
  `origin/main`. This task's branch is **stacked on PR #335** (ML-EXP-001) so it
  can compile against `ExperimentRegistry`. The `loss` axis is therefore
  declared as a *categorical* axis (`CATEGORICAL_AXES`) that a trial runner
  reads from `params`/`model_config` — the runner does not import `build_loss`
  itself, which keeps the module torch-free. The natural follow-up once #333
  lands: a trial runner that selects the loss through `build_loss`.
- `ExperimentSpec` (`src/nexus_scalp/model_lab/registry.py:43`) declares
  `learning_rate`, `weight_decay`, `batch_size`, `epochs`, `label_smoothing`…
  but **not** `dropout` or `hidden_dim` — those are architecture knobs living in
  `nexus_scalp/model_lab/architectures.py`, outside this task's
  OWNERSHIP_SCOPE. `lab_trial_runner_factory` forwards only fields
  `ExperimentSpec` actually declares; `dropout`/`hidden_dim` stay in the
  recorded provenance (`params` / `model_config`) so an architecture layer can
  read them, and are never silently dropped from the reproduction record.
- The default `synthetic_trial_runner` is a deterministic stdlib-only response
  surface (quadratic in `learning_rate`, optimum at 3e-3) so the grid has a
  genuine, checkable optimum and tests run without torch.

## Bugs caught by my own tests (all fixed + pinned)

1. **Spec schema ambiguity**: `load_spec` accepted `axes:` nested *and*
   top-level axis keys, so `{name, axes: {...}}` tripped the "unknown spec key"
   guard and every test failed. Now one canonical form with an explicit
   shorthand, plus a rejection when an axis appears in both places.
2. **`loss` axis type crash**: a string axis (`loss: focal_smoothing`) hit
   `_as_float_list` and raised `TypeError` instead of a clean `GridSpecError`.
   Split into `CATEGORICAL_AXES` with its own coercion.
3. **Ambiguous trial-id token**: `_slugify` kept underscores, so
   `learning_rate0.003` could read as two fields. Token separator is now `-`
   (`learning-rate0.003`).
4. **Non-idempotent re-run**: a second `run_grid_search` on the same registry
   tried to `record_result` an already-COMPLETED experiment and tripped
   `ExperimentImmutabilityError`. Finalized trials are now reused (`"reused":
   true`) instead of re-finalized.
5. **Weak surface**: the original quadratic coefficient (40.0) was smaller than
   the per-seed jitter (±0.02), so the optimum was not reliably the minimum
   across seeds. Raised to 500.0 so the learning-rate signal dominates noise.
6. **CLI leaderboard depth**: `--top N` printed N rows to stdout but always
   wrote top-3 to `leaderboard.json`. `run_grid_search(top_n=...)` now threads
   the CLI value through to the file.
7. **mypy "found twice under different module names"**: `scripts/experiments/`
   had no `__init__.py`; added (plus `scripts/__init__.py`) and mypy runs with
   `--explicit-package-bases`.

## Verification

```
pytest tests/unit/test_hyperparam_search.py -v     -> 47 passed
ruff check  <both files>                           -> All checks passed!
ruff format --check <both files>                   -> 2 files already formatted
mypy --explicit-package-bases <both files>         -> Success: no issues found
scripts/ci/verify_critical_suite_manifest.py       -> CRITICAL_SUITE_MANIFEST_OK: 217 paths all exist
```

Test coverage groups: spec validation (unknown axis/key, scalar vs list,
non-numeric, non-finite, empty, ceiling exceeded, both-places rejection,
shorthand equivalence), deterministic materialization, stable/unique/registry-
safe ids, the 4-trial BENCHMARK_PLAN mini sweep (all 4 in the registry with
distinct ids and the 3 required metrics), isolated per-trial output dirs, unique
stable seeds, leaderboard ranking + direction derivation + JSON self-consistency,
NaN/inf abort + skip, runner-exception capture, partial-failure leaderboard,
idempotent re-run, tamper-checkable manifests, reproduction bundle round-trip,
shipped example spec validity, CLI entrypoint, and the lazy torch-free import
contract (`torch` absent from `sys.modules` after importing the module).

## Handoff

- Branch: `agent/ml-exp/ml-exp-003` (stacked on `agent/ml-exp/ml-exp-001`).
- Land order: **#333 (losses) and #335 (registry) must land before this PR**;
  this PR's base is `agent/ml-exp/ml-exp-001`.
- Swarm state: `completed_tasks` gains `ML-EXP-003`; 23/30 done.
- Next unblocked candidate: **ML-VAL-002** (Probability Calibration & ECE,
  AGENT-ML-VALIDATION) — its ledger dep is ML-EXP-001, but its own task file
  also lists ML-EXP-002 (HUMAN_DECISION_REQUIRED) as a dependency; read the
  task file before committing to it.
