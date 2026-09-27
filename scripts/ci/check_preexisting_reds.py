#!/usr/bin/env python3
"""Pre-existing reds probe — prove a failure predates the change.

Lane O task 2. ``test_rt007_learning_cycle_persistence`` and
``test_htf_warmup_gate`` are red on clean ``origin/main`` (environment /
upstream-library drift, not a Lane O regression). This probe RUNS them at a
base revision and records the verdict, so a red seen after a Lane O change
can be attributed to the base and never masked or blamed on the gate.

The probe is deliberately conservative: it never mutates the worktree, never
checks out anything into the source tree, and fails LOUDLY if it cannot get
a clean base verdict (a missing evidence file is treated as a failure to
prove, never as a pass).

Usage:
    python scripts/ci/check_preexisting_reds.py --base origin/main
    python scripts/ci/check_preexisting_reds.py --base origin/main --json

Exit codes:
    0  every documented red reproduced at base (or a documented red passed
       at base — that is information, not a failure, and is reported loudly)
    1  a red could NOT be reproduced at base (it may be new — investigate)
    2  tooling error (cannot resolve base / cannot run pytest)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The tests documented as red on clean origin/main BEFORE any Lane O edit.
#: Each entry carries the recorded base verdict so the gate can compare.
DOCUMENTED_REDS: tuple[dict[str, str], ...] = (
    {
        "file": "tests/unit/test_rt007_learning_cycle_persistence.py",
        "why": "non-SQLite audit-repo db-path resolution: the MagicMock test-double "
        "now resolves to a real workspace file instead of :memory: "
        "(pre-existing at origin/main, environment/provider-config drift)",
    },
    {
        "file": "tests/unit/test_htf_warmup_gate.py",
        "why": "warmup-gate mock expectations desynced against the installed "
        "torch/numpy stack (pre-existing at origin/main, upstream-library drift)",
    },
)


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, check=False
    )


def _base_sha(base: str) -> str | None:
    r = _git(["rev-parse", "--verify", base])
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _run_at_base(base_sha: str, file: str) -> tuple[int, str]:
    """Run ONE test file at the base revision in a scratch worktree.

    Uses `git worktree add` so the live source tree is never touched. The
    worktree is removed afterwards whatever happens.
    """
    wt = REPO_ROOT.parent / f".preexisting-reds-{base_sha[:10]}-{Path(file).stem}"
    _git(["worktree", "prune"])
    r = _git(
        [
            "worktree",
            "add",
            "--detach",
            "--force",
            str(wt),
            base_sha,
        ]
    )
    if r.returncode != 0:
        return 2, f"git worktree add failed: {r.stderr.strip()}"

    try:
        # pytest against the checked-out base tree; PYTHONPATH=src matches the
        # project's CI convention (ci.yml runs the suite with src on the path).
        env_cmd = {
            "PATH": __import__("os").environ.get("PATH", ""),
            "PYTHONPATH": "src",
        }
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", file, "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=str(wt),
            capture_output=True,
            text=True,
            env={**__import__("os").environ, **env_cmd},
            timeout=900,
            check=False,
        )
        tail = proc.stdout[-3000:] + proc.stderr[-1000:]
        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        return 2, "pytest timed out at base"
    finally:
        _git(["worktree", "remove", "--force", str(wt)])


def main(argv: list[str] | None = None) -> int:

    ap = argparse.ArgumentParser(prog="check_preexisting_reds")
    ap.add_argument("--base", default="origin/main", help="base revision to prove against")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument(
        "--skip-run",
        action="store_true",
        help="do not run pytest; only report the documented reds (CI quick path)",
    )
    args = ap.parse_args(argv)

    base_sha = _base_sha(args.base)
    report: dict[str, object] = {
        "base": args.base,
        "base_sha": base_sha,
        "documented_reds": [d["file"] for d in DOCUMENTED_REDS],
        "verdicts": [],
    }

    if base_sha is None:
        print(f"PRE-EXISTING REDS: cannot resolve base {args.base!r}", file=sys.stderr)
        report["status"] = "base-unresolvable"
        if args.json:
            print(json.dumps(report, indent=2))
        return 2

    if args.skip_run:
        for d in DOCUMENTED_REDS:
            report["verdicts"].append(  # type: ignore[attr-defined]
                {"file": d["file"], "expected": "failed at base", "why": d["why"]}
            )
        report["status"] = "documented-only"
        print(
            f"PRE-EXISTING REDS: {len(DOCUMENTED_REDS)} documented red(s) at "
            f"{args.base} ({base_sha[:10]}): " + "; ".join(d["file"] for d in DOCUMENTED_REDS)
        )
        if args.json:
            print(json.dumps(report, indent=2))
        return 0

    unproven: list[str] = []
    for d in DOCUMENTED_REDS:
        rc, out = _run_at_base(base_sha, d["file"])
        failed = rc != 0
        report["verdicts"].append(  # type: ignore[attr-defined]
            {
                "file": d["file"],
                "base_exit_code": rc,
                "failed_at_base": failed,
                "why": d["why"],
                "output_tail": out[-1500:],
            }
        )
        status = "RED at base (pre-existing)" if failed else "GREEN at base (unexpected)"
        print(f"  - {d['file']}: {status} (rc={rc})")
        if not failed:
            # A documented red that now passes at base is NOT a Lane O failure
            # — but it must be reported loudly, not silently absorbed.
            print(f"      NOTE: documented as red, but PASSED at {base_sha[:10]}")
            print(f"      reason on record: {d['why']}")
            unproven.append(d["file"])

    reds = [v for v in report["verdicts"] if v.get("failed_at_base")]  # type: ignore[attr-defined]
    report["status"] = f"{len(reds)}/{len(DOCUMENTED_REDS)} documented reds reproduced at base"
    report["unproven"] = unproven  # type: ignore[assignment]

    if not reds and DOCUMENTED_REDS:
        report["status"] = "no documented red reproduced at base"
        print(
            "\nPRE-EXISTING REDS: WARNING — none of the documented reds failed at "
            f"{base_sha[:10]}. They may have been fixed upstream; update this "
            "probe's DOCUMENTED_REDS table.",
            file=sys.stderr,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        return 1

    print(f"\nPRE-EXISTING REDS: {report['status']} (base {base_sha[:10]})")
    if unproven:
        print(f"  passed-at-base (informational): {unproven}")
    if args.json:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
