"""RC-1 + RC-2 regression tests (Nexus Installer Evolution, 2026-09-02).

RC-1: full-install -Json stdout must be PURE JSON frames (no Write-Host
      banner prefix). Full network install is E2E-gated, so the cheap
      always-on proof is the dry-run JSON surface + the banner function's
      driver-mode routing, plus the repair -Json surface.

RC-2: when -NexusHome is passed explicitly it must WIN over $env:NEXUS_HOME
      for BOTH roots (home and engine dir). Regression for the resolved-paths
      defect where install_dir derived from the env var even when -NexusHome
      pointed elsewhere.

These live in their own file so the repair wave's provenance is auditable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "installer" / "install.ps1"

pytestmark = pytest.mark.skipif(
    not INSTALLER.exists() or sys.platform != "win32",
    reason="installer tests require Windows + installer/install.ps1",
)


def _ps() -> str:
    for candidate in ("pwsh.exe", "powershell.exe"):
        p = shutil.which(candidate)
        if p:
            return p
    pytest.skip("no PowerShell host")
    return ""


PS = _ps()


def run_installer(*args: str, timeout: int = 180, env: dict | None = None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    cmd = [
        PS,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALLER),
        *args,
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=full_env,
    )


def parse_json_stdout(result) -> dict:
    stdout = result.stdout.strip()
    assert stdout, f"empty stdout (stderr: {result.stderr[:500]})"
    return json.loads(stdout)  # raises if stdout is not pure JSON


class TestRc1BannerJsonPurity:
    def test_dryrun_json_stdout_is_single_pure_json_frame(self, tmp_path):
        """-DryRun is a no-mutation driver surface; stdout must parse as ONE
        JSON document with zero non-JSON prefix (the RC-1 banner defect class)."""
        home = tmp_path / "NexusHome"
        result = run_installer("-DryRun", "-Json", "-NexusHome", str(home), "-NonInteractive")
        assert result.returncode == 0, result.stderr[-500:]
        parsed = parse_json_stdout(result)
        assert parsed["ok"] is True
        assert parsed["mode"] == "dry-run"
        assert len(parsed["would_run_stages"]) >= 10
        # Byte-level: the first non-whitespace char must be '{' (no banner).
        assert result.stdout.lstrip().startswith("{")

    def test_banner_function_routes_to_stderr_in_driver_mode(self, tmp_path):
        """Dot-source the installer and call Write-Banner under driver mode:
        it must NOT write the ASCII banner box to stdout (RC-1 root cause)."""
        home = tmp_path / "NexusHome2"
        script = (
            ". '{inst}'\n"
            "$Script:_DriverMode = $true\n"
            "$NexusHome = '{home}'\n"
            "$Script:ResolvedPathReport = [ordered]@{{}}\n"
            "Write-Banner\n"
            "'BANNER-DONE'\n"
        ).format(inst=str(INSTALLER).replace("\\", "/"), home=str(home).replace("\\", "/"))
        r = subprocess.run(
            [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert "BANNER-DONE" in r.stdout, r.stderr[-300:]
        assert "Nexus Scalp Engine Installer" not in r.stdout, (
            "banner leaked to stdout in driver mode (RC-1 regression)"
        )


class TestRc2HomeParamPrecedence:
    def test_explicit_nexushome_wins_over_env_for_both_roots(self, tmp_path):
        """-NexusHome A + env NEXUS_HOME B => both resolved roots must live
        under A. Regression: install_dir used to derive from the env var."""
        param_home = tmp_path / "ParamHome"
        env_home = tmp_path / "EnvHome"
        env_home.mkdir()
        result = run_installer(
            "-ShowResolvedPaths",
            "-NexusHome",
            str(param_home),
            env={"NEXUS_HOME": str(env_home)},
        )
        assert result.returncode == 0, result.stderr[-500:]
        report = parse_json_stdout(result)
        ph = str(param_home).lower()
        eh = str(env_home).lower()
        assert ph in str(report["nexus_home"]).lower(), report
        assert ph in str(report["install_dir"]).lower(), (
            f"RC-2 regression: install_dir followed env var not -NexusHome: {report}"
        )
        assert eh not in str(report["install_dir"]).lower(), report

    def test_env_nexus_home_alone_still_drives_defaults(self, tmp_path):
        """The documented env override (no -NexusHome) keeps working: both
        roots derive from $env:NEXUS_HOME."""
        env_home = tmp_path / "EnvOnlyHome"
        result = run_installer("-ShowResolvedPaths", env={"NEXUS_HOME": str(env_home)})
        assert result.returncode == 0, result.stderr[-500:]
        report = parse_json_stdout(result)
        eh = str(env_home).lower()
        assert eh in str(report["nexus_home"]).lower(), report
        assert eh in str(report["install_dir"]).lower(), report

    def test_explicit_installdir_still_beats_everything(self, tmp_path):
        """-InstallDir remains the strongest override (doc contract)."""
        param_home = tmp_path / "PH"
        engine = tmp_path / "CustomEngine"
        result = run_installer(
            "-ShowResolvedPaths",
            "-NexusHome",
            str(param_home),
            "-InstallDir",
            str(engine),
            env={"NEXUS_HOME": str(tmp_path / "EH")},
        )
        assert result.returncode == 0, result.stderr[-500:]
        report = parse_json_stdout(result)
        assert str(engine).lower() in str(report["install_dir"]).lower(), report
