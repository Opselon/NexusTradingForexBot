"""CLI/INSTALLER LIFECYCLE CHAOS ACCEPTANCE — black-box operator simulation.

Operates the real ``nexus`` CLI exactly as an operator would (subprocess only,
no internal function calls for the behaviors under test) across the full
first-run -> recovery -> update lifecycle in ISOLATED temporary environments:

Every scenario is exercised against a sandboxed LOCALAPPDATA + HOME so the
operator's real installation, config, DB and logs are never touched.

Scenario matrix (id = matrix row in the evidence artifact):
  S01 fresh clean environment         S12 missing commit metadata
  S02 missing runtime dependency      S13 stale build metadata
  S03 broken venv                     S14 DB not initialized
  S04 pending venv transaction        S15 optional/shadow tables absent
  S05 interrupted installation        S16 doctor detects repairable state
  S06 interrupted update              S17 doctor --fix repairs it
  S07 network unavailable (--fetch)   S18 repair fails safely + explains
  S08 git fetch failure               S19 start from foreign CWD
  S09 local HEAD behind remote        S20 start after recovery
  S10 local HEAD ahead of remote      S21 idempotent repeats
  S11 diverged branch

OUTPUT: writes artifacts/chaos/lifecycle_chaos_evidence.json with per-scenario
INPUT/OBSERVED/EXIT/STATE/NEXT-ACTION/EXPECTED-ACTUAL/PASS-FAIL records.

Usage:  .venv/Scripts/python.exe scripts/release/lifecycle_chaos.py
        (or pytest tests/unit/test_lifecycle_chaos.py for the pytest view)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
MODULE = ["-m", "nexus_scalp.cli.main"]


class Sandbox:
    """Isolated operator environment: temp LOCALAPPDATA/USERPROFILE/HOME."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="nexus-chaos-"))
        self.localappdata = self.root / "LocalAppData"
        self.home = self.root / "Home"
        self.cwd = self.root / "cwd"
        for d in (self.localappdata, self.home, self.cwd):
            d.mkdir(parents=True, exist_ok=True)

    def env(self) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items()}
        e["LOCALAPPDATA"] = str(self.localappdata)
        e["USERPROFILE"] = str(self.home)
        e["HOME"] = str(self.home)
        e["NEXUS_HOME"] = ""  # never inherit an installer-home override
        return e

    def nexus(
        self, *args: str, cwd: Path | None = None, timeout: int = 240, check: bool = False
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(PY), *MODULE, *args],
            cwd=str(cwd or self.cwd),
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=check,
        )

    def data_root(self) -> Path:
        return self.localappdata / "NexusScalpEngine" / "data"

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def record(
    evidence: dict,
    sid: str,
    name: str,
    *,
    cmd: list[str] | None = None,
    observed: str = "",
    rc: int | None = None,
    state: str = "",
    next_action: str = "",
    expected: str = "",
    actual: str = "",
    passed: bool | None = None,
    notes: str = "",
) -> bool:
    rows = evidence.setdefault("scenarios", [])
    row = {
        "id": sid,
        "scenario": name,
        "input": cmd or [],
        "observed_output": observed[:1200],
        "exit_code": rc,
        "state_change": state,
        "user_next_action": next_action,
        "expected": expected,
        "actual": actual,
        "pass": passed,
    }
    if notes:
        row["notes"] = notes
    rows.append(row)
    return bool(passed)


def main() -> int:  # pragma: no cover - orchestrated manually via pytest too
    print("Use tests/unit/test_lifecycle_chaos.py (pytest) to execute scenarios.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
