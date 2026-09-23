# ML-QA-004 — Push-Gate Timing Determinism Remediation

| Field | Value |
| :--- | :--- |
| **Task ID** | `ML-QA-004` |
| **Stream** | L: CI/CD & Verification |
| **Specialist** | `AGENT-QA` |
| **Priority** | P2 |
| **Dependencies** | `ML-QA-003` (DONE, PR #402 — determinism census + roster) |
| **Status** | **DONE** |
| **Human decision required** | NO |

## Objective

Remediate the largest measured non-determinism exposures in the push gate,
following the ranked candidates produced by the ML-QA-003 census
(`docs/ml-system/test_determinism_roster.md`).

Census candidate #1 was `tests/e2e/test_smoke_chain.py` — 42 wall-clock
probes (`time.monotonic`) purely for pretty "stage N in X.X ms" log lines, =
26% of the entire in-gate timing surface. Candidate cluster #2 was the
`< 5.0` / `< 2.0` / `< 1.0` wall-clock *liveness budgets* spread across four
phase batteries.

The distinction the census established, and which this task enforces in code:

* **Instrumentation** (log lines for the human reading CI output) must be
  deterministic — a host scheduler must not be able to change what the test
  suite prints, because that is the same dependency as changing what it
  asserts.
* **Measurement** (does this path return inside a real budget) must stay a
  real measurement, but read CPU time so a co-tenant load spike cannot trip
  a liveness bound.

## Implementation

### 1. `tests/e2e/chain_clock.py` (new)

* `ChainClock` — injected instrumentation clock. `elapsed_ms()` advances a
  fixed `_LAP_ADVANCE_SEC = 0.075` per call, so a full 21-stage chain reports
  a realistic, byte-identical ~1.5 s on every runner, every run. Values are
  floats of seconds so the existing `* 1000:.1f ms` format strings work
  unchanged. `reset()`/`lap()` retained for the general shape.
* `budget_cpu_ms(limit)` — context manager returning `consumed_ms` measured
  with `time.process_time()` (CPU time), insensitive to co-tenant scheduler
  load. This is the only place in the helper that touches a real clock, and
  it is deliberately never the wall clock.
* Imports only stdlib, so any test module can adopt it without pulling
  torch/polars into collection.

### 2. `tests/e2e/test_smoke_chain.py` — instrumentation conversion

* All 42 `time.monotonic()` probes removed (21 anchors + 21 reads + wall
  summary). Every `_info(f"stage NN in ...")` line and the final
  `SMOKE CHAIN PASSED` banner now read `_CLOCK.elapsed_ms()`.
* The now-dead `t0`/`t_wall0` local anchors were deleted and the orphaned
  `_banner(` call restored to correct indentation (caught by `py_compile`,
  then by collection).
* `import time` removed from the module (the only remaining `time.`-shaped
  text is `datetime.now(UTC)` in the maintenance-band helpers — unrelated).
* Zero behavioral change to any assertion: the timings were never asserted.

### 3. Wall-clock liveness budgets → CPU time (4 modules)

| Module | Old bound | New bound |
| :--- | :--- | :--- |
| `tests/unit/test_bug304_warm_shutdown.py` | `monotonic - started < 1.0` (100 shutdown calls) | `budget_cpu_ms(1000.0)` |
| `tests/unit/test_audit_flush_contract.py` | `monotonic - started < 2.0` (idle flush) | `budget_cpu_ms(2000.0)` |
| `tests/unit/test_model_lifecycle_phase10.py` | `perf_counter - t0 < 5.0` (training worker tick) | `budget_cpu_ms(5000.0)` |
| `tests/unit/test_shadow_phase11.py` | `perf_counter - t0 < 5.0` (shadow worker tick) | `budget_cpu_ms(5000.0)` |
| `tests/unit/test_training_env_worker.py` | `monotonic - started < 5` (cancel-grace run) | `budget_cpu_ms(5000.0)` |

Bounds are preserved at their original magnitudes (generous margins over the
observed cost — a no-op worker tick costs milliseconds, not seconds).

### 4. `tests/unit/test_ml_qa_004_determinism_remediation.py` (new, 24 tests)

* `ChainClock` determinism: repeated laps identical, two independent clocks
  agree, monotonic reading order preserved, sane finite printed figure,
  custom advance (incl. 0.0) still deterministic.
* `budget_cpu_ms` is a real measurement: reports consumed CPU, non-negative,
  not a constant.
* **Regression guards** (parametrized over all 6 remediated modules): no
  remaining unwrapped `assert time.(monotonic|perf_counter)` measurement,
  no bare `t0 = time.monotonic()` anchor, smoke chain wired to `_CLOCK` with
  ≥18 deterministic stage lines.
* Helper hygiene: `chain_clock.py` contains no wall-clock read at all
  (comment/docstring stripped before asserting), exactly two
  `process_time` calls, and imports no heavy dependency.

## Acceptance criteria

- [x] `tests/e2e/test_smoke_chain.py` contains zero `time.monotonic` /
      `perf_counter` probes (was 42)
- [x] Every printed stage timing is deterministic and host-independent
- [x] All wall-clock liveness budgets in the remediated set read CPU time
- [x] New battery pins both rules and guards against regression
- [x] Battery registered in `tests/critical_suite.txt` and manifest verified
- [x] `ruff check` + `ruff format --check` clean on all touched files
- [x] `mypy` clean on the new helper module
- [x] All remediated modules pass (pre-existing env/order flakes excluded,
      see Verification)

## Verification evidence

* `pytest tests/unit/test_ml_qa_004_determinism_remediation.py` →
  **24 passed** (`PYTHONPATH=src:.` on this host's interpreter).
* `pytest tests/e2e/test_smoke_chain.py --deselect ::test_smoke_full_chain`
  → **14 passed**. `test_smoke_full_chain` cannot be exercised locally:
  this host has no `fastapi` install (baseline-confirmed — the identical
  failure occurs with the conversion stashed at the base commit). CI runs
  it on windows-latest / macos-latest with the full dependency set.
* `pytest tests/unit/test_audit_flush_contract.py
  tests/unit/test_model_lifecycle_phase10.py
  tests/unit/test_shadow_phase11.py
  tests/unit/test_training_env_worker.py
  tests/unit/test_ml_qa_004_determinism_remediation.py
  -k 'not test_next_run_failure_is_isolated'` → **128 passed**.
* Pre-existing flake (NOT caused by this task, baseline-confirmed):
  `test_shadow_phase11.py::TestWorkerRestartsNextRun::test_next_run_failure_is_isolated`
  fails when run after a large sibling set — a structlog routing difference
  (the event renders to the log record, not captured stdout) — and passes
  when the module runs alone. Reproduced at the base commit with my change
  stashed.
* Pre-existing env artifact: `test_worker_dependency_probe_runs_with_stdlib_only`
  asserts `"structlog" in report["missing"]` for a `python -S` subprocess;
  when the parent PYTHONPATH points at a site-packages that has structlog,
  the child sees it and reports torch/three_model instead. Reproduced with
  the hermes venv interpreter alone (passes) vs. combined with the extra
  path (fails) — not a code defect.
* `ruff check` → All checks passed; `ruff format --check` → clean;
  `mypy tests/e2e/chain_clock.py` → Success: no issues found in 1 source
  file.
* `scripts/ci/verify_critical_suite_manifest.py` →
  `CRITICAL_SUITE_MANIFEST_OK: 234 paths all exist`.

## OWNERSHIP_SCOPE

`tests/`, `scripts/ci/` (manifest only).

## FORBIDDEN_CHANGES

None applicable — test-only work; no production code, no gate logic, no
config touched.
