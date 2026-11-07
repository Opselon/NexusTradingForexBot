"""Regression tests for the CI runner-routing architecture (2026-11).

Locks down the two invariants the architecture depends on:

1. ``check_workflows.py`` resolves a LOCAL reusable workflow's declared
   outputs, so a caller job may reference ``needs.<job>.outputs.<name>``
   without a redundant local ``outputs:`` block. Without this rule every
   caller of ``runner-select.yml`` would be a false ERROR.
2. ``scripts/ci/runner_select.py`` decides the runner class correctly for
   every documented scenario: online/offline/busy runner, disabled flag,
   API failure, missing credential, fork PR, and label mismatch.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_CI = Path(__file__).resolve().parents[2] / "scripts" / "ci"
sys.path.insert(0, str(_CI))

import check_workflows as c  # noqa: E402
import runner_select as rs  # noqa: E402


def _write(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text(body, encoding="utf-8")
    return p


_CALLEE = """
name: callee
"on":
  workflow_call:
    inputs:
      platform:
        type: string
        required: true
outputs:
  runner_class:
    description: "class"
    value: ${{ jobs.select.outputs.runner_class }}
  selection_reason:
    description: "reason"
    value: ${{ jobs.select.outputs.selection_reason }}
permissions:
  contents: read
jobs:
  select:
    runs-on: ubuntu-latest
    outputs:
      runner_class: ${{ steps.emit.outputs.runner_class }}
      selection_reason: ${{ steps.emit.outputs.selection_reason }}
    steps:
      - name: emit
        id: emit
        run: |
          echo "runner_class=github-hosted" >> "$GITHUB_OUTPUT"
          echo "selection_reason=test" >> "$GITHUB_OUTPUT"
"""

_CALLER = """
name: caller
"on":
  push:
jobs:
  runner-select:
    uses: ./.github/workflows/callee.yml
    permissions:
      contents: read
    with:
      platform: linux
  work:
    needs: runner-select
    runs-on: ${{ needs.runner-select.outputs.runner_class == 'self-hosted' && 'self-hosted' || 'ubuntu-latest' }}
    steps:
      - run: echo "${{ needs.runner-select.outputs.selection_reason }}"
"""


def test_reusable_workflow_outputs_resolve_to_callee():
    """A caller reading callee-declared outputs must NOT be an error."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        wf_dir = d / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        _write(wf_dir, "callee.yml", _CALLEE)
        caller = _write(wf_dir, "caller.yml", _CALLER)
        wf = c.parse_workflow(caller)
        names = list(wf.jobs["runner-select"].outputs)
        assert "runner_class" in names, names
        assert "selection_reason" in names, names
        for check in (
            c.check_undefined_outputs,
            c.check_silent_skip,
        ):
            check(wf)
        assert not [f for f in wf.findings if f.severity == "ERROR"], wf.findings


def test_reusable_workflow_missing_output_still_fails():
    """A reference to an output the callee does NOT declare is an ERROR."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        wf_dir = d / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        _write(wf_dir, "callee.yml", _CALLEE)
        caller = _write(
            wf_dir,
            "caller.yml",
            _CALLER.replace(
                "needs.runner-select.outputs.selection_reason",
                "needs.runner-select.outputs.nope",
            ),
        )
        wf = c.parse_workflow(caller)
        c.check_undefined_outputs(wf)
        errs = [f for f in wf.findings if f.severity == "ERROR"]
        assert errs, "undeclared reusable-workflow output must be an ERROR"
        assert any("nope" in f.message for f in errs)


def test_unquoted_on_does_not_hide_outputs():
    """The YAML-1.1 ``on``-as-True trap must not mask declared outputs."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        wf_dir = d / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        # callee with BARE on: — outputs sit under the True key
        _write(wf_dir, "callee.yml", _CALLEE.replace('"on":', "on:"))
        caller = _write(wf_dir, "caller.yml", _CALLER)
        wf = c.parse_workflow(caller)
        names = list(wf.jobs["runner-select"].outputs)
        assert "runner_class" in names, names


# ---------------------------------------------------------------------------
# runner_select.py — the documented selection scenarios (section 12).
# ---------------------------------------------------------------------------
_REPO = "Opselon/NexusTradingForexBot"
_LINUX = ("self-hosted", "Linux", "X64")
_WIN = ("self-hosted", "Windows", "X64")
_MAC = ("self-hosted", "macOS", "ARM64")
_TOKEN = {"NSE_RUNNER_STATUS_TOKEN": "x" * 40}


def _runner(status="online", busy=False, name="r", labels=_LINUX):
    return {
        "name": name,
        "status": status,
        "busy": busy,
        "labels": [{"name": x} for x in labels],
    }


def _select(platform, runners, labels=_LINUX, env=None, untrusted=False):
    env = dict(_TOKEN, **(env or {}))
    orig = rs.list_runners
    rs.list_runners = lambda repository, token: runners
    try:
        return rs.select(platform, _REPO, labels, "fallback", env, untrusted=untrusted)
    finally:
        rs.list_runners = orig


def _assert_class(decision, expected):
    assert decision["runner_class"] == expected, decision


def test_linux_prefers_eligible_self_hosted():
    _assert_class(_select("linux", [_runner()]), "self-hosted")


def test_linux_falls_back_when_runner_offline():
    _assert_class(_select("linux", [_runner(status="offline")]), "github-hosted")


def test_linux_falls_back_when_runner_busy():
    _assert_class(_select("linux", [_runner(busy=True)]), "github-hosted")


def test_linux_falls_back_when_all_runners_busy_contention():
    runners = [_runner(busy=True, name="a"), _runner(busy=True, name="b")]
    _assert_class(_select("linux", runners), "github-hosted")


def test_linux_falls_back_when_flag_disabled():
    env = {"ENABLE_LINUX_SELF_HOSTED": "false"}
    _assert_class(_select("linux", [_runner()], env=env), "github-hosted")


def test_linux_falls_back_on_api_failure():
    _assert_class(_select("linux", None), "github-hosted")


def test_linux_falls_back_when_credential_missing():
    env = {"NSE_RUNNER_STATUS_TOKEN": ""}
    _assert_class(_select("linux", [_runner()], env=env), "github-hosted")


def test_fork_pr_never_uses_self_hosted():
    # Even with a healthy runner AND the flag on, a fork PR is untrusted.
    _assert_class(
        _select("linux", [_runner()], env={"ENABLE_LINUX_SELF_HOSTED": "true"}, untrusted=True),
        "github-hosted",
    )


def test_label_mismatch_falls_back():
    _assert_class(_select("linux", [_runner(labels=_WIN)]), "github-hosted")


def test_windows_flag_off_selects_hosted():
    _assert_class(_select("windows", [_runner(labels=_WIN)], labels=_WIN), "github-hosted")


def test_windows_flag_on_with_eligible_runner():
    env = {"ENABLE_WINDOWS_SELF_HOSTED": "true"}
    _assert_class(_select("windows", [_runner(labels=_WIN)], labels=_WIN, env=env), "self-hosted")


def test_windows_flag_on_with_offline_runner():
    env = {"ENABLE_WINDOWS_SELF_HOSTED": "true"}
    runners = [_runner(status="offline", name="w", labels=_WIN)]
    _assert_class(_select("windows", runners, labels=_WIN, env=env), "github-hosted")


def test_windows_flag_on_with_api_error():
    env = {"ENABLE_WINDOWS_SELF_HOSTED": "true"}
    _assert_class(_select("windows", None, labels=_WIN, env=env), "github-hosted")


def test_macos_flag_off_selects_hosted():
    _assert_class(_select("macos", [_runner(labels=_MAC)], labels=_MAC), "github-hosted")


def test_macos_flag_on_with_eligible_runner():
    env = {"ENABLE_MACOS_SELF_HOSTED": "true"}
    _assert_class(_select("macos", [_runner(labels=_MAC)], labels=_MAC, env=env), "self-hosted")


def test_macos_flag_on_with_busy_runner():
    env = {"ENABLE_MACOS_SELF_HOSTED": "true"}
    runners = [_runner(busy=True, name="m", labels=_MAC)]
    _assert_class(_select("macos", runners, labels=_MAC, env=env), "github-hosted")


def test_decision_records_reason_and_eligibility():
    d = _select("linux", [_runner()])
    assert d["self_hosted_eligible"] is True
    assert d["reason"], "reason must be populated for observability"
    assert "self-hosted" in d["reason"]


def test_malformed_repository_identifier_fails_open(monkeypatch=None):
    """A bad --repository must not reach the network; it fails open."""
    import subprocess
    import sys as _sys

    r = subprocess.run(
        [
            _sys.executable,
            str(_CI / "runner_select.py"),
            "--platform",
            "linux",
            "--labels",
            "self-hosted,Linux,X64",
            "--hosted",
            "ubuntu-latest",
            "--repository",
            "not-a-valid-repo",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    import json as _json

    # The CLI pretty-prints the decision JSON; reassemble it.
    start = r.stdout.index("{")
    decision = _json.loads(r.stdout[start:])
    _assert_class(decision, "github-hosted")
    assert "malformed repository identifier" in decision["reason"]
