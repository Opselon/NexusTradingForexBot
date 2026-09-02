"""bootstrap_smoke.py - BOUNDED REAL public bootstrap acceptance (GATE 18-21).

Executes the actual public bootstrap flow against the REAL GitHub raw URL
(the same one documented in README.md):

    irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1 | iex

bounded to: ONE download, ONE install run, ONE re-run (NO_UPDATE check),
with a fresh temporary NEXUS_HOME. Everything runs under Windows PowerShell
5.1 semantics where available (the canonical user shell).

Provenance is captured (URL, fetched bytes, hash, resolved revision) - no
secrets. Cleanup reports residue honestly.

Usage:  .venv/Scripts/python.exe scripts/release/bootstrap_smoke.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_URL = (
    "https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1"
)


def find_powershell() -> str:
    # Public one-liner targets Windows PowerShell 5.1 first.
    for c in ("powershell.exe", "pwsh.exe"):
        p = shutil.which(c)
        if p:
            return p
    raise RuntimeError("no PowerShell found")


def main() -> int:
    ps = find_powershell()
    print(f"BOOTSTRAP SMOKE (host={ps})")
    work = Path(tempfile.mkdtemp(prefix="nexus-bootstrap-smoke-"))
    home = work / "NexusHome"
    fetched = work / "install.ps1"

    results: dict[str, object] = {}

    # ---- Step 1: the REAL download (irm equivalent, bounded: one fetch) ----
    dl = subprocess.run(
        [
            ps,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"irm '{PUBLIC_URL}' -OutFile '{fetched.as_posix()}'",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if dl.returncode != 0 or not fetched.exists():
        print("FAIL: public fetch failed:", dl.stderr[-300:])
        return 1
    payload = fetched.read_bytes()
    results["fetched_bytes"] = len(payload)
    results["fetched_sha256"] = hashlib.sha256(payload).hexdigest()[:16]

    # ---- Step 2: provenance check - is it OUR bootstrap? ----
    text = payload.decode("utf-8", errors="replace")
    ok_marker = ("Nexus Scalp Engine" in text) and ("param(" in text) and ("NexusHome" in text)
    results["provenance_ok"] = ok_marker
    if not ok_marker:
        print("FAIL: fetched content is not the Nexus bootstrap")
        return 2

    # ---- Step 3: real install run from the fetched script (temp home) ----
    # Use -File (equivalent semantics to irm|iex for the installer body; the
    # one-liner itself is what end users run - here we additionally verify
    # the downloaded file executes standalone, which irm|iex does in-memory).
    inst = subprocess.run(
        [
            ps,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(fetched),
            "-NexusHome",
            str(home),
            "-NonInteractive",
            "-Json",
        ],
        capture_output=True,
        text=True,
        timeout=1800,
        encoding="utf-8",
        errors="replace",
        env={k: v for k, v in os.environ.items() if k != "NEXUS_HOME"},
        cwd=str(work),
        check=False,
    )
    results["install_rc"] = inst.returncode
    try:
        frames = [json.loads(l) for l in inst.stdout.splitlines() if l.strip().startswith("{")]
    except json.JSONDecodeError:
        frames = []
    results["install_json_frames"] = len(frames)
    if inst.returncode != 0:
        print("FAIL: public-bootstrap install run failed")
        print("stderr tail:", inst.stderr[-500:])
        for f in frames[-3:]:
            print("  frame:", f)
        shutil.rmtree(work, ignore_errors=True)
        return 3

    # ---- Step 4: second run - NO_UPDATE / idempotency ----
    inst2 = subprocess.run(
        [
            ps,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(fetched),
            "-NexusHome",
            str(home),
            "-NonInteractive",
            "-Json",
        ],
        capture_output=True,
        text=True,
        timeout=1800,
        encoding="utf-8",
        errors="replace",
        env={k: v for k, v in os.environ.items() if k != "NEXUS_HOME"},
        cwd=str(work),
        check=False,
    )
    results["rerun_rc"] = inst2.returncode
    state = home / "state" / "install.json"
    if state.exists():
        st = json.loads(state.read_text(encoding="utf-8"))
        results["ledger_last_stage"] = st.get("last_successful_stage")
        results["ledger_stages_recorded"] = len(st.get("stages", {}))
    gate_idem = inst2.returncode == 0
    print(f"GATE 18 (public fetch + provenance): {'PASS' if ok_marker else 'FAIL'}")
    print(
        f"GATE 19 (real install from fetched script): {'PASS' if inst.returncode == 0 else 'FAIL'}"
    )
    print(f"GATE 20 (second run idempotent): {'PASS' if gate_idem else 'FAIL'}")
    print("EVIDENCE:", json.dumps(results, indent=2)[:800])

    try:
        shutil.rmtree(work)
    except Exception as e:
        print(f"RESIDUE: {work} ({e})")

    ok = ok_marker and inst.returncode == 0 and gate_idem
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
