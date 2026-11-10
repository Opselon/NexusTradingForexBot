"""PR-policy regression: wall-clock budget tests never run under coverage.

Policy (2026-11 audit, area 4): a test that asserts a WALL-CLOCK budget
(``perf_counter`` / ``perf_counter_ns``) must not execute in a
coverage-instrumented, parallel pytest run. Measured 2026-11-09: the
payload value-gate cost ~23 us/call isolated with or without coverage,
but coverage + xdist on a loaded runner inflated the same measurement to
72 us (4-CPU VM under load) and 158 us in CI on the 2-core tier-2 box —
past the 100 us floor. (The gate itself was later optimized to ~0.6
us/call, so the isolated number is now far below the floor — but the
measurement distortion under coverage + parallelism remains, which is
why the rule is structural, not a test-code tweak.) Under coverage +
parallelism the assertion measures the scheduler, not the code. The
model-promotion SLA node has the same shape: a single-shot wall-clock
assert (500 ms x 1.25 jitter headroom = 625 ms) over a real DB write,
which its own comment admits already measured 511 ms once.

The rule is therefore:

* every coverage-instrumented pytest invocation deselects the
  wall-clock nodes;
* the nodes run in a dedicated step in the SAME job/lane — uninstrumented
  (no ``--cov``), single-process (no ``-n``), blocking;
* the other tests in the gate file are pure correctness assertions and
  stay in the coverage run; the promotion node's functional assertions
  (commit, audit rows) travel with it into the dedicated step, so
  nothing is lost — the node simply runs uninstrumented.

These tests pin the workflow wiring so a future edit cannot silently
re-instrument a budget assertion (which would re-open the 9715a4ae
Quality-lane failure) or silently drop one (which would delete the
protection entirely). A new wall-clock budget assertion anywhere in the
critical suite must be added to WALL_CLOCK_NODES here AND to the
deselect + dedicated step in every coverage lane.

TEMPORARY (2026-11-10, user-approved): the Quality planner run is
uninstrumented except on a manual full dispatch (COV_FLAGS is empty
unless inputs.full=true), so the suite fits the cap on the shared
tier-1 box without the coverage tracer. The rule above still binds
every instrumented invocation (manual full dispatch); the budget nodes
still run in the dedicated uninstrumented step on every PR.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(".github/workflows")

# Every wall-clock budget assertion in the critical suite. The node ids
# are pinned exactly: a rename in a test file must update the workflows
# (and this test) together, never silently stop matching the deselect.
# NOTE (review 2026-11-09): an earlier version of this file claimed the
# gate node was the ONLY such assertion — false. The promotion-transaction
# SLA node below asserts a wall-clock budget too, so it is pinned here
# like the gate node.
WALL_CLOCK_NODES = (
    "tests/unit/news/ingest/test_payload_value_gate_benchmark.py"
    "::test_gate_cost_is_far_cheaper_than_the_write_it_prevents",
    "tests/integration/test_model_promotion_pipeline_e2e.py"
    "::test_promotion_transaction_happy_path_and_benchmark",
)


def _load(name: str) -> dict:
    with open(WORKFLOWS / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _steps(job: dict) -> list[dict]:
    return [s for s in job.get("steps", []) if isinstance(s, dict)]


def _named(steps: list[dict], fragment: str) -> dict:
    for step in steps:
        if fragment in str(step.get("name", "")):
            return step
    raise AssertionError(f"no step matching {fragment!r}")


def _run(step: dict) -> str:
    return str(step.get("run", ""))


def _code(run: str) -> str:
    """The run block minus full-line comments (policy prose mentions flags)."""
    return "\n".join(line for line in run.splitlines() if not line.strip().startswith("#"))


def test_ci_quality_tests_deselects_wall_clock_nodes_from_coverage_run() -> None:
    """ci.yml quality-tests: the planner run deselects the budget nodes.

    Coverage is MANUAL-ONLY (2026-11-10): the planner run carries --cov
    only on a manual full dispatch (inputs.full=true), via COV_FLAGS —
    not on PR/push runs and not on the schedule. The deselects stay in
    every mode — on a manual full run the planner run IS instrumented,
    so the budget nodes must be deselected from it; on PR runs they
    still run in the dedicated uninstrumented budget step.
    """
    ci = _load("ci.yml")
    steps = _steps(ci["jobs"]["quality-tests"])
    planner = _named(steps, "Planner-driven pytest")
    run = _run(planner)
    # --cov exists only behind the manual-dispatch gate (COV_FLAGS); the
    # --pytest-extra itself must not hardcode coverage.
    assert 'COV_FLAGS=""' in run, "planner run must default to no coverage (PR runs)"
    assert 'if [ "${{ inputs.full }}" = "true" ]; then COV_FLAGS="--cov=src' in run, (
        "planner run must stay coverage-instrumented on manual full dispatch only"
    )
    extra = next(line for line in run.splitlines() if line.strip().startswith("--pytest-extra"))
    assert "$COV_FLAGS" in extra, "planner run must pass coverage via COV_FLAGS"
    assert "--cov" not in extra, "planner run must not hardcode --cov in --pytest-extra"
    for node in WALL_CLOCK_NODES:
        assert f"--deselect {node}" in run, (
            f"quality-tests planner run must deselect the wall-clock budget node {node}"
        )


def test_ci_quality_tests_has_uninstrumented_blocking_budget_step() -> None:
    """ci.yml quality-tests: the budget nodes have their own uninstrumented step."""
    ci = _load("ci.yml")
    steps = _steps(ci["jobs"]["quality-tests"])
    budget = _named(steps, "Performance budget")
    run = _code(_run(budget))
    for node in WALL_CLOCK_NODES:
        assert node in run, f"budget step must run the wall-clock node {node}"
    assert "--cov" not in run, "budget step must run WITHOUT coverage instrumentation"
    assert '"-n"' not in run and " -n " not in run, "budget step must run single-process"
    assert "perf_budget" in _run(budget), "budget step must record a perf_budget check"
    assert 'exit "$rc"' in run, "budget step must fail the job when the budget trips"


def test_ci_quality_tests_budget_step_has_no_skip_guard() -> None:
    """ci.yml quality-tests: the budget step cannot be skipped or tolerated.

    The step must have no ``if:`` (it runs in every mode) and no
    ``continue-on-error`` (a tripped budget must fail the job). A guard
    here would silently drop the wall-clock protection.
    """
    ci = _load("ci.yml")
    steps = _steps(ci["jobs"]["quality-tests"])
    budget = _named(steps, "Performance budget")
    assert "if" not in budget, "budget step must have no if: guard"
    assert "continue-on-error" not in budget, "budget step must not have continue-on-error"


def test_ci_quality_tests_reports_perf_budget_status() -> None:
    """ci.yml quality-tests: step summary + review-status include perf_budget."""
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert '"Perf budget": load("perf_budget.json")' in text
    assert '"perf_budget"' in text


def test_os_full_runs_deselect_wall_clock_nodes_in_both_legs() -> None:
    """tests-os.yml: both FULL legs deselect the nodes from the coverage run."""
    os_wf = _load("tests-os.yml")
    for leg in ("tests-linux-self-hosted", "tests-linux-hosted"):
        steps = _steps(os_wf["jobs"][leg])
        full = _named(steps, "Run Full Critical Suite")
        run = _run(full)
        assert "--cov=src" in run, f"{leg} FULL run must stay coverage-instrumented"
        for node in WALL_CLOCK_NODES:
            assert f"'--deselect', '{node}'" in run, (
                f"{leg} FULL run must deselect the wall-clock budget node {node}"
            )


def test_os_full_runs_have_uninstrumented_blocking_budget_step() -> None:
    """tests-os.yml: both legs run the nodes uninstrumented, FULL-mode only."""
    os_wf = _load("tests-os.yml")
    for leg in ("tests-linux-self-hosted", "tests-linux-hosted"):
        steps = _steps(os_wf["jobs"][leg])
        budget = _named(steps, "Performance budget")
        assert budget.get("if") == "steps.lane.outputs.mode == 'FULL'", (
            f"{leg} budget step must be FULL-mode only"
        )
        assert "continue-on-error" not in budget, (
            f"{leg} budget step must not have continue-on-error"
        )
        run = _code(_run(budget))
        for node in WALL_CLOCK_NODES:
            assert node in run, f"{leg} budget step must run the wall-clock node {node}"
        assert "--cov" not in run, f"{leg} budget step must run WITHOUT coverage"
        assert '"-n"' not in run and " -n " not in run, f"{leg} budget step must run single-process"
        assert 'exit "$rc"' in run, f"{leg} budget step must fail the job on a trip"


def test_os_skew_runs_do_not_deselect_the_nodes() -> None:
    """tests-os.yml: SKEW runs keep the nodes — they carry no --cov.

    The SKEW planner run is uninstrumented, so the budget assertions run
    where their measurements mean something. SKEW is dispatch-only now
    (2026-11-09: Quality owns test execution on PRs, one execution per
    PR); the nodes' PR home is the Quality budget step in ci.yml. The
    assertion still holds for dispatch SKEW runs: deselecting the nodes
    there would leave the dedicated FULL-only step as their only home
    outside PRs.
    """
    os_wf = _load("tests-os.yml")
    for leg in ("tests-linux-self-hosted", "tests-linux-hosted"):
        steps = _steps(os_wf["jobs"][leg])
        skew = _named(steps, "Run C-planned targeted subset")
        run = _run(skew)
        assert "--cov" not in run, f"{leg} SKEW run must stay uninstrumented"
        for node in WALL_CLOCK_NODES:
            assert node not in run, (
                f"{leg} SKEW run must NOT deselect the node {node} (dispatch home)"
            )
