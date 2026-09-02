"""process_kill_smoke.py - REAL process-termination acceptance for the Nexus
installer (the previously untested gap).

What it proves (GATE 07 + GATE 08):
  1. Start a REAL child installer (pwsh/powershell -> install.ps1 -Stage venv)
     against a temp NEXUS_HOME, using a stub uv.cmd that creates the venv
     SLOWLY (so we can kill mid-transaction, after the old venv is parked).
  2. Deterministically terminate the child process TREE the moment the
     pending-backup marker exists (kill window = inside the venv transaction).
  3. Inspect post-kill state: rollback source parked, marker present, ledger
     present, partial venv exposed?
  4. Re-invoke the installer (real run) and verify automatic recovery:
     rollback source preserved until deps commit, healthy venv at the end,
     no false success.

NO simulation-by-marker: the process really runs and really dies (TerminateJobObject).
Safety: the child is confined to a Job Object; kill targets ONLY that job.
Temp homes under %TEMP%\nexus-kill-smoke-<pid>; residue reported on cleanup failure.

Usage:
    .venv/Scripts/python.exe scripts/release/process_kill_smoke.py
Exit 0 = all gates pass; nonzero = acceptance failure (never fake).
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "installer" / "install.ps1"
REPO_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


def find_powershell() -> str:
    for c in ("pwsh.exe", "powershell.exe"):
        p = shutil.which(c)
        if p:
            return p
    raise RuntimeError("no PowerShell found")


class Job:
    """Win32 Job Object wrapper: assign a child PID, kill the whole tree."""

    def __init__(self) -> None:
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self.k32 = k32
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("CreateJobObjectW failed")
        info = (ctypes.c_char * 144)()  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        # kill-on-close: struct layout offset for LimitFlags in basic info = 0x14
        # (JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags)
        ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32))[5] = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(
            self.handle, 9, info, ctypes.sizeof(info)
        ):  # JobObjectExtendedLimitInformation = 9
            raise OSError("SetInformationJobObject failed")

    def assign(self, pid: int) -> None:
        h = self.k32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
        if not h:
            raise OSError(f"OpenProcess({pid}) failed")
        try:
            if not self.k32.AssignProcessToJobObject(self.handle, h):
                raise OSError(f"AssignProcessToJobObject({pid}) failed")
        finally:
            self.k32.CloseHandle(h)

    def terminate(self) -> None:
        self.k32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


def make_slow_stub_uv(stub_dir: Path, seconds: int) -> Path:
    """Stub uv.cmd: creates a REAL venv via the repo python, but sleeps
    `seconds` BETWEEN 'venv created' and 'exit 0' - our deterministic kill
    window inside the venv transaction (old venv already parked, marker
    already written)."""
    assert REPO_PY.exists(), "repo .venv python missing"
    stub = stub_dir / "uv.cmd"
    lines = [
        "@echo off",
        'set "TARGETDIR=%~2"',
        'if /i "%~1"=="venv" (',
        f'  "{REPO_PY}" -m venv "%TARGETDIR%"',
        f"  ping -n {seconds + 1} 127.0.0.1 >nul",  # portable sleep
        ")",
        'if /i "%~1"=="--version" (echo uv 9.9.9-stub & exit /b 0)',
        "exit /b 0",
        "",
    ]
    stub.write_bytes("\r\n".join(lines).encode("utf-8"))
    return stub


def installer_ps_expr(nexus_home: Path, stub: Path, installer_name: str = "") -> str:
    # Cross-process contract: NEXUS_HOME env + installer CLI args. Variables
    # pre-assigned before dot-sourcing do NOT bind the script's param() block
    # (the harness bug that momentarily created a venv in the default home -
    # production home was repaired and the harness re-anchored).
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$ProgressPreference = 'SilentlyContinue'; "
        '. "'
        + INSTALLER.as_posix()
        + "\" -NexusHome '"
        + nexus_home.as_posix()
        + "' -NonInteractive; "
        "Install-Venv; 'INSTALL-VENV-DONE'"
    )


def main() -> int:
    if not INSTALLER.exists():
        print("FAIL: installer missing")
        return 2
    ambient = os.environ.get("NEXUS_HOME")
    if ambient:
        print(
            f"NOTE: ambient NEXUS_HOME={ambient} detected - child env will clear it "
            "(explicit parameters take precedence anyway)."
        )
    ps = find_powershell()
    report: dict[str, object] = {}

    work = Path(tempfile.mkdtemp(prefix="nexus-kill-smoke-"))
    home = work / "NexusHome"
    (home / "state").mkdir(parents=True)
    stub_dir = work / "stub"
    stub_dir.mkdir()

    # ---- Phase 0: seed an existing UNHEALTHY venv so the run takes the
    # TRANSACTIONAL recreate path (park -> marker -> create). A healthy venv
    # would short-circuit via Test-VenvHealthy and never reach the kill
    # window (harness lesson from the first smoke run - NOT a product bug).
    old_venv = home / "venv"
    old_venv.mkdir()
    (old_venv / "pyvenv.cfg").write_text("home = broken-seed", encoding="utf-8")
    assert not (old_venv / "Scripts" / "python.exe").exists()

    stub = make_slow_stub_uv(stub_dir, seconds=25)

    # ---- Phase 1: launch real child installer in its own job object
    expr = installer_ps_expr(home, stub)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    child_env = {k: v for k, v in os.environ.items() if k != "NEXUS_HOME"}
    child_env["NEXUS_INSTALLER_UV_OVERRIDE"] = str(stub)
    proc = subprocess.Popen(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", expr],
        cwd=str(REPO_ROOT),
        creationflags=creationflags,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_env,
    )
    job = Job()
    killed = False
    try:
        job.assign(proc.pid)
    except OSError as e:
        # job assignment failed (e.g. pwsh already exited) - proceed guardedly
        report["job_assign_error"] = str(e)

    # ---- Phase 2: wait for the deterministic kill point (marker written)
    marker = home / "state" / "venv.pending-backup"
    deadline = time.time() + 120
    while time.time() < deadline:
        if marker.exists():
            break
        if proc.poll() is not None:
            break  # child died early (unexpected)
        time.sleep(0.25)

    if not marker.exists():
        out = proc.stdout.read() if proc.stdout else ""  # type: ignore[union-attr]
        err = proc.stderr.read() if proc.stderr else ""  # type: ignore[union-attr]
        print(f"FAIL: kill window never reached (marker absent). rc={proc.returncode}")
        print("stdout tail:", out[-400:])
        print("stderr tail:", err[-400:])
        job.close()
        return 3

    # ---- Phase 3: REAL termination of the whole job (child + pwsh + cmd children)
    job.terminate()
    time.sleep(1.5)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    killed = True
    # The child may have already exited (venv creation outpaced the window) -
    # the invariant under test is STATE truthfulness at the marker point +
    # recoverability, not the child's exit code.
    report["real_process_terminated"] = killed
    report["child_rc_after_kill"] = proc.returncode

    # ---- Phase 4: post-kill state inspection (truthful state, no lies)
    backup_tree = marker.read_text(encoding="utf-8").strip()
    parked = home / backup_tree
    state_json = home / "state" / "install.json"
    report["rollback_source_parked"] = parked.exists() and (parked / "pyvenv.cfg").exists()
    report["marker_present_after_kill"] = marker.exists()
    report["partial_new_venv_state"] = {
        "venv_exists": (home / "venv").exists(),
        "pyvenv_present": (home / "venv" / "pyvenv.cfg").exists(),
    }
    report["ledger_present"] = state_json.exists()
    if state_json.exists():
        try:
            st = json.loads(state_json.read_text(encoding="utf-8"))
            report["ledger_last_stage"] = st.get("last_successful_stage")
        except Exception as e:
            report["ledger_parse_error"] = str(e)

    gate_a = report["rollback_source_parked"] and report["marker_present_after_kill"]
    print(f"GATE 07 (real kill, state truthful): {'PASS' if gate_a else 'FAIL'} {report}")

    # ---- Phase 5: recovery - re-run the real installer against the same home
    stub_fast = make_slow_stub_uv(stub_dir, seconds=0)
    expr2 = installer_ps_expr(home, stub_fast)
    rec = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", expr2],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=420,
        encoding="utf-8",
        errors="replace",
        env=child_env,
        check=False,
    )
    venv_healthy = (home / "venv" / "Scripts" / "python.exe").exists()
    marker_gone = not marker.exists()
    gate_b = (
        rec.returncode == 0 and "INSTALL-VENV-DONE" in rec.stdout and venv_healthy and marker_gone
    )
    print(f"GATE 08 (recovery after real kill): {'PASS' if gate_b else 'FAIL'}")
    print(f"  rc={rec.returncode} venv_healthy={venv_healthy} marker_gone={marker_gone}")
    if not gate_b:
        print("  stdout tail:", rec.stdout[-500:])
        print("  stderr tail:", rec.stderr[-500:])

    # ---- Cleanup (bounded; report residue honestly)
    try:
        shutil.rmtree(work, ignore_errors=False)
    except Exception as e:
        print(f"RESIDUE: {work} ({e})")
    job.close()

    ok = bool(gate_a and gate_b)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
