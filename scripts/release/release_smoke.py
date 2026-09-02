"""release_smoke.py - single-command highest-value release acceptance runner.

Orchestrates the release gates end-to-end in isolated temp homes:
  INSTALL -> VERIFY/DOCTOR -> UPDATE -> INTERRUPT(real kill) -> RECOVER
  -> REPAIR -> VERIFY -> NO_UPDATE

Wraps the focused harnesses:
  - installer stage flow (local-origin repo via installer tests' fixtures)
  - scripts/release/process_kill_smoke.py (GATE 07/08)
  - scripts/release/bootstrap_smoke.py (GATE 18-20; opt-in via --bootstrap)

Usage:
  .venv/Scripts/python.exe scripts/release/release_smoke.py [--bootstrap] [--quick]
Exit 0 only when every executed gate passes.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


def run_py(script: Path, *args: str, timeout: int = 1800) -> tuple[int, str]:
    r = subprocess.run(
        [str(PY), str(script), *args],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace", check=False,
    )
    return r.returncode, (r.stdout + "\n" + r.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", action="store_true",
                    help="also run the bounded public bootstrap smoke (real network)")
    ap.add_argument("--quick", action="store_true",
                    help="skip the installer pytest suite (smokes only)")
    args = ap.parse_args()

    gates: list[tuple[str, str, str]] = []  # (name, status, evidence)

    if not args.quick:
        print("== Installer pytest suite ==")
        r = subprocess.run(
            [str(PY), "-m", "pytest", "tests/installer/", "-q", "--no-header"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=1800, encoding="utf-8", errors="replace", check=False,
        )
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
        gates.append(("GATE 00 installer suite", "PASS" if r.returncode == 0 else "FAIL", tail[0]))

    print("== Real process-kill + recovery ==")
    rc, out = run_py(REPO_ROOT / "scripts" / "release" / "process_kill_smoke.py")
    gates.append(("GATE 07/08 real kill + recovery", "PASS" if rc == 0 else "FAIL",
                  "\n".join(l for l in out.splitlines() if "GATE" in l or "RESULT" in l)[:300]))

    print("== Public bootstrap ==")
    if args.bootstrap:
        rc, out = run_py(REPO_ROOT / "scripts" / "release" / "bootstrap_smoke.py", timeout=2400)
        gates.append(("GATE 18-20 public bootstrap", "PASS" if rc == 0 else "FAIL",
                      "\n".join(l for l in out.splitlines() if "GATE" in l or "RESULT" in l)[:300]))
    else:
        gates.append(("GATE 18-20 public bootstrap", "SKIPPED (opt-in via --bootstrap)", ""))

    print("\nRELEASE SMOKE")
    print(f"{'GATE':<42} {'STATUS':<8} EVIDENCE")
    for name, status, ev in gates:
        print(f"{name:<42} {status:<8} {ev.replace(chr(10), ' | ')[:120]}")
    failed = [g for g in gates if g[1] == "FAIL"]
    print("VERDICT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
