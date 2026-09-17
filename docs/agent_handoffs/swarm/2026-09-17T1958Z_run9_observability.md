# NSE-Swarm Run 9: Observability — BUG-303 (CI Notify Environment Repair & Observability Reporting)

- **Role**: 9 (Observability)
- **Run ID**: `2026-09-17T1958Z_run9_observability`
- **Defect ID**: `BUG-303` (P1 / Reliability & Observability)
- **Status**: FIXED-VERIFIED
- **Base Commit**: `f5ff4576` (`origin/main`)
- **Branch**: `swarm/r9-bug303-env` in `/tmp/nse-swarm-r9-current`

## 1. Defect & Root Cause Analysis

### Symptoms
Lanes calling `scripts/ci/telegram_notify.py` without pre-installing the project packages (e.g. JS Tests, Trivy scanner, SKIP-REPORT OS leg, ci-summary) died with `ModuleNotFoundError: No module named 'structlog'` at module import time. Because all workflow notification invocations are guarded with `|| true` and `continue-on-error: true`, the notification failure was silently swallowed — the workflow runs reported green but Telegram notifications were never sent and no diagnostics reached the channel.

### Root Cause
1. `scripts/ci/telegram_notify.py` unconditionally imported `from nexus_scalp.observability.ci_telegram_reporter import CITelegramReporter` at module scope.
2. In addition, `src/nexus_scalp/observability/__init__.py` eagerly imported `from nexus_scalp.observability.logging import configure_logging, get_logger`, which pulled in `structlog`. Any import from `nexus_scalp.observability.*` loaded `__init__.py`, instantly failing on uninstalled interpreters.
3. Distribution names with hyphens/case differences (`PyYAML` -> `yaml`, `pydantic-settings` -> `pydantic_settings`) were checked by distribution name directly in `__import__`, causing false positives in dependency checks.

## 2. Solution & Seams Changed

1. **Stdlib-only Environment Probe** (`src/nexus_scalp/observability/ci_env_probe.py`):
   - Pure stdlib implementation probing for required dependencies (`structlog`, `pydantic`, `pydantic-settings`, `PyYAML`, `numpy`) with `MODULE_MAP` translation.
   - Returns structured dictionary with `python_version`, `imports` map, and `missing` list.

2. **Lazy Observability Subsystem Init** (`src/nexus_scalp/observability/__init__.py`):
   - Converted eager `logging` import to lazy `__getattr__` accessor, ensuring that stdlib-only submodules like `ci_env_probe` can be imported even when `structlog` is absent.

3. **Pre-Import Self-Healing & Safe Fallback** (`scripts/ci/telegram_notify.py`):
   - Probes the interpreter before importing `CITelegramReporter`.
   - If dependencies are missing and `--no-env-repair` is not set, executes `pip install --user` in the current interpreter.
   - Wraps `CITelegramReporter` import in `try...except` and emits structured JSON with category `ENV_IMPORT_FAILED` (including diagnosis, remedy, and env probe status) instead of an unhandled traceback.

4. **CI Heartbeat & Lane Reporting** (`ci_heartbeat.py`, `ci_lane_report.py`):
   - `ci_heartbeat.py`: Stdlib+repo AI endpoint connectivity probe (TCP + HTTP /v1/models check), fail-safe with structured verdict (`NO_ENDPOINT_CONFIGURED`, `TCP_FAILED`, `REACHABLE`).
   - `ci_lane_report.py`: Advisory summary writer producing `GITHUB_STEP_SUMMARY` Markdown, `ci-results/run-info/lane-report.json`, and optional Telegram block.

5. **Static Workflow Validation** (`scripts/ci/check_workflows.py`):
   - Added `validate_lane_report_step(wf)` to validate that lane-report steps adhere to the required contract (`continue-on-error: true`, `if: always()`, `|| true`).

6. **Lockfile Synchronization** (`requirements.lock`):
   - Regenerated lockfile reflecting upstream PyPI release of `idna==3.20` per `check_dependency_drift.py`.

## 3. Verification & Evidence

- **New Test Battery**: `tests/unit/test_bug303_ci_notify_env_and_report.py` (9 tests, all passing):
  - `test_bug303_env_probe_stdlib_only`: Verifies stdlib-only probe runs without third-party dependencies.
  - `test_bug303_cli_no_env_repair_skips_probe`: Verifies `--no-env-repair` prevents pip invocation.
  - `test_bug303_cli_env_repair_installs_missing_deps`: Verifies missing dependencies trigger pip repair.
  - `test_bug303_cli_ship_shapes`: Verifies all 13 shipped CLI command shapes parse and return exit code 0.
  - `test_bug303_import_failure_payload`: Verifies structured `ENV_IMPORT_FAILED` payload generation.
  - `test_bug303_heartbeat_unconfigured`: Verifies unconfigured AI endpoint returns `NO_ENDPOINT_CONFIGURED`.
  - `test_bug303_heartbeat_connection_failure`: Verifies unreachable host returns `TCP_FAILED`.
  - `test_bug303_lane_report`: Verifies JSON artifact and Markdown step summary creation.
  - `test_bug303_check_workflows_validate_lane_report`: Verifies contract checker against synthetic workflows.
- **Neighbor Suites**:
  - `test_bug289_telegram_notify_cli.py`: 5 passed
  - `test_check_workflows.py`: 18 passed
  - `test_bug300_ai_triage_fallback.py`: 11 passed
  - `test_bug300_workflow_ai_wiring.py`: 9 passed
  - Total combined slice: 52 passed in 1.60s.
- **Linters and Type Checks**:
  - `ruff check .`: All checks passed!
  - `ruff format --check .`: 2104 files already formatted.
  - `mypy src`: Success: no issues found in 607 source files.
  - `python3 scripts/ci/check_dependency_drift.py`: OK (98 pins match).
  - `python3 scripts/ci/check_workflows.py --strict`: 0 ERROR, 0 WARNING, 0 INFO.
  - `tests/critical_suite.txt` manifest validation: 0 missing files.
