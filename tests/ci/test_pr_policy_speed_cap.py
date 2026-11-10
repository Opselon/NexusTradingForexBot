"""PR-policy regression: no PR-feedback job runs longer than its cap.

Speed cap (2026-11-09, hard user requirement): PR feedback in 3-5
minutes, and NO PR-feedback job may exceed 10 minutes. Evidence for the
cap: the 7d2fa45d run (created 18:53:53Z) was still in_progress ~47
minutes later — the Quality lane's pytest alone took 1449.8s on the
4-core tier-2 box (Dubai), while the same full suite on a GitHub-hosted runner
took ~8 minutes. The 20-30 minute lanes are the 4-core box + coverage
overhead, not one slow test.

TEMPORARY (2026-11-10, user directive, revised ~08:57): the ci.yml
PR-feedback jobs (quality-lint, quality-tests) are capped at 60
minutes, not 10 — tier-1 is OFF, so they fall back to Dubai (tier-2),
and the serial -n 1 run on the expanded suite (356 files) crept at
~1% at a time and would blow past 20 min. Back to 10 when tier-1
returns / the venv is warm. The tests-os.yml Linux legs stay at 10:
they SKIP on PRs (Quality owns execution), so the cap never binds them.

The cap lands WITH the tier-1 speedup, never alone: the routing prefers
tier-1 (opselon-docker-runner) and the runner
selector only falls to tier-2 when tier-1 has no online+idle match. On
tier-2 this job CANNOT fit in 10 minutes — a timeout there is the
fail-closed guard doing its job (bring tier-1 back), not a reason to
raise the cap — except for this temporary 60-minute allowance while
tier-1 is off by the user's own hand. Pytest runs with FOUR workers on
self-hosted tiers (2026-11-10 user directive, revised ~08:57: the -n 1
serial run crept at ~1% at a time; -n 4 was tried before and measured
no speedup on Dubai, only memory pressure and swap thrash — revisit if
it thrashes again).

Scope (deliberate, pinned below): the cap covers the PR-feedback path
only — ci.yml quality-lint / quality-tests and the tests-os.yml Linux
legs. It does NOT cover the schedule/manual-only heavy jobs (extended,
heavy-ci, full-runtime-provider, db-fabric-certification) or the
dispatch-only macOS/Windows FULL lanes: those legitimately run long
full suites on a schedule or on explicit dispatch, and capping them at
10 minutes would kill them every run — that would weaken safety, not
improve speed. A future edit that caps those jobs must change this test
explicitly, with the reason recorded.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(".github/workflows")

# PR-feedback jobs and their caps (2026-11-10, user directive,
# revised ~08:57): ci.yml quality-lint / quality-tests are capped at
# 60 minutes — tier-1 is OFF, so they fall back to Dubai (tier-2) and
# the serial run on the expanded suite would blow past 20 min.
# Temporary: back to 10 when tier-1 returns / the venv is warm. The
# tests-os.yml Linux legs stay at 10 — they SKIP on PRs (Quality owns
# execution), so the cap never binds them.
PR_JOB_CAPS = {
    "ci.yml": (("quality-lint", "quality-tests"), 60),
    "tests-os.yml": (("tests-linux-self-hosted", "tests-linux-hosted"), 10),
}

# Schedule/manual-only heavy jobs: deliberately NOT capped at 10 minutes.
# Their budgets below are the designed values (2026-11-09); the test pins
# them as "above the cap" so a blanket cap-everything edit fails loudly.
HEAVY_JOBS = {
    "ci.yml": ("extended", "heavy-ci", "full-runtime-provider", "db-fabric-certification"),
}


def _load(name: str) -> dict:
    with open(WORKFLOWS / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_pr_feedback_jobs_are_capped() -> None:
    """Every PR-feedback job has timeout-minutes <= its cap."""
    for workflow, (jobs, cap) in PR_JOB_CAPS.items():
        wf = _load(workflow)
        for job in jobs:
            timeout = wf["jobs"][job].get("timeout-minutes")
            assert timeout is not None, f"{workflow} {job} must set timeout-minutes"
            assert timeout <= cap, (
                f"{workflow} {job} timeout-minutes is {timeout}, "
                f"over the {cap}-minute PR-feedback cap (2026-11-10)"
            )


def _steps(wf: dict, job: str) -> list:
    return wf["jobs"][job].get("steps", [])


def _run(step: dict) -> str:
    return step.get("run", "")


def test_self_hosted_pytest_runs_four_workers() -> None:
    """Self-hosted pytest runs with FOUR workers (-n 4); hosted keeps cores.

    2026-11-10 (user directive, revised ~08:57): the -n 1 serial run on
    the expanded suite (356 files) crept at ~1% at a time on Dubai
    (4 cores / 8 GB), so the user asked for -n 4. Note: -n 4 was tried
    before and measured no speedup on this box (memory pressure + swap
    thrash) — revisit if it thrashes again. Hosted runners are big
    ephemeral machines and hosted is last-resort only, so they keep the
    core-sized count (capped at 8).
    """
    wf = _load("ci.yml")
    pytest_steps = [s for s in _steps(wf, "quality-tests") if "--xdist" in _run(s)]
    assert pytest_steps, "quality-tests must have a pytest step using --xdist"
    run = _run(pytest_steps[0])
    assert "RUNNER_ENVIRONMENT" in run and "XDIST=4" in run, (
        "quality-tests must force XDIST=4 when RUNNER_ENVIRONMENT is self-hosted"
    )

    os_wf = _load("tests-os.yml")
    for leg, expect_four in (
        ("tests-linux-self-hosted", True),
        ("tests-linux-hosted", False),
    ):
        skew = [s for s in _steps(os_wf, leg) if "--xdist" in _run(s)]
        assert skew, f"{leg} must have a SKEW step using --xdist"
        leg_run = _run(skew[0])
        if expect_four:
            assert "XDIST=4" in leg_run and "cpu_count" not in leg_run, (
                f"{leg} (Dubai) must run pytest with four workers"
            )
        else:
            assert "cpu_count" in leg_run, f"{leg} (hosted) must keep the core-sized worker count"


def test_heavy_schedule_only_jobs_keep_their_budgets() -> None:
    """Schedule/manual-only heavy jobs are explicitly NOT capped at 10.

    Capping them would kill legitimate long runs (scheduled full suites,
    dispatch-only heavy lanes) on every execution. If the speed program
    ever makes one of them fit in 10 minutes, update this test with the
    measurement — do not silently extend the cap.
    """
    for workflow, jobs in HEAVY_JOBS.items():
        wf = _load(workflow)
        for job in jobs:
            timeout = wf["jobs"][job].get("timeout-minutes")
            assert timeout is not None, f"{workflow} {job} must set timeout-minutes"
            assert timeout > 10, (
                f"{workflow} {job} timeout-minutes is {timeout}; the heavy "
                "schedule/manual-only jobs keep budgets above the 10-minute "
                "PR-feedback cap by design"
            )
