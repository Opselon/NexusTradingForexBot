#!/usr/bin/env python3
"""Canonical local pre-push quality gate (CHG-0036).

One boring, deterministic, OFFLINE command that catches the cheap failure
classes (Ruff lint / Ruff format / import order / critical-suite manifest /
targeted fast tests) BEFORE they consume a GitHub Actions run.

CI remains the authoritative gate — this tool runs the SAME underlying
configuration (pyproject.toml [tool.ruff] / [tool.mypy], tests/critical_suite.txt)
with the SAME tool modules, so local green and CI green mean the same thing.

Usage:
    python scripts/ci/check_local.py                 # changed-file scope
    python scripts/ci/check_local.py --all           # whole-tree scope
    python scripts/ci/check_local.py --staged        # staged files only
    python scripts/ci/check_local.py --fix           # apply SAFE mechanical fixes, re-check
    python scripts/ci/check_local.py --fast          # skip mypy (cheap syntactic checks only)
    python scripts/ci/check_local.py --json          # machine-readable (stdout = pure JSON)

Safety contract (multi-agent swarm):
    * NEVER stages, commits, pushes, stashes, resets or checks out anything.
    * --fix touches ONLY the files in the current scope enumeration.
    * Offline: no pip/network/MT5/provider/model downloads; a missing tool is
      reported as status=configuration_error, never auto-installed.

Exit codes: 0 = all stages passed; 1 = one or more stage failed;
            2 = usage/configuration error (nothing ran).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CRITICAL_MANIFEST = REPO_ROOT / "tests" / "critical_suite.txt"

# Never lint/format/fix generated or foreign surface (mirrors pyproject excludes
# plus artifact/scratch conventions; scratch/ is already excluded in pyproject).
SCOPE_EXCLUDES = (
    "/.venv/",
    "/artifacts/",
    "/release/",
    "/scratch/",
    "/_cleanup_hold_",
    # A venv accidentally created INSIDE the repo (e.g. Lib/ by a stray uv run)
    # is never agent source; linting it would only report third-party noise.
    "/Lib/site-packages/",
    "/site-packages/",
    "/venv/",
)


# ---------------------------------------------------------------------------
# Scope enumeration (multi-agent safe: read-only git calls, never mutate)
# ---------------------------------------------------------------------------
def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        # ALWAYS the tree the gate script lives in (parents[2] of __file__).
        # A gate copy inside a temp checkout must interrogate THAT checkout —
        # hardcoding the real repo would silently cross tree boundaries.
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )


def _in_scope(path: str) -> bool:
    p = path.replace("\\", "/")
    return p.endswith(".py") and not any(x in f"/{p}" for x in SCOPE_EXCLUDES)


def changed_files(*, all_files: bool, staged_only: bool) -> list[str]:
    """Deterministic changed-file enumeration. Deleted files are EXCLUDED from
    lint/format scopes (they do not exist) but reported in the result envelope.

    Untracked files count as changed (an agent's brand-new module is exactly
    the kind of file that must be linted before push) — resolved via
    `git ls-files --others --exclude-standard`, never by walking foreign trees.
    """
    if all_files:
        files: list[str] = []
        for base in ("src", "tests", "scripts"):
            for p in (REPO_ROOT / base).rglob("*.py"):
                files.append(p.relative_to(REPO_ROOT).as_posix())
        return sorted(files)

    names: set[str] = set()
    diffs: list[list[str]] = [
        ["diff", "--name-only", "--diff-filter=ACMR", "HEAD"],  # working tree
    ]
    if staged_only:
        diffs.append(["diff", "--name-only", "--diff-filter=ACMR", "--cached"])
        diffs.append(["ls-files", "--others", "--exclude-standard"])
    else:
        # staged + unstaged + new-vs-HEAD, plus push-base (origin/main) additions
        diffs.append(["diff", "--name-only", "--diff-filter=ACMR", "--cached"])
        diffs.append(["ls-files", "--others", "--exclude-standard"])
        base = _git(["merge-base", "HEAD", "origin/main"])
        if base.returncode == 0 and base.stdout.strip():
            diffs.append(["diff", "--name-only", "--diff-filter=ACMR", base.stdout.strip()])
    for d in diffs:
        r = _git(d)
        if r.returncode == 0:
            names.update(line.strip() for line in r.stdout.splitlines() if line.strip())
    return sorted(n for n in names if _in_scope(n))


def deleted_files() -> list[str]:
    r = _git(["diff", "--name-only", "--diff-filter=D", "HEAD"])
    if r.returncode != 0:
        return []
    return sorted(line.strip() for line in r.stdout.splitlines() if line.strip().endswith(".py"))


def changed_files_any() -> list[str]:
    """ALL changed files regardless of extension (working tree + staged +
    untracked + origin/main delta), including deletions.

    Used by prepush_plan ONLY: gate-integrity files (critical_suite.txt,
    pyproject.toml, ci.yml) and docs-only surfaces are non-Python, so a
    .py-filtered enumeration silently bypasses the integrity contract.
    Lint/format scope MUST keep using changed_files() (.py only).
    """
    names: set[str] = set()
    diffs: list[list[str]] = [
        ["diff", "--name-only", "HEAD"],
        ["diff", "--name-only", "--cached"],
        ["ls-files", "--others", "--exclude-standard"],
    ]
    base = _git(["merge-base", "HEAD", "origin/main"])
    if base.returncode == 0 and base.stdout.strip():
        diffs.append(["diff", "--name-only", base.stdout.strip()])
    for d in diffs:
        r = _git(d)
        if r.returncode == 0:
            names.update(line.strip() for line in r.stdout.splitlines() if line.strip())
    return sorted(n for n in names if n and not any(x in f"/{n}" for x in SCOPE_EXCLUDES))


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------
@dataclass
class StageResult:
    name: str
    command: list[str]
    exit_code: int
    status: str  # passed | failed | errored | skipped | configuration_error
    duration_sec: float
    scope_files: list[str] = field(default_factory=list)
    detail: str = ""
    fix_attempted: bool = False
    fix_applied: bool = False
    output: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "exit_code": self.exit_code,
            "status": self.status,
            "duration_sec": round(self.duration_sec, 3),
            "files_scope_count": len(self.scope_files),
            "detail": self.detail,
            "fix_attempted": self.fix_attempted,
            "fix_applied": self.fix_applied,
            "error_classification": self._classify(),
        }

    def _classify(self) -> str:
        if self.status == "configuration_error":
            return "TOOL_OR_CONFIG_MISSING"
        if self.status == "passed":
            return "NONE"
        if self.name.startswith("ruff"):
            return "LINT_OR_FORMAT"
        if self.name == "mypy":
            return "TYPE_CHECK"
        if self.name == "critical_suite_manifest":
            return "CONFIGURATION_ERROR"
        if self.name.startswith("fast_tests"):
            return "TEST_FAILURE"
        return "OTHER"


def _tool_cmd(module: str) -> list[str] | None:
    """Same interpreter that hosts this script = the repo venv toolchain.

    Resilience: if that interpreter lacks the module (e.g. the gate was
    launched from a foreign venv, as some harnesses do), fall back to the
    repo's canonical .venv interpreter. A completely missing toolchain
    surfaces later as status=configuration_error — never silently skipped.
    """
    exe = Path(sys.executable)
    probe = subprocess.run(
        [str(exe), "-m", module, "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if probe.returncode == 0:
        return [str(exe), "-m", module]
    fallback = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if not fallback.exists():
        fallback = REPO_ROOT / ".venv" / "bin" / "python"
    if fallback.exists() and fallback.resolve() != exe.resolve():
        probe2 = subprocess.run(
            [str(fallback), "-m", module, "--version"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if probe2.returncode == 0:
            return [str(fallback), "-m", module]
    return None


def _run_stage(
    name: str,
    cmd: list[str] | None,
    *,
    scope_files: list[str] | None = None,
    extra_args: list[str] | None = None,
    timeout: int = 600,
) -> StageResult:
    args = list(cmd or [])
    # Ordering contract: [python, -m, <tool>, <subcommand+flags...>, <files...>]
    # ruff parses `check`/`format` as its subcommand, so the file list MUST be
    # appended AFTER extra_args (which carry the subcommand). The caller passes
    # base cmd = [python, -m, tool] only; extra_args carry the rest.
    if extra_args:
        args.extend(extra_args)
    if scope_files:
        args.extend(scope_files)
    t0 = time.perf_counter()
    if cmd is None:
        return StageResult(
            name=name,
            command=[],
            exit_code=-1,
            status="configuration_error",
            duration_sec=0.0,
            scope_files=scope_files or [],
            detail=f"tool not available in {sys.executable}",
        )
    r = subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    dur = time.perf_counter() - t0
    detail = (r.stdout or r.stderr).strip().splitlines()
    detail_line = detail[0][:200] if detail else ""
    if name == "ruff_format":
        detail_line = detail_line if r.returncode else "all files formatted"
    return StageResult(
        name=name,
        command=args,
        exit_code=r.returncode,
        status="passed" if r.returncode == 0 else "failed",
        duration_sec=dur,
        scope_files=scope_files or [],
        detail=detail_line,
        output=(r.stdout or "")[-4000:],
    )


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------
def stage_manifest() -> StageResult:
    t0 = time.perf_counter()
    script = REPO_ROOT / "scripts" / "ci" / "verify_critical_suite_manifest.py"
    if not script.exists():
        return StageResult(
            name="critical_suite_manifest",
            command=[],
            exit_code=-1,
            status="configuration_error",
            duration_sec=0.0,
            detail="verify_critical_suite_manifest.py missing",
        )
    r = subprocess.run(
        [sys.executable, str(script), "--root", str(REPO_ROOT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    return StageResult(
        name="critical_suite_manifest",
        command=[sys.executable, "scripts/ci/verify_critical_suite_manifest.py"],
        exit_code=r.returncode,
        status="passed" if r.returncode == 0 else "failed",
        duration_sec=time.perf_counter() - t0,
        detail=(r.stdout or r.stderr).strip().splitlines()[0][:200]
        if (r.stdout or r.stderr)
        else "",
    )


def stage_fast_tests(scope_files: list[str]) -> StageResult:
    """Cheap targeted tests: the local-gate regression suite (does NOT modify
    the real tree) + the manifest unit tests when they exist. Never xdist,
    never coverage, never network.

    NOTE: the gate's own regression suite is EXCLUDED from the gate's fast
    stage (it materializes HEAD into temp trees and re-invokes the gate —
    running it inside the gate would recurse and burn minutes). It runs in
    CI / explicitly instead.
    """
    targets = [
        REPO_ROOT / "tests" / "unit" / "test_research_purge_defaults_bug183.py",
    ]
    args = [sys.executable, "-m", "pytest", "-q", "--no-header", "-x", "-p", "no:cacheprovider"]
    existing = [str(t.relative_to(REPO_ROOT)) for t in targets if t.exists()]
    if not existing:
        return StageResult(
            name="fast_tests",
            command=args,
            exit_code=0,
            status="skipped",
            duration_sec=0.0,
            detail="no fast test targets present",
        )
    args.extend(existing)
    t0 = time.perf_counter()
    r = subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=420,
    )
    tail = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-1:]
    return StageResult(
        name="fast_tests",
        command=args,
        exit_code=r.returncode,
        status="passed" if r.returncode == 0 else "failed",
        duration_sec=time.perf_counter() - t0,
        scope_files=scope_files,
        detail=tail[0][:200] if tail else "",
    )


# ---------------------------------------------------------------------------
# Main gate
# ---------------------------------------------------------------------------
#: Files whose modification triggers MANDATORY full-tree validation: a change
#: to the gate itself, the critical-suite manifest, or the shared lint/type
#: config can silently widen or narrow what "safe to push" means for every
#: future push. Gate-integrity contract (CHG-0049): touching any of these
#: forces scope=all + mypy ON, and records a parity obligation in the result.
GATE_INTEGRITY_FILES = (
    "scripts/ci/check_local.py",
    "scripts/ci/verify_critical_suite_manifest.py",
    "tests/critical_suite.txt",
    "pyproject.toml",
    ".github/workflows/ci.yml",
)


#: Push-bypass contract: the minimum validation a push requires. --prepush
#: enforces this exactly; agents may exceed it, never fall below it.
#:   scope: the union of every .py file that will reach the remote with this
#:          push (working tree + staged + untracked + origin/main delta)
#:   mypy:  ON unless the ONLY changed surface is docs/markdown (nothing the
#:          type checker consumes can drift)
def prepush_plan() -> dict[str, Any]:
    """Deterministic push-time plan derived from the CURRENT git state.

    Machine-checkable definition of "safe to push" (steer §2/§6):
      * scope is ALWAYS the push scope (the union the CI will see) — a
        narrower scope than the changed files require is a bypass.
      * mypy is ON unless the push surface is pure docs (no .py, no config).
      * gate-integrity files (gate/manifest/pyproject/CI workflow) in the
        diff force --all + mypy: a modified gate must prove it still covers
        the whole tree before it may guard anyone else's push.
    """
    changed = changed_files_any()
    deleted = [f for f in changed if not (REPO_ROOT / f).exists()]
    all_changed = set(changed)
    integrity_touched = sorted(all_changed & set(GATE_INTEGRITY_FILES))
    # docs-only push surface: no python, no config, no CI wiring changed
    py_or_config = [
        f for f in all_changed if _in_scope(f) or f.endswith((".yml", ".yaml", ".toml"))
    ]
    docs_only = bool(all_changed) and not py_or_config
    return {
        "scope_mode": "all" if integrity_touched else "changed",
        "all_files": bool(integrity_touched),
        "fast": docs_only and not integrity_touched,
        "integrity_files_touched": integrity_touched,
        "docs_only": docs_only,
        "push_surface_files": sorted(all_changed),
        "deleted_py_files": deleted,
    }


def run_gate(*, all_files: bool, staged_only: bool, fix: bool, fast: bool, json_out: bool) -> int:
    scope = changed_files(all_files=all_files, staged_only=staged_only)
    deleted = deleted_files()
    results: list[StageResult] = []

    ruff = _tool_cmd("ruff")
    mypy = _tool_cmd("mypy")

    # [1] Ruff lint (scope-pinned; when the scope is small we pass files
    # explicitly so unrelated tree noise — e.g. an accidental in-repo venv —
    # cannot fail the gate. Whole-tree scope relies on pyproject excludes.)
    lint_args = ["check", "--output-format", "concise"]
    res = _run_stage("ruff_lint", ruff, scope_files=scope, extra_args=lint_args)
    if fix and res.exit_code != 0 and res.status != "configuration_error":
        fixed = _run_stage("ruff_lint_fix", ruff, scope_files=scope, extra_args=["check", "--fix"])
        res.fix_attempted = True
        if fixed.exit_code == 0:
            res = _run_stage(
                "ruff_lint",
                ruff,
                scope_files=scope,
                extra_args=["check", "--output-format", "concise"],
            )
            res.fix_attempted = True
            res.fix_applied = True
    results.append(res)

    # [2] Ruff format --check
    res = _run_stage("ruff_format", ruff, scope_files=scope, extra_args=["format", "--check"])
    if fix and res.exit_code != 0 and res.status != "configuration_error":
        _run_stage("ruff_format_fix", ruff, scope_files=scope, extra_args=["format"])
        res.fix_attempted = True
        res2 = _run_stage("ruff_format", ruff, scope_files=scope, extra_args=["format", "--check"])
        res2.fix_attempted = True
        res2.fix_applied = True
        res = res2
    results.append(res)

    # [3] Mypy (src always; skipped in --fast)
    if fast:
        results.append(
            StageResult(
                name="mypy",
                command=[],
                exit_code=0,
                status="skipped",
                duration_sec=0.0,
                detail="skipped by --fast",
            )
        )
    else:
        results.append(_run_stage("mypy", mypy, extra_args=["src"], timeout=900))

    # [4] Critical-suite manifest validation (cheap, configuration-level)
    results.append(stage_manifest())

    # [5] Fast targeted unit tests
    results.append(stage_fast_tests(scope))

    overall = "passed" if all(r.status in ("passed", "skipped") for r in results) else "failed"

    envelope: dict[str, Any] = {
        "gate": "check_local",
        "overall": overall,
        "scope": {
            "mode": "all" if all_files else ("staged" if staged_only else "changed"),
            "files": scope,
            "deleted_py_files": deleted,
            "count": len(scope),
        },
        "fix_mode": fix,
        "fast_mode": fast,
        "results": [r.public() for r in results],
    }
    # Gate-integrity obligations travel with EVERY result so a drift/bypass
    # detector can audit any run envelope post-hoc (steer §4).
    plan = prepush_plan()
    envelope["gate_integrity"] = {
        "integrity_files_touched": plan["integrity_files_touched"],
        "full_tree_required": plan["all_files"],
        "full_tree_honored": all_files,
        "mypy_omitted": fast,
        "mypy_omission_justified": plan["docs_only"],
    }
    payload = json.dumps(envelope, indent=2 if not json_out else None, sort_keys=False)
    if json_out:
        sys.stdout.write(payload + "\n")
    else:
        sys.stdout.write(payload + "\n")
    for r in results:
        if r.output and r.status == "failed":
            print(f"--- {r.name} output (tail) ---", file=sys.stderr)
            print(r.output[-2000:], file=sys.stderr)
    return 0 if overall == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="check_local", description=__doc__.splitlines()[0])
    p.add_argument("--all", action="store_true", help="whole-tree scope instead of changed files")
    p.add_argument("--staged", action="store_true", help="staged files only")
    p.add_argument("--fix", action="store_true", help="apply SAFE mechanical fixes then re-check")
    p.add_argument("--fast", action="store_true", help="skip mypy (cheap stages only)")
    p.add_argument(
        "--prepush",
        action="store_true",
        help="canonical push-time contract: push-scope validation level chosen "
        "deterministically from the current git state (overrides --all/--staged/--fast)",
    )
    p.add_argument("--json", action="store_true", help="pure-JSON stdout (diagnostics to stderr)")
    args = p.parse_args(argv)
    if args.all and args.staged:
        print("--all and --staged are mutually exclusive", file=sys.stderr)
        return 2
    try:
        if args.prepush:
            # CANONICAL PRE-PUSH CONTRACT (steer §2/§6): the push scope and the
            # validation level are derived from git state, not agent mood.
            # Gate-integrity changes force full-tree + mypy; docs-only surfaces
            # may skip mypy; everything else gets push-scope + mypy. Agents may
            # run a WIDER check manually, never a narrower one.
            plan = prepush_plan()
            if not args.json:
                print(
                    f"[prepush] scope={plan['scope_mode']} "
                    f"mypy={'OFF(docs-only)' if plan['fast'] else 'ON'} "
                    f"integrity={plan['integrity_files_touched'] or 'none'}",
                    file=sys.stderr,
                )
            return run_gate(
                all_files=plan["all_files"],
                staged_only=False,
                fix=args.fix,
                fast=plan["fast"],
                json_out=args.json,
            )
        return run_gate(
            all_files=args.all,
            staged_only=args.staged,
            fix=args.fix,
            fast=args.fast,
            json_out=args.json,
        )
    except subprocess.TimeoutExpired as e:
        print(f"stage timeout: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
