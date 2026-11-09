"""PR-policy regression: ONE execution per PR — no duplicate test runs.

Design correction (2026-11-09, simplify): the mechanism has exactly one
job — the C engine finds the tests related to the changed files, Python
runs them, done. The same tests must NOT run back-to-back across the
Quality + OS lanes on one PR. Ownership:

* Quality (ci.yml quality-tests) owns test execution on PRs: it runs the
  C-planned selection, with coverage, on the routed runner.
* The OS Linux legs (tests-os.yml) SKIP on pull_request / push /
  schedule — they used to re-run the SAME selection (on this PR: the
  same 353 files in Quality AND in the OS lane, ~24 min + ~17 min for
  one PR). The expanded/full suite runs ONCE: on schedule in Quality,
  on explicit dispatch in the OS lane (FULL) or as a targeted subset
  (SKEW). SKEW survives only as the manual dispatch shape.
* Inside the Quality job, a test file also runs at most once: the smoke
  chain file is owned by the dedicated smoke step (it runs on every PR
  regardless of selection and gates via the `smoke` check + final gate),
  so the planner run deselects it; the wall-clock budget nodes are
  owned by the dedicated budget step (see test_pr_policy_benchmark_lane).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
SMOKE_FILE = "tests/e2e/test_smoke_chain.py"
DECIDE = REPO / "scripts" / "ci" / "decide_os_lane.bash"


def _load(name: str) -> dict:
    with open(WORKFLOWS / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _steps(job: dict) -> list:
    return job.get("steps", [])


def _named(steps: list, fragment: str) -> dict:
    for step in steps:
        if fragment in str(step.get("name", "")):
            return step
    raise AssertionError(f"no step matching {fragment!r}")


def _run(step: dict) -> str:
    return step.get("run", "")


def _decide_mode(event: str) -> str:
    """Run decide_os_lane.bash with the given event; return its mode."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "gh-out.txt"
        env = dict(os.environ)
        env["GITHUB_EVENT_NAME"] = event
        env["GITHUB_OUTPUT"] = str(out)
        env.pop("GITHUB_BASE_REF", None)
        env.pop("GITHUB_EVENT_BEFORE", None)
        env.pop("NSE_FORCE_FULL_SUITE", None)
        proc = subprocess.run(
            ["bash", str(DECIDE), "linux"],
            cwd=str(REPO),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, f"decide script failed for {event}: {proc.stderr}"
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.startswith("mode="):
                return line.split("=", 1)[1]
    raise AssertionError(f"decide script wrote no mode for {event}")


def test_quality_planner_deselects_the_smoke_file() -> None:
    """The planner run must not re-run the smoke-chain file.

    tests/e2e/test_smoke_chain.py is in critical_suite.txt, so on a
    global-invalidation PR the planner selects it — and the dedicated
    smoke step below runs it again. One execution: the smoke step owns
    it, the planner run deselects it.
    """
    wf = _load("ci.yml")
    steps = _steps(wf["jobs"]["quality-tests"])
    planner = _named(steps, "Planner-driven pytest (C17 only — sole planner)")
    assert f"--deselect {SMOKE_FILE}" in _run(planner), (
        "planner run must deselect the smoke-chain file (owned by the smoke step)"
    )


def test_smoke_step_still_runs_and_gates_the_file() -> None:
    """The smoke step runs the file on every PR and gates via `smoke`."""
    wf = _load("ci.yml")
    steps = _steps(wf["jobs"]["quality-tests"])
    smoke = _named(steps, "End-to-end smoke chain")
    assert SMOKE_FILE in _run(smoke), "smoke step must run the smoke-chain file"
    assert "make_ci_results.py check" in _run(smoke) and "smoke" in _run(smoke), (
        "smoke step must record a `smoke` check for the final gate"
    )
    gate = _named(steps, "Fail job if any check failed (fail-closed)")
    assert "smoke" in _run(gate), "final gate must verdict the `smoke` check"


def test_os_lane_skips_on_automatic_events() -> None:
    """decide_os_lane.bash: SKIP-REPORT on PR / push / schedule.

    Quality owns test execution on those events; the OS legs must not
    re-run the same selection. The mode is decided before the classifier
    block, so this does not depend on the diff at all.
    """
    for event in ("pull_request", "push", "schedule"):
        assert _decide_mode(event) == "SKIP-REPORT", (
            f"OS lane must SKIP-REPORT on {event} (Quality owns execution)"
        )


def test_os_lane_dispatch_escape_hatch_still_exists() -> None:
    """Dispatch keeps FULL (force) / SKEW (targeted) — the manual escape hatch.

    The full suite still runs ONCE on explicit dispatch; removing the
    dispatch shapes would leave no way to run the OS-matrix FULL check.
    """
    text = DECIDE.read_text(encoding="utf-8")
    assert "workflow_dispatch)" in text, "decide script must keep the dispatch branch"
    assert 'MODE="FULL"' in text, "dispatch with force must still select FULL"
    assert 'MODE="SKEW"' in text, "dispatch without force must still select SKEW"
