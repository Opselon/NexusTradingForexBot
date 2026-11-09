"""PR-policy regression: wall-clock budget tests never run under coverage.

Policy (2026-11 audit, area 4): a test that asserts a WALL-CLOCK budget
(``perf_counter_ns``) must not execute in a coverage-instrumented,
parallel pytest run. Measured 2026-11-09: the payload value-gate costs
~23 us/call isolated with or without coverage, but coverage + xdist on a
loaded runner inflates the same measurement to 72 us (4-CPU VM under
load) and 158 us in CI on the 2-core tier-2 box — past the 100 us floor.
Under coverage + parallelism the assertion measures the scheduler, not
the gate. The rule is therefore structural, not a test-code tweak:

* every coverage-instrumented pytest invocation deselects the
  wall-clock node;
* the node runs in a dedicated step in the SAME job/lane — uninstrumented
  (no ``--cov``), single-process (no ``-n``), blocking;
* the other tests in the file are pure correctness assertions and stay in
  the coverage run.

These tests pin the workflow wiring so a future edit cannot silently
re-instrument the budget assertion (which would re-open the 9715a4ae
Quality-lane failure) or silently drop it (which would delete the
protection entirely).
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(".github/workflows")

# The only wall-clock budget assertion in the critical suite. The node id
# is pinned exactly: a rename in the test file must update the workflows
# (and this test) together, never silently stop matching the deselect.
WALL_CLOCK_NODE = (
    "tests/unit/news/ingest/test_payload_value_gate_benchmark.py"
    "::test_gate_cost_is_far_cheaper_than_the_write_it_prevents"
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


def test_ci_quality_tests_deselects_wall_clock_node_from_coverage_run() -> None:
    """ci.yml quality-tests: the planner run deselects the budget node."""
    ci = _load("ci.yml")
    steps = _steps(ci["jobs"]["quality-tests"])
    planner = _named(steps, "Planner-driven pytest")
    run = _run(planner)
    assert "--cov=src" in run, "quality-tests planner run must stay coverage-instrumented"
    assert f"--deselect {WALL_CLOCK_NODE}" in run, (
        "quality-tests planner run must deselect the wall-clock budget node"
    )


def test_ci_quality_tests_has_uninstrumented_blocking_budget_step() -> None:
    """ci.yml quality-tests: the budget node has its own uninstrumented step."""
    ci = _load("ci.yml")
    steps = _steps(ci["jobs"]["quality-tests"])
    budget = _named(steps, "Performance budget")
    run = _code(_run(budget))
    assert WALL_CLOCK_NODE in run
    assert "--cov" not in run, "budget step must run WITHOUT coverage instrumentation"
    assert '"-n"' not in run and " -n " not in run, "budget step must run single-process"
    assert "perf_budget" in _run(budget), "budget step must record a perf_budget check"
    assert 'exit "$rc"' in run, "budget step must fail the job when the budget trips"


def test_ci_quality_tests_reports_perf_budget_status() -> None:
    """ci.yml quality-tests: step summary + review-status include perf_budget."""
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert '"Perf budget": load("perf_budget.json")' in text
    assert '"perf_budget"' in text


def test_os_full_runs_deselect_wall_clock_node_in_both_legs() -> None:
    """tests-os.yml: both FULL legs deselect the node from the coverage run."""
    os_wf = _load("tests-os.yml")
    for leg in ("tests-linux-self-hosted", "tests-linux-hosted"):
        steps = _steps(os_wf["jobs"][leg])
        full = _named(steps, "Run Full Critical Suite")
        run = _run(full)
        assert "--cov=src" in run, f"{leg} FULL run must stay coverage-instrumented"
        assert f"'--deselect', '{WALL_CLOCK_NODE}'" in run, (
            f"{leg} FULL run must deselect the wall-clock budget node"
        )


def test_os_full_runs_have_uninstrumented_blocking_budget_step() -> None:
    """tests-os.yml: both legs run the node uninstrumented, FULL-mode only."""
    os_wf = _load("tests-os.yml")
    for leg in ("tests-linux-self-hosted", "tests-linux-hosted"):
        steps = _steps(os_wf["jobs"][leg])
        budget = _named(steps, "Performance budget")
        assert budget.get("if") == "steps.lane.outputs.mode == 'FULL'", (
            f"{leg} budget step must be FULL-mode only"
        )
        run = _code(_run(budget))
        assert WALL_CLOCK_NODE in run
        assert "--cov" not in run, f"{leg} budget step must run WITHOUT coverage"
        assert '"-n"' not in run and " -n " not in run, f"{leg} budget step must run single-process"
        assert 'exit "$rc"' in run, f"{leg} budget step must fail the job on a trip"


def test_os_skew_runs_do_not_deselect_the_node() -> None:
    """tests-os.yml: SKEW runs keep the node — they carry no --cov.

    The SKEW planner run is uninstrumented, so the budget assertion already
    runs where its measurement means something; deselecting it there would
    leave the dedicated FULL-only step as its only home and the assertion
    would silently not run on PRs at all.
    """
    os_wf = _load("tests-os.yml")
    for leg in ("tests-linux-self-hosted", "tests-linux-hosted"):
        steps = _steps(os_wf["jobs"][leg])
        skew = _named(steps, "Run C-planned targeted subset")
        run = _run(skew)
        assert "--cov" not in run, f"{leg} SKEW run must stay uninstrumented"
        assert WALL_CLOCK_NODE not in run, (
            f"{leg} SKEW run must NOT deselect the node (it is the PR home)"
        )
