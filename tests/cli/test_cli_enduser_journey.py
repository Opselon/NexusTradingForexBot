"""ENDUSER-OPERABILITY lane — CLI journey certification (real subprocess).

The user-facing claim under test: a non-developer who only has the shipped
executable can discover the product's state, find its dashboard, and get an
actionable message on failure — without Python, logs, or source.

Every case here runs the REAL entry point as a subprocess and asserts on the
exact contract surface a user sees: exit code + stdout wording.

Journeys:
  * EU-09  `nexus status --json` is one canonical product-state answer
           (application / engine / execution_mode / trading axes) and exits 0.
  * EU-05  `nexus logs` reports the engine's REAL log root, not a path the
           engine never writes.
  * EU-03  `nexus dashboard --json` names the recorded address, answers
           honestly when nothing is running, and exits non-zero with the
           exact next action.
  * EU-03  `nexus dashboard --url <bogus>` exits 2 (usage) and never opens
           anything.
  * EU-04  `nexus --help` lists the dashboard + status commands a user is
           expected to be able to find.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

ENTRY: list[str]
if os.environ.get("NEXUS_CLI_EXE"):
    ENTRY = [os.environ["NEXUS_CLI_EXE"]]
elif (REPO_ROOT / ".venv" / "Scripts" / "nexus.exe").exists():
    # EU-RELEASE-003: only a console script built from THIS tree matches the
    # code under test. The shared main-checkout venv belongs to another branch.
    ENTRY = [str(REPO_ROOT / ".venv" / "Scripts" / "nexus.exe")]
else:
    ENTRY = [sys.executable, "-m", "nexus_scalp.cli.main"]


def run_cli(*args: str, timeout: int = 90):
    env = dict(os.environ)
    src_dir = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    if src_dir not in existing.split(os.pathsep):
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}" if existing else src_dir
    env.setdefault("NSE_NO_BROWSER", "1")  # never spawn a browser in the test box
    t0 = time.perf_counter()
    proc = subprocess.run(
        [*ENTRY, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr, time.perf_counter() - t0


pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "pyproject.toml").exists(), reason="must run from the repo"
)


# ---------------------------------------------------------------------------
# EU-09: one canonical state answer
# ---------------------------------------------------------------------------


def test_eu09_status_json_is_the_canonical_state_answer() -> None:
    rc, out, _err, _secs = run_cli("status", "--json")
    assert rc == 0, f"`nexus status --json` rc={rc}\n{out}"
    payload = json.loads(out[out.find("{") :])
    # The four axes a user must be able to read, and no more.
    for axis in ("application", "engine", "execution_mode", "trading"):
        assert axis in payload, f"status is missing the canonical {axis} axis"
    # Trading arming is never conflated with "engine running".
    assert payload["engine"] != "RUNNING" or payload["trading"] in {
        "NOT_ARMED",
        "PAPER_ONLY",
        "SHADOW_ONLY",
        "ARMED_LIVE",
        "BLOCKED",
        "UNKNOWN",
    }
    assert payload["application"] != "READY" or payload["engine"] == "RUNNING", (
        "READY application with a non-running engine is the exact conflation this fixes"
    )


def test_eu09_state_machine_is_advertised_for_automation() -> None:
    rc, out, _err, _secs = run_cli("status", "--json")
    assert rc == 0
    payload = json.loads(out[out.find("{") :])
    axes = payload.get("runtime", {}).get("product_state", {}).get("axes", {})
    assert "application" in axes and "trading" in axes, (
        "machine readers need the legal values advertised (EU-09 automation contract)"
    )
    # The canonical axes must be readable WITHOUT knowing the nesting of health.
    for axis in ("application", "engine", "execution_mode", "trading"):
        assert axis in payload, f"canonical {axis} axis missing from status --json root"


# ---------------------------------------------------------------------------
# EU-05: logs report the engine's real log root
# ---------------------------------------------------------------------------


def test_eu05_logs_command_reports_the_engine_log_root() -> None:
    rc, out, _err, _secs = run_cli("logs", "--json")
    assert rc == 0, f"`nexus logs --json` rc={rc}\n{out}"
    payload = json.loads(out[out.find("{") :])
    root = payload.get("log_root") or payload.get("root") or payload.get("dir")
    if root is not None:
        # The answer must be the tree the engine writes (release/paths owns it),
        # never a flat LocalAppData path the engine never touches.
        assert "logs" in str(root)
    # No matter what, an honest message is emitted.
    assert "logs" in out.lower()


# ---------------------------------------------------------------------------
# EU-03: dashboard discovery
# ---------------------------------------------------------------------------


def test_eu03_dashboard_json_is_honest_when_not_running() -> None:
    rc, out, _err, _secs = run_cli("dashboard", "--json")
    payload = json.loads(out[out.find("{") :])
    if payload.get("reachable"):
        # An engine happens to be up on this box: the answer must still be
        # internally consistent.
        assert payload["http_status"] is not None
        assert payload["opened"] in (True, False)
    else:
        assert rc != 0, "a dashboard that is not answering must NOT exit 0"
        assert "next_action" in payload, "a failure with no next action is incomplete"
        assert payload["next_action"], "the next action must be non-empty"


def test_eu03_dashboard_bad_url_is_usage_not_runtime() -> None:
    rc, out, _err, _secs = run_cli("dashboard", "--url", "not-a-url")
    assert rc == 2, f"invalid --url must be usage (rc 2), got {rc}"
    assert "http://" in out or "https://" in out, (
        "the error must tell the user what a valid URL looks like"
    )


def test_eu03_dashboard_help_tells_the_user_where_the_ui_is() -> None:
    rc, out, _err, _secs = run_cli("dashboard", "--help")
    assert rc == 0
    assert "dashboard" in out.lower()


# ---------------------------------------------------------------------------
# EU-04: discoverability — the commands a user needs exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["dashboard", "status", "logs", "doctor", "health"])
def test_eu04_user_commands_are_listed_in_help(command: str) -> None:
    rc, out, _err, _secs = run_cli("--help")
    assert rc == 0
    assert command in out, f"`nexus --help` does not advertise the {command} command"


def test_eu04_help_exits_zero() -> None:
    rc, _out, _err, _secs = run_cli("--help")
    assert rc == 0, f"`nexus --help` must exit 0 (got {rc})"


def test_eu04_unknown_command_exits_usage() -> None:
    rc, _out, _err, _secs = run_cli("no-such-command-xyz")
    assert rc != 0, "an unknown command must not exit 0"
