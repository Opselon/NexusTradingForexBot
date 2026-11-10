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


def _decide_mode(event: str, platform: str = "linux") -> str:
    """Run decide_os_lane.bash with the given event/platform; return its mode."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "gh-out.txt"
        env = dict(os.environ)
        env["GITHUB_EVENT_NAME"] = event
        env["GITHUB_OUTPUT"] = str(out)
        env.pop("GITHUB_BASE_REF", None)
        env.pop("GITHUB_EVENT_BEFORE", None)
        env.pop("NSE_FORCE_FULL_SUITE", None)
        proc = subprocess.run(
            ["bash", str(DECIDE), platform],
            cwd=str(REPO),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, f"decide script failed for {event}/{platform}: {proc.stderr}"
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.startswith("mode="):
                return line.split("=", 1)[1]
    raise AssertionError(f"decide script wrote no mode for {event}/{platform}")


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


# --- Windows / macOS: schedule + dispatch only, never on PRs ----------


def _on(wf: dict) -> dict:
    # PyYAML parses the `on:` key as boolean True (YAML 1.1).
    return wf.get(True, wf.get("on", {}))


def test_windows_and_macos_run_on_schedule_never_on_prs() -> None:
    """Windows/macOS lanes: semi-monthly schedule + dispatch, no PR trigger.

    The Windows OS lane must not borrow a hosted Windows runner on every
    PR — Quality owns PR test execution. These workflows have no
    push/pull_request trigger at all (the `Py Tests (windows-latest)` /
    `Py Tests (macos-latest)` checks on a PR are routing notices from
    tests-os.yml's ctx jobs on ubuntu-latest, not test execution). Their
    cross-platform coverage comes from the semi-monthly sweep (1st &
    15th, 07:17 UTC — the same cadence as tests-os.yml, at most twice a
    month, like the MT5-native line).
    """
    for wf_name in ("tests-windows.yml", "tests-macos.yml"):
        wf = _load(wf_name)
        on = _on(wf)
        assert "push" not in on and "pull_request" not in on, (
            f"{wf_name} must have no push/pull_request trigger (never on PRs)"
        )
        assert "schedule" in on, f"{wf_name} must have a schedule trigger"
        crons = [s.get("cron", "") for s in on["schedule"]]
        assert "17 7 1,15 * *" in crons, (
            f"{wf_name} schedule must be semi-monthly (1st & 15th, 07:17 UTC)"
        )
        assert "workflow_dispatch" in on, f"{wf_name} must keep the dispatch escape hatch"


def test_perf_benchmark_runs_on_schedule_never_on_prs() -> None:
    """Performance benchmark: semi-monthly schedule + dispatch, no PR trigger.

    The hosted nse-bench regression watch must not run on every PR that
    touches web/models/experience/bench paths — it burns hosted minutes
    and PR noise for a regression table. Its PR home is the Quality
    wall-clock budget step (blocking, every PR); the full regression
    table comes from the semi-monthly sweep (1st & 15th, 04:17 UTC)
    plus manual dispatch.
    """
    wf = _load("perf-benchmark.yml")
    on = _on(wf)
    assert "push" not in on and "pull_request" not in on, (
        "perf-benchmark.yml must have no push/pull_request trigger (never on PRs)"
    )
    assert "schedule" in on, "perf-benchmark.yml must have a schedule trigger"
    crons = [s.get("cron", "") for s in on["schedule"]]
    assert "17 4 1,15 * *" in crons, (
        "perf-benchmark.yml schedule must be semi-monthly (1st & 15th, 04:17 UTC)"
    )
    assert "workflow_dispatch" in on, "perf-benchmark.yml must keep the dispatch escape hatch"


def test_os_matrix_runs_on_schedule_never_on_prs() -> None:
    """OS matrix: semi-monthly schedule + dispatch, no PR/push trigger.

    Quality (ci.yml) owns ALL PR/push test execution — one execution per
    PR. The matrix ran its Linux leg on every PR: it installed a full
    workspace venv BEFORE the decide step, then SKIP-REPORTed — racing
    Quality for the single online runner and falling back to hosted
    (2026-11-10: the OS Matrix fell to github-hosted because Dubai was
    busy running Quality). The matrix now runs only the semi-monthly
    FULL-suite health sweep (1st & 15th, 07:17 UTC) plus manual dispatch
    (full-platform check). The decide script's SKIP-REPORT on PR/push
    stays as the backstop if the triggers ever come back.
    """
    wf = _load("tests-os.yml")
    on = _on(wf)
    assert "push" not in on and "pull_request" not in on, (
        "tests-os.yml must have no push/pull_request trigger (never on PRs)"
    )
    assert "schedule" in on, "tests-os.yml must have a schedule trigger"
    crons = [s.get("cron", "") for s in on["schedule"]]
    assert "17 7 1,15 * *" in crons, (
        "tests-os.yml schedule must be semi-monthly (1st & 15th, 07:17 UTC)"
    )
    assert "workflow_dispatch" in on, "tests-os.yml must keep the dispatch escape hatch"


def test_windows_and_macos_run_full_on_schedule() -> None:
    """decide_os_lane.bash: FULL for windows/macos on schedule, SKIP for linux.

    Linux skips on schedule because Quality runs the FULL suite once
    there. Windows/macOS have no Quality lane, so their scheduled sweep
    runs FULL here — unconditionally (a periodic health sweep, not a
    change-driven run; the classifier must not downgrade it, so this
    asserts the mode with no diff context at all).
    """
    assert _decide_mode("schedule", "linux") == "SKIP-REPORT", (
        "Linux must SKIP-REPORT on schedule (Quality owns the scheduled FULL suite)"
    )
    for platform in ("windows", "macos"):
        assert _decide_mode("schedule", platform) == "FULL", (
            f"{platform} must run FULL on schedule (semi-monthly cross-platform sweep)"
        )


def test_windows_and_macos_still_skip_on_prs() -> None:
    """decide_os_lane.bash: SKIP-REPORT for windows/macos on PR/push too.

    Belt and braces with the trigger test above: even if someone re-adds
    a PR trigger, the lane must not execute on a PR.
    """
    for platform in ("windows", "macos"):
        for event in ("pull_request", "push"):
            assert _decide_mode(event, platform) == "SKIP-REPORT", (
                f"{platform} must SKIP-REPORT on {event} (Quality owns PR execution)"
            )
