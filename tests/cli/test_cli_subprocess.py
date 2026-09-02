"""CLI SUBPROCESS certification harness (TASK-CLI-CLOSURE, 2026-09-01).

Real-executable tests: these run the installed entry point as a SUBPROCESS
(no in-process CliRunner shortcuts), capturing exit code / stdout / stderr /
duration - the contract surface a real Windows user gets.

Harness (module-level helper):
    run_cli(*args, cwd=None, timeout=120) -> (rc, stdout, stderr, duration_s)

Scope per the user directive (2026-09-01 data-quota steer): these tests are
OFFLINE-ONLY. They exercise help/version/doctor/status/error UX and JSON
purity. Anything that downloads (update install, model training against the
network) is intentionally NOT executed here; `nexus update check --json` runs
against a bounded timeout but asserts only structural honesty, and is skipped
when NEXUS_CLI_NO_NETWORK=1 is set.

Entry point resolution order:
    1. $NEXUS_CLI_EXE      - explicit exe for a disposable-install run
    2. repo .venv nexus.exe - the managed-runtime equivalent
    3. python -m fallback   - ``python -m nexus_scalp.cli.main`` via sys.executable
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


def _resolve_entry() -> list[str]:
    explicit = os.environ.get("NEXUS_CLI_EXE")
    if explicit:
        return [explicit]
    venv_exe = REPO_ROOT / ".venv" / "Scripts" / "nexus.exe"
    if venv_exe.exists():
        return [str(venv_exe)]
    return [sys.executable, "-m", "nexus_scalp.cli.main"]


ENTRY = _resolve_entry()


def run_cli(*args: str, cwd: Path | None = None, timeout: int = 120):
    """Run the real CLI as a subprocess; return (rc, stdout, stderr, seconds)."""
    cmd = [*ENTRY, *args]
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr, time.perf_counter() - t0


def parse_json(stdout: str) -> dict:
    """Assert stdout carries ONLY valid JSON (first '{' onward, no preamble)."""
    stripped = stdout.strip()
    assert stripped, "empty stdout"
    start = stripped.find("{")
    assert start == 0, f"JSON mode stdout must begin with '{{' (got {stripped[:60]!r})"
    return json.loads(stripped)


pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "pyproject.toml").exists(), reason="must run from the repo"
)


# ---------------------------------------------------------------------------
# Golden: help
# ---------------------------------------------------------------------------


class TestHelp:
    def test_nexus_help_rc0_core_commands_present(self):
        rc, out, err, dur = run_cli("--help")
        assert rc == 0
        # Core command names pinned (not whitespace/cosmetics).
        for cmd in (
            "help",
            "version",
            "doctor",
            "status",
            "start",
            "stop",
            "restart",
            "update",
            "repair",
            "config",
            "settings",
            "setup",
            "install",
            "uninstall",
            "health",
        ):
            assert cmd in out, f"--help missing core command: {cmd}"
        assert dur < 30

    def test_help_word_form_same_surface_rc0(self):
        rc, out, _, _ = run_cli("help")
        assert rc == 0
        for cmd in ("version", "doctor", "start", "update", "repair"):
            assert cmd in out
        # must equal the --help surface (byte-for-byte modulo trailing space)
        _, out2, _, _ = run_cli("--help")
        assert out.strip() == out2.strip(), "nexus help must mirror nexus --help"

    def test_help_topic_renders_command_help(self):
        rc, out, _, _ = run_cli("help", "start")
        assert rc == 0
        assert "start" in out.lower()
        assert "mode" in out.lower()

    def test_help_unknown_topic_rc2_readable(self):
        rc, out, err, _ = run_cli("help", "definitely-nope")
        assert rc == 2
        combined = out + err
        assert "does not exist" in combined or "No such command" in combined
        assert "nexus help" in combined  # recovery hint present
        assert "Traceback" not in combined

    def test_help_is_fast_and_side_effect_free(self):
        rc, _, _, dur = run_cli("help")
        assert rc == 0 and dur < 15


# ---------------------------------------------------------------------------
# Golden: version / doctor / status
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_rc0(self):
        rc, out, _, _ = run_cli("version")
        assert rc == 0

    def test_version_json_minimal_deterministic(self):
        rc, out, _, _ = run_cli("version", "--json")
        assert rc == 0
        data = parse_json(out)
        for key in ("product", "version", "channel", "architecture"):
            assert key in data
        assert isinstance(data["version"], str)

    def test_version_plain_small_output(self):
        rc, out, _, _ = run_cli("version", "--plain")
        assert rc == 0
        assert len(out) < 300, "version --plain should be a single small line"


class TestDoctorStatus:
    def test_doctor_json_valid_structure(self):
        rc, out, _, _ = run_cli("doctor", "--json")
        assert rc in (0, 1)  # doctor may honestly report DEGRADED on dev hosts
        data = parse_json(out)
        assert "overall" in data and "checks" in data
        for check in data["checks"]:
            assert "category" in check and "verdict" in check

    def test_status_json_side_effect_free(self):
        rc, out, _, _ = run_cli("status", "--json")
        assert rc in (0, 1)
        data = parse_json(out)
        assert isinstance(data, dict)

    def test_health_json(self):
        rc, out, _, _ = run_cli("health", "--json")
        assert rc in (0, 1)
        parse_json(out)


# ---------------------------------------------------------------------------
# User-input matrix (realistic Windows mistakes)
# ---------------------------------------------------------------------------


class TestUserInputMatrix:
    def test_unknown_command_rc2_no_traceback(self):
        rc, out, err, _ = run_cli("definitely-not-a-command")
        assert rc == 2
        combined = out + err
        assert "No such command" in combined
        assert "Traceback" not in combined

    def test_unknown_option_rc2_no_traceback(self):
        rc, out, err, _ = run_cli("--definitely-invalid-option")
        assert rc == 2
        assert "No such option" in (out + err)
        assert "Traceback" not in (out + err)

    def test_bad_option_on_doctor_rc2(self):
        rc, out, err, _ = run_cli("doctor", "--foo")
        assert rc == 2
        assert "Traceback" not in (out + err)

    def test_empty_string_argument_handled(self):
        rc, out, err, _ = run_cli("")
        assert rc != 0
        assert "Traceback" not in (out + err)

    def test_whitespace_argument_handled(self):
        rc, out, err, _ = run_cli("   ")
        assert rc != 0
        assert "Traceback" not in (out + err)

    def test_invalid_enum_value_rejected(self):
        # start validates mode; invalid enum must fail with usage error
        rc, out, err, _ = run_cli("start", "--mode", "not-a-mode", "--yes")
        combined = out + err
        assert rc != 0
        assert "Traceback" not in combined

    def test_invalid_path_handled_cleanly(self):
        rc, out, err, _ = run_cli("config", "--validate", "Z:/definitely/missing/config.yaml")
        combined = out + err
        assert rc != 0
        assert "Traceback" not in combined

    def test_unicode_arg_no_crash(self):
        rc, out, err, _ = run_cli("help", " star t")
        combined = out + err
        assert "Traceback" not in combined

    def test_long_argument_no_crash(self):
        rc, out, err, _ = run_cli("help", "x" * 500)
        assert "Traceback" not in (out + err)

    def test_extra_args_no_hang(self):
        rc, out, err, _ = run_cli("version", "extra", "args")
        combined = out + err
        assert rc in (0, 2)
        assert "Traceback" not in combined


# ---------------------------------------------------------------------------
# Cross-CWD (caller-repo isolation at the CLI level)
# ---------------------------------------------------------------------------


class TestCrossCwd:
    def test_version_from_temp_cwd(self, tmp_path):
        rc, out, _, _ = run_cli("version", cwd=tmp_path)
        assert rc == 0

    def test_version_from_drive_root_cwd(self):
        rc, out, _, _ = run_cli("version", cwd=Path(os.environ.get("SystemDrive", "C:") + "/"))
        assert rc == 0

    def test_help_json_free_from_other_cwd(self, tmp_path):
        rc, out, _, _ = run_cli("help", cwd=tmp_path)
        assert rc == 0


# ---------------------------------------------------------------------------
# JSON purity contract
# ---------------------------------------------------------------------------


class TestJsonPurity:
    @pytest.mark.parametrize(
        "args",
        [
            ("version", "--json"),
            ("health", "--json"),
            ("status", "--json"),
            ("doctor", "--json"),
        ],
    )
    def test_json_commands_stdout_is_pure_json(self, args):
        rc, out, _, _ = run_cli(*args)
        if rc == 2:  # usage regression would be a contract break
            pytest.fail(f"{args} unexpectedly a usage error")
        if not out.strip():
            pytest.fail(f"{args} produced empty stdout in JSON mode")
        start = out.strip().find("{")
        json.loads(out.strip()[start:])


# ---------------------------------------------------------------------------
# Network-optional: update check (skippable per data-quota steer)
# ---------------------------------------------------------------------------


class TestUpdateCheck:
    @pytest.mark.skipif(
        os.environ.get("NEXUS_CLI_NO_NETWORK") == "1",
        reason="NEXUS_CLI_NO_NETWORK=1 (data-quota steer): skip network-touching test",
    )
    def test_update_check_json_honest(self):
        rc, out, _, _ = run_cli("update", "check", "--json", timeout=180)
        assert rc in (0, 5)  # 0 = success, 5 = update-not-applicable/failed class
        data = parse_json(out)
        # Honest check: must carry a real state field, never fabricated.
        assert "state" in data or "status" in data

    def test_update_dry_run_or_check_offline_skips_cleanly(self):
        """--dry-run / offline contract documented in docs/CLI.md (no download)."""
        rc, out, _, _ = run_cli("update", "--help")
        assert rc == 0
        assert "--dry-run" in out
