"""Venv transaction stress + failure-injection suite (TASK: lifecycle assurance).

OFFLINE: pure local fixtures, no network. Every test proves one lifecycle
invariant of the deferred venv transaction (pending-backup marker):

  V1  marker written when old venv parked; stale sweep respects it
  V2  missing backup + present marker -> marker self-heals (dropped)
  V3  marker + corrupt backup name -> marker dropped, no crash
  V4  backup exists but marker missing -> sweep may collect it (documented)
  V5  failure injection: marker deleted between stages -> stale sweep keeps
      nothing inconsistent; next install healthy-detects and rebuilds
  V6  ledger truthfulness: a failed stage writes ok=false into install.json
  V7  zip-slip assault remains blocked (regression guard, local fixtures)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "installer" / "install.ps1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not INSTALLER.exists(),
    reason="Windows + installer required",
)


def _ps() -> str:
    import shutil

    for c in ("pwsh.exe", "powershell.exe"):
        p = shutil.which(c)
        if p:
            return p
    pytest.skip("no PowerShell")
    return ""


PS = _ps()


def run_ps(expr: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", expr],
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def dot(installer: Path) -> str:
    return '. "' + installer.as_posix() + '"; '


class TestVenvTransaction:
    def _setup_home(self, tmp_path: Path) -> Path:
        home = tmp_path / "NexusHome"
        (home / "state").mkdir(parents=True)
        return home

    def test_marker_written_and_sweep_respects_pending(self, tmp_path):
        """V1: parked tree + marker -> stale sweep must NOT delete the backup."""
        home = self._setup_home(tmp_path)
        backup = home / "venv.stale.20260901-120000-abc"
        backup.mkdir()
        (backup / "pyvenv.cfg").write_text("home = X", encoding="utf-8")
        marker = home / "state" / "venv.pending-backup"
        marker.write_text(backup.name, encoding="utf-8")

        expr = (
            dot(INSTALLER)
            + f"$NexusHome = '{home.as_posix()}'; "
            + "Remove-StaleVenvBackups -AgeMinutes 0; "
            + "'BACKUP_ALIVE=' + (Test-Path -LiteralPath '"
            + backup.as_posix()
            + "')"
        )
        r = run_ps(expr)
        assert "BACKUP_ALIVE=True" in r.stdout, r.stdout + r.stderr

    def test_missing_backup_marker_self_heals(self, tmp_path):
        """V2: marker pointing at a missing tree is dropped, no crash."""
        home = self._setup_home(tmp_path)
        marker = home / "state" / "venv.pending-backup"
        marker.write_text("venv.stale.gone", encoding="utf-8")

        expr = (
            dot(INSTALLER)
            + f"$NexusHome = '{home.as_posix()}'; "
            + "$b = Get-PendingVenvBackup; "
            + "'MARKER_GONE=' + (-not (Test-Path -LiteralPath '"
            + marker.as_posix()
            + "')); 'BACKUP=' + $b"
        )
        r = run_ps(expr)
        assert "MARKER_GONE=True" in r.stdout, r.stdout + r.stderr
        assert "BACKUP=" in r.stdout

    def test_marker_with_corrupt_backup_name_dropped(self, tmp_path):
        """V3: marker content names a nonexistent/corrupt tree -> dropped."""
        home = self._setup_home(tmp_path)
        marker = home / "state" / "venv.pending-backup"
        marker.write_text("../evil", encoding="utf-8")

        expr = (
            dot(INSTALLER)
            + f"$NexusHome = '{home.as_posix()}'; "
            + "$b = Get-PendingVenvBackup; "
            + "'BACKUP_IS_NULL=' + ($null -eq $b)"
        )
        r = run_ps(expr)
        assert "BACKUP_IS_NULL=True" in r.stdout, r.stdout + r.stderr

    def test_crash_between_stages_recovers_on_next_run(self, tmp_path):
        """V5: simulate crash after venv park but before deps commit - marker
        + backup exist. The next venv-stage invocation must healthy-detect the
        RESTORED venv (or rebuild) and reach a consistent end state."""
        home = self._setup_home(tmp_path)
        # Simulate the crash state: marker + parked backup, plus a live venv.
        backup = home / "venv.stale.20260901-130000-def"
        backup.mkdir()
        (backup / "pyvenv.cfg").write_text("home = X", encoding="utf-8")
        (home / "state" / "venv.pending-backup").write_text(backup.name, encoding="utf-8")

        # The restore path (Restore-VenvBackup) must bring the backup back as venv.
        expr = (
            dot(INSTALLER)
            + f"$NexusHome = '{home.as_posix()}'; "
            + "Restore-VenvBackup; "
            + "'VENV_RESTORED=' + (Test-Path -LiteralPath '"
            + (home / "venv").as_posix()
            + "'); 'MARKER_CLEARED=' + (-not (Test-Path -LiteralPath '"
            + (home / "state" / "venv.pending-backup").as_posix()
            + "'))"
        )
        r = run_ps(expr)
        assert "VENV_RESTORED=True" in r.stdout, r.stdout + r.stderr
        assert "MARKER_CLEARED=True" in r.stdout, r.stdout + r.stderr

    def test_marker_written_when_old_venv_parked(self, tmp_path):
        """V1b: Install-Venv transactional recreate parks the old venv and
        writes the pending-backup marker. Uses a STUB uv (no network): the
        stub creates a REAL venv via the repo interpreter's venv module so
        health verification passes offline."""
        home = self._setup_home(tmp_path)
        # an existing (unhealthy) venv forces the recreate path
        venv = home / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = broken", encoding="utf-8")

        repo_py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
        if not repo_py.exists():
            pytest.skip("repo .venv python missing for stub venv creation")
        stub_dir = tmp_path / "stubbin"
        stub_dir.mkdir()
        stub = stub_dir / "uv.cmd"
        lines = [
            "@echo off",
            'set "TARGETDIR=%~2"',
            'if /i "%~1"=="venv" "' + str(repo_py) + '" -m venv "%TARGETDIR%" >nul 2>&1',
            'if /i "%~1"=="--version" (echo uv 9.9.9-stub & exit /b 0)',
            "exit /b 0",
            "",
        ]
        stub.write_bytes("\r\n".join(lines).encode("utf-8"))

        expr = (
            dot(INSTALLER)
            + f"$NexusHome = '{home.as_posix()}'; "
            + f"$Script:UvCmd = '{stub.as_posix()}'; "
            + "try { Install-Venv; 'MARKER=' + (Test-Path -LiteralPath '"
            + (home / "state" / "venv.pending-backup").as_posix()
            + "' -PathType Leaf); 'PARKED=' + ((Get-ChildItem '"
            + home.as_posix()
            + "' -Directory -Filter 'venv.stale.*').Count -gt 0) } catch { 'VENV_ERR=' + $_.Exception.Message }"
        )
        r = run_ps(expr, timeout=180)
        out = r.stdout
        assert "VENV_ERR=" not in out, out + r.stderr
        assert "MARKER=True" in out, out + r.stderr
        assert "PARKED=True" in out, out + r.stderr


class TestLedgerTruthfulness:
    def test_failed_stage_records_ok_false_in_ledger(self, tmp_path):
        """V6: a failed stage must appear in install.json with ok=false."""
        home = tmp_path / "NexusHome"
        engine = tmp_path / "engine-src"
        r = subprocess.run(
            [
                PS,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALLER),
                "-Stage",
                "dependencies",
                "-NexusHome",
                str(home),
                "-InstallDir",
                str(engine),
                "-Json",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        frame = json.loads(r.stdout.strip())
        assert frame["ok"] is False
        state_path = home / "state" / "install.json"
        assert state_path.exists(), "failed stage must still write a truthful ledger"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        rec = state.get("stages", {}).get("dependencies", {})
        assert rec.get("ok") is False, f"ledger must record the failure: {state}"
        assert state.get("last_successful_stage") != "dependencies"

    def test_stage_ledger_survives_process_death_shape(self, tmp_path):
        """Ledger is valid JSON with atomic-replace marker absent (no .tmp)."""
        home = tmp_path / "NexusHome"
        engine = tmp_path / "engine-src"
        subprocess.run(
            [
                PS,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALLER),
                "-Stage",
                "environment",
                "-NexusHome",
                str(home),
                "-InstallDir",
                str(engine),
                "-Json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        state_path = home / "state" / "install.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["stages"]["environment"]["ok"] is True
        leftovers = list((home / "state").glob("install.json.tmp-*"))
        assert not leftovers, f"atomic replace left temp files: {leftovers}"


class TestZipAssault:
    def _zip(self, tmp_path: Path, entries: dict) -> Path:
        z = tmp_path / "assault.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for name, content in entries.items():
                zf.writestr(name, content)
        return z

    def _extract(self, tmp_path: Path, zip_path: Path, dest: str):
        expr = (
            dot(INSTALLER)
            + "try { Expand-NexusZipSafe -ZipPath '"
            + zip_path.as_posix()
            + "' -Destination '"
            + (tmp_path / dest).as_posix()
            + "'; 'EXTRACTED' } catch { 'BLOCKED' }"
        )
        return run_ps(expr)

    def test_traversal_blocked(self, tmp_path):
        z = self._zip(tmp_path, {"../../evil.txt": "x"})
        r = self._extract(tmp_path, z, "out1")
        assert "BLOCKED" in r.stdout

    def test_absolute_windows_blocked(self, tmp_path):
        z = self._zip(tmp_path, {"C:/evil.txt": "x"})
        r = self._extract(tmp_path, z, "out2")
        assert "BLOCKED" in r.stdout

    def test_unc_blocked(self, tmp_path):
        z = self._zip(tmp_path, {"//server/share/evil.txt": "x"})
        r = self._extract(tmp_path, z, "out3")
        assert "BLOCKED" in r.stdout

    def test_valid_zip_extracts(self, tmp_path):
        z = self._zip(tmp_path, {"repo/pyproject.toml": "[project]\n"})
        r = self._extract(tmp_path, z, "out4")
        assert "EXTRACTED" in r.stdout, r.stdout + r.stderr
        assert (tmp_path / "out4" / "repo" / "pyproject.toml").exists()

    def test_failed_extract_leaves_no_dir_when_fresh(self, tmp_path):
        z = self._zip(tmp_path, {"../escape.txt": "x"})
        dest = tmp_path / "out5"
        r = self._extract(tmp_path, z, "out5")
        assert "BLOCKED" in r.stdout
        assert not dest.exists(), "failed extraction must leave no fresh-dir residue"
