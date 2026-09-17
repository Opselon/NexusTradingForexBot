# ML-CI-001 — CI Model Training Smoke vs Real Validation Gap & Nightly Harness

STREAM: STREAM L — OBSERVABILITY
PRIORITY: P2
STATUS: BLOCKED
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
1. test_walk_forward_pipeline_real.py passes in < 180 seconds on CPU.
2. Nightly benchmark workflow correctly packages and reports training metrics.

## ABORT_CONDITIONS
If training exceeds host RAM and crashes with OOM, reduce batch size or fold count.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `.github/workflows/nightly-ml-benchmark.yml`
- `tests/integration/test_walk_forward_pipeline_real.py`

## SHARED_FILE_RISK
Low. AGENT-QA owns nightly workflow.
