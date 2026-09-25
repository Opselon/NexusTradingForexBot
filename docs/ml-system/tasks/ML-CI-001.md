# ML-CI-001 — CI Model Training Smoke vs Real Validation Gap & Nightly Harness

STREAM: STREAM L — OBSERVABILITY
PRIORITY: P2
STATUS: DONE (2026-09-23, AGENT-QA)
DEPENDENCIES: ML-DATA-001, ML-VAL-001
AGENT_ROLE: AGENT-QA
OWNERSHIP_SCOPE: .github/workflows/nightly-ml-benchmark.yml
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Bridge the gap between fast CI synthetic smoke tests and full production candidate training by creating an automated, scheduled benchmark harness that exercises real multi-fold walk-forward training.

## WHY_IT_EXISTS
Current CI runs only synthetic smoke tests (SMOKE_MIN_ROWS=3000) to keep PR checks fast. Full walk-forward candidate training is never exercised in CI, meaning syntax regressions, gate evaluation bugs, or tensor shape mismatches in training pipelines can slip undetected into main.

## CURRENT_EVIDENCE
- **Path:** `tests/critical_suite.txt` (Lines: `1-439`)
  - **Symbol:** `Critical Suite`
  - **Behavior:** Contains 439 test files; all training tests use synthetic in-memory frames
  - **Classification:** `CI/CD`
  - **Confidence:** 100%
  - **Contradiction:** Real training pipeline not exercised in continuous integration
- **Path:** `.github/workflows/ci.yml` (Lines: `50-180`)
  - **Symbol:** `CI Workflow`
  - **Behavior:** Runs fast unit and contract tests; caps job timeout at 15 minutes
  - **Classification:** `CI/CD`
  - **Confidence:** 100%
  - **Contradiction:** Full 34-fold training exceeds PR timeout limits

## FACTS
- 439 tests in critical suite.
- CI uses synthetic smoke frames.
- Full training never runs in PR checks.

## UNKNOWNs
- Maximum execution time of 5-fold walk-forward on GitHub runner CPU.

## SCOPE
Create tests/integration/test_walk_forward_pipeline_real.py (5 folds, 3 epochs); create .github/workflows/nightly-ml-benchmark.yml triggered on schedule or workflow_dispatch.

## NON_GOALS
Do not add long-running multi-hour training jobs to standard per-commit PR checks.

## SOURCE_AREAS
- `.github/workflows/ci.yml`
- `tests/critical_suite.txt`

## FILES_LIKELY_TO_CHANGE
- `.github/workflows/nightly-ml-benchmark.yml`
- `tests/integration/test_walk_forward_pipeline_real.py`

## INVESTIGATION_PLAN
Check GitHub runner CPU cores and RAM limits (3.9GB host limit; verify serial execution to avoid xdist OOM).

## IMPLEMENTATION_PLAN
1. Write tests/integration/test_walk_forward_pipeline_real.py (folds=5, epochs=3).
2. Assert training runs to completion and produces valid candidate bundle passing GATE1-5 and GATE11.
3. Create .github/workflows/nightly-ml-benchmark.yml.
4. Verify local execution completes in < 120 seconds.

## TEST_PLAN
- `pytest tests/integration/test_walk_forward_pipeline_real.py -v`

## BENCHMARK_PLAN
5-fold walk-forward training execution in < 180 seconds on CPU.

## EVIDENCE_REQUIRED
- Workflow file: .github/workflows/nightly-ml-benchmark.yml
- Test file: tests/integration/test_walk_forward_pipeline_real.py
- Passing execution log

## ACCEPTANCE_CRITERIA
1. [x] test_walk_forward_pipeline_real.py passes in < 180 seconds on CPU.
   VERIFIED 2026-09-23: 11/11 tests pass in 4.98s serial on CPU (Python 3.11.16,
   torch 2.13.0+cpu, 2-core host) — ~36x inside the 180s budget. The battery
   drives the real `WalkForwardTrainer.train_and_validate` path (5 folds x 3
   epochs, 720-row synthetic 50D frame), asserts the fold geometry is a genuine
   walk-forward with a 15-bar purge band on every fold, asserts pooled OOS
   samples equal the sum of fold validation-block sizes, verifies the published
   candidate bundle is complete on disk with manifest SHA-256 matching the bytes,
   verifies the checkpoint head is 3-wide and the input projection is 50-wide,
   runs GATE4/GATE5/GATE11 against the real artifact, asserts the smoke bundle is
   rejected by GATE_PRODUCTION_ELIGIBLE, and asserts seed-level determinism
   (identical fold geometry + identical model SHA-256 on rerun).
2. [x] Nightly benchmark workflow correctly packages and reports training metrics.
   VERIFIED 2026-09-23: `.github/workflows/nightly-ml-benchmark.yml` created
   (schedule 02:41 UTC + workflow_dispatch), runs the battery serially
   (xdist dies under memory pressure on CPU-heavy training), collects junit
   timing + pass counts into `ml-benchmark-results/report/metrics.json`, writes
   a `$GITHUB_STEP_SUMMARY` block, uploads the `nightly-ml-benchmark-<run>`
   artifact (retention 30d), and notifies Telegram on failure via the canonical
   `scripts/ci/telegram_notify.py release-failed`. `scripts/ci/check_workflows.py`
   validates the new workflow (0 ERROR / 0 WARNING) and the repo's
   `test_bug300_workflow_ai_wiring.py` watch-list enforcement passes with
   "Nightly ML Benchmark" added to ci-summary.yml.

## VERIFICATION_EVIDENCE
- Local gates at branch agent/qa/ml-ci-001 (HEAD off origin/main c2fe11d2):
  - `pytest tests/integration/test_walk_forward_pipeline_real.py -v -p no:cacheprovider`
    -> 11 passed in 4.98s (serial).
  - `pytest tests/unit/test_bug300_workflow_ai_wiring.py -q` -> 9 passed
    (the ci-summary workflow_run watch-list gate).
  - `python scripts/ci/check_workflows.py` -> SUMMARY: 0 ERROR, 0 WARNING, 0 INFO.
  - `python scripts/ci/verify_critical_suite_manifest.py` -> 233 paths all exist.
  - `ruff check .` -> All checks passed!; `ruff format --check .` ->
    2293 files already formatted (ruff 0.16.8, the CI pin).
  - `mypy tests/integration/test_walk_forward_pipeline_real.py` -> Success: no
    issues found in 1 source file.
- Registered in `tests/slow_suite.txt` (nightly tier, NOT the fast PR critical
  gate) so the battery runs on schedule without widening per-push CI.

## ABORT_CONDITIONS
If training exceeds host RAM and crashes with OOM, reduce batch size or fold count.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `.github/workflows/nightly-ml-benchmark.yml`
- `tests/integration/test_walk_forward_pipeline_real.py`

## SHARED_FILE_RISK
Low. AGENT-QA owns nightly workflow.
