# CI Reliability Findings — Enterprise GitHub Actions Modernization Input

Status: RECORDED (separate from BUG-120, which is DONE/verified at 2d7a295)
Owner: Hermes-CI-Ready (next task — .github/workflows/** ONLY, no application code)
Date: 2026-08-19

## Context
BUG-120 (Incident Center) is verified complete at 2d7a295 and is NOT part
of this work. The findings below are pre-existing CI/reliability issues
observed during the BUG-120 verification run and earlier gates. They are
recorded here as input for the GitHub Actions modernization task and must
NOT be "fixed" by weakening tests or changing application behavior.

## Findings

### F1. Pre-existing full-suite order-dependent flakiness
- Symptom: `pytest tests/unit/ -q` full-suite run fails 4 tests that each
  PASS when run in isolation:
  - tests/unit/test_model_lifecycle_phase10.py
    ::TestCompatibility::test_bug118_champion_verified_logs_once_per_fingerprint
    ::TestCompatibility::test_bug118_artifact_rewrite_reverifies_once
    ::TestCompatibility::test_bug118_cold_start_none_memoized
  - tests/unit/test_web_security.py
    ::TestSanitizedResponses::test_06_server_log_contains_detailed_exception
- Cause class: cross-test logger/state leakage (champion fingerprint cache,
  capsys/caplog interplay, logging handler residue between suites), NOT a
  logic defect. Isolated runs are green (verified 53/53 + 2/2 on rerun).
- RECOMMENDATION (CI side, NOT app side): split unit suite into stable +
  isolation-needed groups (pytest markers or explicit file ordering), apply
  pytest-xdist/process isolation for the logger-sensitive files, or accept
  documented flakes with retry-once for those exact node ids. Do NOT weaken
  the assertions or silence the logger in app code.

### F2. bug118 / web_security / 70D-latency parallel-WIP interactions
- The three failing files above belong to parallel-task WIP that shares
  global state (frozen model fingerprint cache, Telemetry/logger
  singletons, latency probes). Their failures appear/disappear with the
  presence of unrelated suites in the same process.
- RECOMMENDATION: isolate by workflow matrix (run these files in a separate
  pytest shard with `--forked` or a fresh process), so a flaky sibling can
  never red-X the whole unit gate.

### F3. Cross-file / environment-dependent failures
- Observed: tests that depend on machine state (settings DB path,
  app_settings.db isolation, MT5 presence, wall-clock windows) can fail on
  the runner but pass locally, or vice versa. E.g. tests writing the real
  user settings DB; latency probes measuring CI-host noise.
- RECOMMENDATION: per-run env fixtures are already landing (conftest
  auto-isolation); CI should assert the fixture is applied (e.g. run a
  canary test asserting NEXUS_SETTINGS_DB is set before the suite) and
  tag environment-sensitive tests so they run in a dedicated job with
  generous timeouts.

## Boundary (what this task may NOT touch)
- No application code (src/, Web/ JS logic, DB, incident/diagnostics/trading/
  MT5). Workflow/config/scripts/ci/* + registries only.
- No weakening of tests, no changing app behavior to "fix" flakes.
- BUG-120 files: NOT to be refactored.

## Deliverables for the next task (from user briefing)
.github/workflows/** — CI/release automation, artifact orchestration, test
result aggregation, timeouts, retries, concurrency, GitHub API-safe
behavior, release lifecycle, artifact verification, GitHub Actions
summaries, Telegram HTML observability.