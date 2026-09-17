# TASK-013 — CI Model Training Smoke vs Real Validation Gap & Benchmark Harness

Priority: P2
Status: EXECUTABLE AFTER TASK-002, TASK-005
Type: CI/CD & Testing Infrastructure
Dependencies: TASK-002, TASK-005
Blocks: None
Human Decision Required: NO
Risk: Low (Dedicated CI job; zero impact on standard PR fast-path)
Estimated Scope: New GitHub Actions workflow + benchmark test script (~150 LOC)

## Objective
Bridge the gap between fast CI synthetic smoke tests and full production candidate training by creating an automated, scheduled benchmark harness that exercises real multi-fold walk-forward training.

## Problem / Why
Current CI runs only synthetic smoke tests (SMOKE_MIN_ROWS=3000) to keep PR checks fast. Full walk-forward candidate training is never exercised in CI, meaning syntax regressions, gate evaluation bugs, or tensor shape mismatches in training pipelines can slip undetected into main.

## Current Evidence
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

## Scope
1. Create a lightweight benchmark test tests/integration/test_walk_forward_pipeline_real.py that runs a 5-fold walk-forward candidate training run on cached data.
2. Assert that all 12 gates are evaluated and return valid GateResult structures.
3. Create .github/workflows/nightly-ml-benchmark.yml triggered on schedule (weekly/nightly) or manual workflow_dispatch.

## Non-Goals
Do not add long-running multi-hour training jobs to standard per-commit PR checks.

## Preconditions
TASK-002 and TASK-005 complete.

## Dependencies
TASK-002, TASK-005

## Blocks
None

## Source Areas
- `.github/workflows/ci.yml:1-200`
- `tests/critical_suite.txt:1-440`
- `src/nexus_scalp/training/walk_forward_trainer.py:150-300`

## Investigation
Check runner CPU cores and RAM limits on GitHub Actions runners (3.9GB RAM host limit on standard runners; verify serial execution to avoid xdist OOM).

## Implementation Plan
1. Write tests/integration/test_walk_forward_pipeline_real.py configuring folds=5, epochs=3, patience=1.
2. Assert training runs to completion and produces valid candidate bundle passing GATE1 through GATE5 and GATE11.
3. Create .github/workflows/nightly-ml-benchmark.yml with workflow_dispatch and scheduled trigger.
4. Run test locally and verify completion within 120 seconds.

## Tests
- `pytest tests/integration/test_walk_forward_pipeline_real.py -v`

## Validation / Benchmark
5-fold walk-forward training completes cleanly, evaluates gates, and produces valid artifact bundle.

## Evidence Required
- Workflow file: .github/workflows/nightly-ml-benchmark.yml
- Test file: tests/integration/test_walk_forward_pipeline_real.py
- Passing execution log with gate evaluation results

## Acceptance Criteria
1. test_walk_forward_pipeline_real.py passes in < 180 seconds on CPU.
2. Nightly benchmark workflow correctly packages and reports training metrics.

## Failure / Abort Conditions
If training exceeds host RAM and crashes with SIGKILL / OOM, reduce batch size or fold count.

## Human Stop Conditions
None.

## Expected Output
Automated regression testing pipeline verifying full candidate training health.
