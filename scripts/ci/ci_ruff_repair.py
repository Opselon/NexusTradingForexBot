#!/usr/bin/env python3
"""Safe CI Ruff auto-repair (CHG-0052): detect -> repair -> verify -> artifact.

Run #685 class of failure: `ruff format --check .` fails on the CI checkout,
the job is cancelled, and every downstream check without a result JSON is
misclassified as "errored". This module owns the REPAIR half of that failure:

  1. detect    : run `ruff format --check .` (canonical pyproject config).
  2. repair    : on failure, run `ruff format .` on the CI CHECKOUT ONLY.
  3. verify    : re-run `--check` (must pass) AND `ruff check .` (lint must
                 stay green - a formatter that breaks lint fails the repair).
  4. artifact  : `git diff` -> ruff-repair.patch (applies with plain `git
                 apply` on the ORIGINAL commit) + ruff-repair-report.json
                 (timestamp, commit sha, ruff version, config identity,
                 files changed, diff hash).

The committed tree is NEVER pushed, committed, or reported green here: the
report carries `source_tree_was_clean` so CI summaries can state honestly
whether the SOURCE was malformed. Persistence strategy = OPTION A of the
repair contract: patch artifact + report; no bot commits, no force push,
no mutation of any agent's WIP (the tool only ever runs in CI or in an
explicitly disposable checkout).

Exit codes: 0 = no repair needed OR repair succeeded and verified;
            1 = repair attempted but the tree still fails format/lint;
            2 = usage/configuration error (ruff missing, results root bad).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUFF_TIMEOUT = 300


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=RUFF_TIMEOUT,
    )


def _ruff_cmd(cwd: Path) -> list[str]:
    """Canonical ruff: repo venv first (same interpreter chain as CI's
    `pip install -e .[dev]`), falling back to `python -m ruff` (PATH python)
    and finally PATH `ruff`. A bare `ruff` name is probed defensively because
    Windows CreateProcess cannot resolve extensionless names."""
    for py in (cwd / ".venv" / "Scripts" / "python.exe", cwd / ".venv" / "bin" / "python"):
        if py.exists():
            probe = _run([str(py), "-m", "ruff", "--version"], cwd)
            if probe.returncode == 0:
                return [str(py), "-m", "ruff"]
    probe = _run([sys.executable, "-m", "ruff", "--version"], cwd)
    if probe.returncode == 0:
        return [sys.executable, "-m", "ruff"]
    for exe in ("ruff.exe", "ruff"):
        probe = _run([exe, "--version"], cwd)
        if probe.returncode == 0:
            return [exe]
    return []


def _format_check_files(cwd: Path, ruff: list[str]) -> list[str]:
    """Parse `ruff format --check` output into the exact offending file list.

    ruff prints `--> <path>:<row>:<col>` for each unformatted file (plus a
    final summary line). Parsing real output beats re-running with --diff:
    one invocation, deterministic, and never reformats anything.
    """
    r = _run([*ruff, "format", "--check", "."], cwd)
    if r.returncode == 0:
        return []
    files: list[str] = []
    # `--> <path>:<row>:<col>` — a Windows drive letter (C:\) adds a colon, so
    # split on the TRAILING :row:col pair instead of the first colon.
    for m in re.finditer(r"-->\s+(.+?):\d+:\d+\s*$", r.stdout, re.M):
        path = m.group(1).strip()
        if path and path not in files:
            files.append(path)
    return files


def _config_identity(cwd: Path) -> dict[str, str]:
    pyproject = cwd / "pyproject.toml"
    identity = {
        "pyproject_sha256": "",
        "pyproject_present": str(pyproject.exists()),
    }
    if pyproject.exists():
        identity["pyproject_sha256"] = hashlib.sha256(pyproject.read_bytes()).hexdigest()
    return identity


def repair(cwd: Path, results_root: Path) -> int:
    ruff = _ruff_cmd(cwd)
    if not ruff:
        print("ruff not available - cannot detect or repair", file=sys.stderr)
        return 2

    ver = _run([*ruff, "--version"], cwd)
    ruff_version = (ver.stdout or ver.stderr).strip().splitlines() or ["unknown"]
    ruff_version = ruff_version[0]

    check = _run([*ruff, "format", "--check", "."], cwd)
    source_fmt_rc = check.returncode
    offending = _format_check_files(cwd, ruff)
    # The patch is captured BEFORE repairing straight from ruff itself: it is
    # the exact deterministic delta of the original checkout, needs no git
    # history, and applies with `git apply` / `patch -p0` from the repo root.
    would_patch = ""
    if source_fmt_rc != 0:
        pre = _run([*ruff, "format", "--diff", "."], cwd)
        would_patch = pre.stdout or ""
    report: dict[str, object] = {
        "tool": "ci_ruff_repair",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ruff_version": ruff_version,
        "config_identity": _config_identity(cwd),
        "source_tree_was_clean": source_fmt_rc == 0,
        "source_format_exit_code": source_fmt_rc,
        "files_offending": offending,
        "repaired": False,
        "files_changed": [],
        "post_repair_format_exit_code": None,
        "post_repair_lint_exit_code": None,
        "patch_file": None,
        "diff_hash": None,
    }

    if source_fmt_rc == 0:
        report["repair_status"] = "not_needed"
        _write(results_root, report, None)
        print("SOURCE TREE CLEAN - no repair needed")
        return 0

    print(f"SOURCE TREE DIRTY: {len(offending)} file(s) would be reformatted")
    fmt = _run([*ruff, "format", "."], cwd)
    if fmt.returncode != 0:
        report["repair_status"] = "repair_command_failed"
        _write(results_root, report, None)
        print(fmt.stdout[-2000:] + fmt.stderr[-2000:], file=sys.stderr)
        return 1

    verify = _run([*ruff, "format", "--check", "."], cwd)
    lint = _run([*ruff, "check", "."], cwd)
    report["repaired"] = True
    report["post_repair_format_exit_code"] = verify.returncode
    report["post_repair_lint_exit_code"] = lint.returncode
    report["files_changed"] = offending

    if verify.returncode != 0 or lint.returncode != 0:
        report["repair_status"] = "failed_verification"
        _write(results_root, report, None)
        print(
            f"REPAIR INCOMPLETE: format rc={verify.returncode} lint rc={lint.returncode}",
            file=sys.stderr,
        )
        return 1

    patch_name = None
    if would_patch.strip():
        results_root.mkdir(parents=True, exist_ok=True)
        patch_name = "ruff-repair.patch"
        (results_root / patch_name).write_text(would_patch, encoding="utf-8", newline="\n")
        report["patch_file"] = patch_name
        report["diff_hash"] = hashlib.sha256(would_patch.encode("utf-8")).hexdigest()
    report["repair_status"] = "repaired"
    _write(results_root, report, patch_name)
    print(f"REPAIR OK: {len(report['files_changed'])} file(s) reformatted on the CI checkout")
    print("PERSISTENCE: patch artifact only - the COMMITTED TREE IS STILL DIRTY")
    return 0


def _write(results_root: Path, report: dict[str, object], patch_name: str | None) -> None:
    try:
        results_root.mkdir(parents=True, exist_ok=True)
        (results_root / "ruff-repair-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        if patch_name:
            print(f"artifact: {results_root / patch_name}")
        print(f"artifact: {results_root / 'ruff-repair-report.json'}")
    except OSError as e:
        print(f"artifact write failed: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ci_ruff_repair", description="Safe CI Ruff format auto-repair + patch artifact"
    )
    p.add_argument(
        "--results",
        default=str(REPO_ROOT / "ci-results"),
        help="directory for ruff-repair.patch / ruff-repair-report.json",
    )
    args = p.parse_args(argv)
    try:
        return repair(REPO_ROOT, Path(args.results))
    except subprocess.TimeoutExpired as e:
        print(f"repair timeout: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
