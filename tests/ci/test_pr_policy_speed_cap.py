"""PR-policy regression: no PR-feedback job runs longer than 10 minutes.

Speed cap (2026-11-09, hard user requirement): PR feedback in 3-5
minutes, and NO PR-feedback job may exceed 10 minutes. Evidence for the
cap: the 7d2fa45d run (created 18:53:53Z) was still in_progress ~47
minutes later — the Quality lane's pytest alone took 1449.8s on the
2-core tier-2 box, while the same full suite on a GitHub-hosted runner
took ~8 minutes. The 20-30 minute lanes are the 2-core box + coverage
overhead, not one slow test.

The cap lands WITH the tier-1 speedup, never alone: the routing prefers
tier-1 (opselon-docker-runner, 8-thread, pytest -n 8) and the runner
selector only falls to tier-2 when tier-1 has no online+idle match. On
tier-2 this job CANNOT fit in 10 minutes — a timeout there is the
fail-closed guard doing its job (bring tier-1 back), not a reason to
raise the cap.

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

# PR-feedback jobs: the 10-minute cap applies to all of these.
PR_JOBS = {
    "ci.yml": ("quality-lint", "quality-tests"),
    "tests-os.yml": ("tests-linux-self-hosted", "tests-linux-hosted"),
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


def test_pr_feedback_jobs_are_capped_at_10_minutes() -> None:
    """Every PR-feedback job has timeout-minutes <= 10."""
    for workflow, jobs in PR_JOBS.items():
        wf = _load(workflow)
        for job in jobs:
            timeout = wf["jobs"][job].get("timeout-minutes")
            assert timeout is not None, f"{workflow} {job} must set timeout-minutes"
            assert timeout <= 10, (
                f"{workflow} {job} timeout-minutes is {timeout}, "
                "over the 10-minute PR-feedback cap (2026-11-09)"
            )


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
