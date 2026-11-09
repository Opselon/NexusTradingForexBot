"""PR-policy regression: a fresh runner bootstraps itself, zero manual steps.

Fresh-machine bootstrap (2026-11-09, hard user requirement): the user's
own tier-1 runner (opselon-docker-runner) took its first jobs and ALL
THREE self-hosted jobs failed, while macOS/Windows (hosted) passed:

* Quality (job 129395787680): depth-1 checkout + blobless SHA fetch
  printed "blobless history fetched" but `git merge-base` failed — the
  fetch can exit 0 yet bring nothing on a virgin shallow clone, so the
  job died in 31s. It worked on tier-2 only because that machine had
  accumulated deep history over many runs.
* Lint (job 129395787984): `uv pip install -r requirements.lock`
  failed — "no version of typer==0.29.0" — the Aliyun mirror lags PyPI
  for fresh releases, so a fresh machine could not install the lock.
  The venv was created empty and every check reported "missing".
* OS Linux lane (job 129395775112): same install failure in its inline
  venv step; the lane-decision step never ran, so mode was empty and the
  lane report failed closed.

The rule: tomorrow we add 100 servers and this must not error
differently on each one. Everything is automatic IN THE CI CODE — no
manual per-machine setup, ever. Acceptance: a fresh runner (or a wiped
_work dir + empty ~/.cache) takes a job and goes green with zero manual
steps. These tests pin the bootstrap machinery so a future edit cannot
silently drop it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
FETCH_SCRIPT = REPO / "scripts" / "ci" / "fetch_planner_history.bash"
PRUNE_SCRIPT = REPO / "scripts" / "ci" / "prune_runner_state.bash"
PLAN_SCRIPT = REPO / "scripts" / "ci" / "run_test_plan.py"


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


def _load_plan_module():
    spec = importlib.util.spec_from_file_location("run_test_plan", PLAN_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- planner history -------------------------------------------------


def test_quality_fetch_step_uses_the_shared_script() -> None:
    """ci.yml Quality must fetch planner history via the shared script.

    The inline fetch (blobless + merge-base check, no escalation) is
    what died on tier-1. The shared script verifies merge-base and
    escalates to unshallow; the step must call it, not re-implement it.
    """
    wf = _load("ci.yml")
    steps = _steps(wf["jobs"]["quality-tests"])
    fetch = _named(steps, "Fetch planner history (blobless = commit+tree only)")
    run = _run(fetch)
    assert "fetch_planner_history.bash" in run, (
        "Quality fetch step must call scripts/ci/fetch_planner_history.bash"
    )
    assert "git merge-base" not in run, (
        "merge-base verification belongs in the shared script, not inline"
    )


def test_os_skew_steps_use_the_shared_script() -> None:
    """Both OS SKEW steps must fetch planner history via the shared script."""
    wf = _load("tests-os.yml")
    for leg in ("tests-linux-self-hosted", "tests-linux-hosted"):
        steps = _steps(wf["jobs"][leg])
        skew = _named(steps, "Run C-planned targeted subset")
        assert "fetch_planner_history.bash" in _run(skew), (
            f"{leg} SKEW step must call scripts/ci/fetch_planner_history.bash"
        )


def test_fetch_script_escalates_to_unshallow() -> None:
    """The fetch script must verify merge-base and escalate to unshallow."""
    text = FETCH_SCRIPT.read_text(encoding="utf-8")
    assert "git merge-base" in text, "script must verify merge-base"
    assert "--unshallow" in text, "script must escalate to unshallow when merge-base is unreachable"
    assert "HEAD~1" in text, "script must fall back to HEAD~1 for an empty base"
    assert FETCH_SCRIPT.stat().st_mode & 0o111, "script must be executable"


def test_fetch_script_actually_unshallows() -> None:
    """Behavioral: the script's escalation makes a shallow repo merge-base-ready.

    Builds a virgin depth-1 checkout of the PR merge commit in a temp
    dir (the exact tier-1 shape), runs the script against the real base
    and head SHAs, and asserts merge-base succeeds afterwards. This is
    the acceptance test in miniature: a fresh clone goes green with
    zero manual steps.
    """
    import tempfile

    base = "33195431b092240c174260c2da9b8f1ba693ea35"
    head = "fe1fe52123892e5c59902c85f4bfed0708dd474a"
    merge = "71cf05e3f3c41781917481d17b8d7dbd5b1a0081"
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        env = {"PATH": "/usr/bin:/bin", "HOME": tmp}

        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", *args],
                cwd=str(repo),
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )

        git("init", "-q")
        git("remote", "add", "origin", "https://github.com/Opselon/NexusTradingForexBot.git")
        r = git(
            "-c",
            "protocol.version=2",
            "fetch",
            "--no-tags",
            "--prune",
            "--no-recurse-submodules",
            "--depth=1",
            "origin",
            f"+{merge}:refs/remotes/pull/731/merge",
        )
        assert r.returncode == 0, f"merge fetch failed: {r.stderr}"
        r = git("checkout", "--force", "refs/remotes/pull/731/merge")
        assert r.returncode == 0, f"checkout failed: {r.stderr}"
        r = subprocess.run(
            ["bash", str(FETCH_SCRIPT), base, head],
            cwd=str(repo),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert r.returncode == 0, f"fetch script failed: {r.stdout}\n{r.stderr}"
        r = git("merge-base", base, head)
        assert r.returncode == 0 and r.stdout.strip() == base, (
            f"merge-base unreachable after the script: {r.stdout} {r.stderr}"
        )


# --- dependency index fallback ---------------------------------------


def test_setup_action_has_pypi_fallback_index() -> None:
    """setup-python-env must add PyPI as an extra index (mirror lag).

    UV_INDEX_STRATEGY is load-bearing: uv's default first-index
    strategy uses the FIRST index that has a package and never consults
    the others for it — the extra index alone was proven a no-op by the
    proof/leaf-timing run (PIP_EXTRA_INDEX_URL set, filelock 4.2.10
    still unresolvable). unsafe-best-match considers all indexes.
    """
    text = (REPO / ".github" / "actions" / "setup-python-env" / "action.yml").read_text(
        encoding="utf-8"
    )
    assert "PIP_EXTRA_INDEX_URL" in text, "setup action must set PIP_EXTRA_INDEX_URL"
    assert "https://pypi.org/simple/" in text, "fallback index must be PyPI"
    assert "UV_INDEX_STRATEGY" in text, "setup action must set UV_INDEX_STRATEGY"
    assert "unsafe-best-match" in text, "index strategy must be unsafe-best-match"
    assert "--refresh" in text, (
        "setup action must retry the lock install with a cache refresh "
        "on resolution failure (stale index cache / mirror lag)"
    )


def test_os_workflow_has_pypi_fallback_index() -> None:
    """tests-os.yml must set the PyPI fallback index (its venv installs inline)."""
    wf = _load("tests-os.yml")
    env = wf.get("env", {})
    assert env.get("PIP_EXTRA_INDEX_URL") == "https://pypi.org/simple/", (
        "tests-os.yml top-level env must set PIP_EXTRA_INDEX_URL to PyPI"
    )
    assert env.get("UV_INDEX_STRATEGY") == "unsafe-best-match", (
        "tests-os.yml top-level env must set UV_INDEX_STRATEGY to unsafe-best-match"
    )


# --- automatic prune -------------------------------------------------


def test_prune_script_is_invoked_by_setup_action_and_os_lanes() -> None:
    """The prune script must run in every lane that installs a venv."""
    action = (REPO / ".github" / "actions" / "setup-python-env" / "action.yml").read_text(
        encoding="utf-8"
    )
    assert "prune_runner_state.bash" in action, "setup action must call the prune script"
    wf = _load("tests-os.yml")
    for leg in ("tests-linux-self-hosted", "tests-linux-hosted"):
        steps = _steps(wf["jobs"][leg])
        venv = _named(steps, "Install via lock")
        assert "prune_runner_state.bash" in _run(venv), (
            f"{leg} venv step must call the prune script"
        )
    assert PRUNE_SCRIPT.stat().st_mode & 0o111, "prune script must be executable"


def test_prune_script_is_age_gated_and_never_fatal() -> None:
    """The prune script must only delete old state and must not fail a lane."""
    text = PRUNE_SCRIPT.read_text(encoding="utf-8")
    assert "-mtime" in text, "prune must be age-gated (mtime)"
    assert "nse-venvs" in text, "prune must cover hash-bound venvs"
    assert "/tmp" in text, "prune must cover /tmp test leftovers"
    # The callers guard with `|| true`; the script itself must also not
    # abort on a single failed deletion (set -uo pipefail, no -e, and
    # `|| true` on the find).
    assert "|| true" in text, "prune deletions must be failure-proof"


# --- cmake bootstrap -------------------------------------------------


def test_run_test_plan_bootstraps_cmake() -> None:
    """ensure_native_engine must self-bootstrap cmake when it is absent."""
    text = PLAN_SCRIPT.read_text(encoding="utf-8")
    assert "def _ensure_cmake" in text, "run_test_plan.py must define _ensure_cmake"
    assert "cmake" in text and "uv" in text, "bootstrap must install the cmake wheel via uv"
    assert "_ensure_cmake()" in text, "ensure_native_engine must call _ensure_cmake"
    assert "build-essential" in text, "missing C compiler must fail with the install command"


def test_ensure_cmake_bootstraps_when_missing() -> None:
    """Unit: _ensure_cmake installs the wheel and returns the venv binary."""
    mod = _load_plan_module()
    fake_proc = mock.Mock(returncode=0)
    with (
        mock.patch.object(mod.shutil, "which") as which,
        mock.patch.object(mod.subprocess, "run", return_value=fake_proc) as run,
        mock.patch.object(mod.os.path, "isfile", return_value=True),
        mock.patch.object(mod.os, "access", return_value=True),
    ):
        # cmake absent on PATH; uv present.
        def _which(cmd: str):
            return "/usr/bin/uv" if cmd == "uv" else None

        which.side_effect = _which
        got = mod._ensure_cmake()
        assert got.endswith("/cmake"), f"expected venv cmake path, got {got!r}"
        assert run.call_count == 1
        argv = run.call_args.args[0]
        assert argv[:2] == ["uv", "pip"], f"bootstrap must use uv pip, got {argv}"
        assert "--python" in argv and sys.executable in argv, (
            "uv pip install must target this interpreter (--python sys.executable)"
        )


def test_ensure_cmake_fails_closed_when_bootstrap_fails() -> None:
    """Unit: _ensure_cmake raises a clear error when every installer fails."""
    mod = _load_plan_module()
    fake_proc = mock.Mock(returncode=1)
    with (
        mock.patch.object(mod.shutil, "which", return_value=None),
        mock.patch.object(mod.subprocess, "run", return_value=fake_proc),
    ):
        try:
            mod._ensure_cmake()
        except RuntimeError as exc:
            assert "cmake" in str(exc).lower(), f"error must name cmake: {exc}"
        else:
            raise AssertionError("_ensure_cmake must raise when bootstrap fails")
