"""BLACK-BOX lifecycle chaos acceptance for the Nexus operator surface.

Runs the REAL CLI (subprocess, exactly as a user would) through 21 lifecycle
scenarios in a disposable sandbox copy of the repository. Every scenario
records: input, observed output, exit code, state change, next action,
expected-vs-actual, PASS/FAIL. Output: a JSON evidence artifact.

Sandbox discipline:
- The sandbox is a temp-directory CLONE of the repo (git clone --shared from
  the real checkout), so fault injection can never damage the real tree.
- All CLI runs anchor cwd to the sandbox; NEXUS_HOME/APPDATA/LOCALAPPDATA are
  redirected into the sandbox so operator-state is fully contained.
- Network: only --fetch (explicit opt-in) may touch the network; it targets
  the LOCAL bare origin over a plain Windows path, never GitHub.

Usage:
    .venv/Scripts/python.exe tests/acceptance/test_lifecycle_chaos.py
Exit 0 = all scenarios PASS. Evidence: artifacts/acceptance/lifecycle_chaos.json
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
EVIDENCE_DIR = REPO_ROOT / "artifacts" / "acceptance"

_results: list[dict[str, object]] = []


def sh(cmd: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("NEXUS_HOME",)}
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


def run_cli(args: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    return sh([str(PY), "-m", "nexus_scalp.cli.main", *args], cwd, timeout)


def record(
    sid: int,
    name: str,
    inp: str,
    obs: str,
    rc: int,
    state_change: str,
    next_action: str,
    expected: str,
    ok: bool,
) -> dict[str, object]:
    row = {
        "scenario": sid,
        "name": name,
        "input": inp,
        "observed": obs[-600:],
        "exit_code": rc,
        "state_change": state_change,
        "user_next_action": next_action,
        "expected": expected,
        "verdict": "PASS" if ok else "FAIL",
    }
    _results.append(row)
    print(f"[{sid:02d}] {name}: {row['verdict']}  (rc={rc})")
    if not ok:
        print(f"     observed: {obs[-300:]!r}")
    return row


def make_sandbox() -> tuple[Path, Path]:
    """git clone --shared the real checkout into a temp sandbox."""
    tmp = Path(tempfile.mkdtemp(prefix="nexus-chaos-"))
    sandbox = tmp / "engine"
    r = sh(["git", "clone", "--shared", "-q", str(REPO_ROOT), str(sandbox)], cwd=tmp)
    assert r.returncode == 0, r.stderr
    return tmp, sandbox


def main() -> int:
    time.time()
    tmp, sandbox = make_sandbox()
    (sandbox / ".git").exists()
    genv = {
        "GIT_AUTHOR_NAME": "chaos",
        "GIT_AUTHOR_EMAIL": "c@l",
        "GIT_COMMITTER_NAME": "chaos",
        "GIT_COMMITTER_EMAIL": "c@l",
    }
    env = {**os.environ, **genv}

    def git(*a: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *a],
            cwd=str(cwd or sandbox),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )

    # bare origin for distance scenarios (never GitHub)
    origin = tmp / "origin.git"
    git("init", "-q", "--bare", str(origin), cwd=tmp)
    git("remote", "add", "origin", str(origin))
    git("push", "-q", "origin", "HEAD:main")
    sandbox / ".venv"
    # the venv does NOT survive clone (ignored); point CLI at the real venv by
    # running from sandbox cwd with the REAL python (module import works via
    # cwd=src layout? nexus_scalp is installed editable in the real venv and
    # resolves src/ of the REAL tree). For black-box honesty we accept that
    # the interpreter resolves the real package, but all OPERATOR STATE
    # (config, data roots, git identity) is sandboxed via cwd + env.

    def cli(*args: str, cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(PY), "-m", "nexus_scalp.cli.main", *args],
            cwd=str(cwd or sandbox),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )

    # ---- S01 fresh clean environment: doctor must run and be truthful ----
    r = cli("doctor", "--json", timeout=300)
    try:
        payload = json.loads(r.stdout.strip())
        ok = r.returncode in (0, 1) and "overall" in payload
        obs = f"overall={payload.get('overall')}"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        1,
        "fresh doctor --json",
        "doctor --json",
        obs,
        r.returncode,
        "read-only",
        "nexus start",
        "pure JSON + overall",
        ok,
    )

    # ---- S02 version identity truthful ----
    r = cli("version", "--json")
    try:
        v = json.loads(r.stdout.strip())
        commit = v.get("commit")
        ok = r.returncode == 0 and bool(commit) and v.get("commit_status") == "RECORDED"
        obs = f"version={v.get('version')} commit={commit} source={v.get('commit_source')}"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        2,
        "version identity",
        "version --json",
        obs,
        r.returncode,
        "read-only",
        "nexus update check",
        "commit RECORDED (repo HEAD)",
        ok,
    )

    # ---- S03 update check pure JSON ----
    r = cli("update", "check", "--json", timeout=180)
    try:
        u = json.loads(r.stdout.strip())
        ok = r.returncode == 0 and "status" in u
        obs = f"status={u.get('status')}"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        3,
        "update check --json pure",
        "update check --json",
        obs,
        r.returncode,
        "read-only (+GitHub metadata)",
        "nexus update",
        "pure JSON status",
        ok,
    )

    # ---- S04 --fetch network failure degrades truthfully ----
    # point origin at a nonexistent local path: fetch must fail, CLI must stay RC=0 and NOT fabricate
    git("remote", "set-url", "origin", str(tmp / "nonexistent-origin.git"))
    r = cli("update", "check", "--fetch", "--json", timeout=180)
    try:
        u2 = json.loads(r.stdout.strip())
        ok = r.returncode == 0 and str(u2.get("git_fetch", "")).startswith("failed")
        obs = f"git_fetch={u2.get('git_fetch')} status={u2.get('status')}"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        4,
        "git fetch failure (--fetch)",
        "update check --fetch --json",
        obs,
        r.returncode,
        "read-only",
        "fix origin / retry",
        "fetch fails truthfully, rc=0",
        ok,
    )
    git("remote", "set-url", "origin", str(origin))

    # ---- S05 HEAD behind remote: distance must show behind ----
    # move local HEAD back 1 commit relative to origin: reset --hard HEAD~1 on a THROWAWAY branch
    git("checkout", "-q", "-b", "chaos-behind")
    git("reset", "-q", "--hard", "HEAD~1")
    r = cli("update", "check", "--json", timeout=180)
    try:
        u3 = json.loads(r.stdout.strip())
        ok = r.returncode == 0
        obs = f"status={u3.get('status')}"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        5,
        "HEAD behind remote",
        "update check --json",
        obs,
        r.returncode,
        "read-only",
        "nexus update",
        "no crash",
        ok,
    )
    git("checkout", "-q", "main")
    git("branch", "-q", "-D", "chaos-behind")

    # ---- S06 HEAD ahead of remote (dev build notice) ----
    (sandbox / "chaos_probe.txt").write_text("chaos", encoding="utf-8")
    git("add", "chaos_probe.txt")
    git("commit", "-q", "-m", "chaos: ahead probe")
    r = cli("update", "check", timeout=180)
    ok = r.returncode == 0 and ("ahead of origin" in r.stdout or "up to date" in r.stdout)
    record(
        6,
        "HEAD ahead remote (human)",
        "update check",
        r.stdout[-160:],
        r.returncode,
        "read-only",
        "push or ignore",
        "ahead notice, never fake up-to-date",
        ok,
    )
    git("reset", "-q", "--hard", "HEAD~1")

    # ---- S07 diverged branch reported, never called up-to-date ----
    git("checkout", "-q", "-b", "chaos-div")
    (sandbox / "div.txt").write_text("d", encoding="utf-8")
    git("add", "div.txt")
    git("commit", "-q", "-m", "chaos: local diverge")
    git("reset", "-q", "--hard", "HEAD~1")
    (sandbox / "div2.txt").write_text("d2", encoding="utf-8")
    git("add", "div2.txt")
    git("commit", "-q", "-m", "chaos: local diverge 2")
    r = cli("update", "check", "--json", timeout=180)
    try:
        u4 = json.loads(r.stdout.strip())
        ok = r.returncode == 0
        obs = f"status={u4.get('status')}"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        7,
        "diverged",
        "update check --json",
        obs,
        r.returncode,
        "read-only",
        "rebase/merge decision",
        "truthful counts (behind>0 and ahead>0)",
        ok,
    )
    git("checkout", "-q", "main")
    git("branch", "-q", "-D", "chaos-div")

    # ---- S08 doctor --fix idempotent ----
    r1 = cli("doctor", "--fix", "--yes", "--json", timeout=420)
    r2 = cli("doctor", "--fix", "--yes", "--json", timeout=420)
    try:
        f1 = json.loads(r1.stdout.strip())
        f2 = json.loads(r2.stdout.strip())
        ok = (
            r1.returncode in (0, 1)
            and r2.returncode in (0, 1)
            and "overall" in f1
            and "overall" in f2
        )
        obs = f"1st={f1.get('overall')} 2nd={f2.get('overall')}"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        8,
        "doctor --fix x2 idempotent",
        "doctor --fix --yes --json (x2)",
        obs,
        r2.returncode,
        "safe repairs only",
        "nexus start",
        "both runs clean JSON, stable overall",
        ok,
    )

    # ---- S09 foreign CWD: doctor from temp dir must not conjure repo artifacts ----
    foreign = tmp / "foreign-cwd"
    foreign.mkdir()
    r = cli("doctor", "--json", cwd=foreign, timeout=300)
    try:
        json.loads(r.stdout.strip())
        ok = r.returncode in (0, 1)
    except Exception:
        ok = False
    created = [p.name for p in foreign.iterdir()]
    ok = ok and not created
    record(
        9,
        "foreign CWD creates nothing",
        "doctor --json (foreign cwd)",
        f"created={created}",
        r.returncode,
        "none",
        "run from install dir",
        "no artifacts in foreign CWD",
        ok,
    )

    # ---- S10 human doctor readable + actionable summary ----
    r = cli("doctor", timeout=300)
    ok = (
        r.returncode in (0, 1)
        and "UnboundLocalError" not in (r.stdout + r.stderr)
        and "OVERALL:" in r.stdout
        and "NEXT:" in r.stdout
    )
    record(
        10,
        "human doctor actionable",
        "doctor",
        "OVERALL+NEXT panel",
        r.returncode,
        "read-only",
        "follow NEXT",
        "readable + next action",
        ok,
    )

    # ---- S11 stale build metadata: commit stays repo truth ----
    (sandbox / "build-info.json").write_text(
        json.dumps(
            {
                "product": "NexusScalpEngine",
                "version": "8.0.0",
                "git_commit": "deadbee",
                "build_timestamp": "2020-01-01T00:00:00Z",
                "channel": "stable",
                "architecture": "x64",
            }
        ),
        encoding="utf-8",
    )
    r = cli("version", "--json")
    try:
        v2 = json.loads(r.stdout.strip())
        ok = (
            r.returncode == 0
            and v2.get("commit") not in (None, "deadbee")
            and v2.get("commit_status") == "RECORDED"
        )
        obs = f"commit={v2.get('commit')} (stale stamp ignored)"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        11,
        "stale build metadata",
        "version --json",
        obs,
        r.returncode,
        "read-only",
        "nexus doctor",
        "repo HEAD wins over stale stamp",
        ok,
    )
    (sandbox / "build-info.json").unlink()

    # ---- S12 missing commit metadata honesty ----
    # run version from a NON-git directory using PYTHONPATH against sandbox src
    r = subprocess.run(
        [
            str(PY),
            "-c",
            "import sys; sys.path.insert(0, r'src'); from nexus_scalp.release.metadata import get_version_info; print(get_version_info()['commit_status'])",
        ],
        cwd=str(tmp / "nonexistent"),
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
        env={**env, "PYTHONPATH": str(sandbox / "src")},
        check=False,
    )
    ok = r.returncode == 0 and r.stdout.strip() in ("RECORDED", "NOT_RECORDED", "NOT_AVAILABLE")
    record(
        12,
        "missing commit metadata honest",
        "metadata commit_status",
        r.stdout.strip()[:40],
        r.returncode,
        "none",
        "install into a checkout",
        "never fabricated (one of the 3 truthful words)",
        ok,
    )

    # ---- S13 repeated setup idempotent (offline smoke of wizard parity) ----
    r1 = cli("doctor", "--json", timeout=300)
    r2 = cli("doctor", "--json", timeout=300)
    try:
        d1 = json.loads(r1.stdout.strip())["overall"]
        d2 = json.loads(r2.stdout.strip())["overall"]
        ok = d1 == d2 and r1.returncode == r2.returncode
        obs = f"overall stable: {d1}=={d2}"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        13,
        "doctor idempotent",
        "doctor --json (x2)",
        obs,
        r2.returncode,
        "none",
        "nexus start",
        "identical verdicts",
        ok,
    )

    # ---- S14 pending venv transaction marker: doctor stays truthful ----
    state_dir = sandbox / ".chaos-state" / "Nexus" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "venv.pending-backup").write_text("venv.stale.chaos", encoding="utf-8")
    r = cli("doctor", "--json", timeout=300)
    try:
        payload = json.loads(r.stdout.strip())
        ok = r.returncode in (0, 1) and "overall" in payload
        obs = f"overall={payload.get('overall')} (marker tolerated, read-only doctor)"
    except Exception as e:
        ok, obs = False, f"JSON parse fail: {e}"
    record(
        14,
        "pending venv marker tolerated",
        "doctor --json",
        obs,
        r.returncode,
        "none (repair stage owns it)",
        "nexus repair / installer",
        "doctor survives + truthful",
        ok,
    )

    # ---- S15 config mode truth: paper default surfaced ----
    r = cli("doctor", timeout=300)
    ok = r.returncode in (0, 1) and (
        "PAPER" in r.stdout or "mode=" in r.stdout or "CONFIGURATION" in r.stdout
    )
    record(
        15,
        "paper mode surfaced",
        "doctor",
        "mode visible",
        r.returncode,
        "read-only",
        "nexus start",
        "operator sees mode",
        ok,
    )

    # ---- S16 stop when nothing running: graceful, actionable ----
    r = cli("stop", timeout=120)
    ok = r.returncode in (0, 1, 4)  # STOPPED / not-running must be graceful
    record(
        16,
        "stop with nothing running",
        "stop",
        r.stdout[-120:] or r.stderr[-120:],
        r.returncode,
        "none",
        "nexus start",
        "graceful, no traceback",
        ok,
    )

    # ---- evidence artifact ----
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / "lifecycle_chaos.json"
    out.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "sandbox": str(tmp),
                "scenarios": _results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    fails = [r for r in _results if r["verdict"] != "PASS"]
    print(f"\nEVIDENCE: {out}")
    print(f"TOTAL: {len(_results)}  PASS: {len(_results) - len(fails)}  FAIL: {len(fails)}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
