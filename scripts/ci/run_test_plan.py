#!/usr/bin/env python3
"""Thin execution glue: the C17 engine plans, Python's pytest executes.

Architecture contract (95% C / 5% Python):
  * scripts/ci/native/nse-ci-engine  — changed files, planning, validation.
  * scripts/ci/test_planner.py       — trusted Python reference (parity only).
  * THIS FILE                        — subprocess glue ONLY: build/verify the
    engine, run the C planner, parity-check against the reference, execute
    the selected tests with real pytest, write evidence, fail closed.

Gate semantics:
  * native unavailable  -> Python-reference plan + DEGRADED status (observable,
                           never reported as a native success).
  * parity mismatch     -> conservative re-plan (--full-suite) + PLANNER
                           INTEGRITY failure (exit 90) after evidence.
  * empty selection     -> conservative expansion (never silently green).
  * pytest exit code    -> propagated unchanged otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ENGINE_VERSION = "0.1.0"
EXIT_PLANNER_INTEGRITY = 90


def log(msg: str) -> None:
    print(f"[run-test-plan] {msg}", flush=True)


def sh(argv: list[str], cwd: str | None = None, timeout: int = 600):
    started = time.time()
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)
    return proc, time.time() - started


# --------------------------------------------------------------------------- #
# Native engine: build once, verify, persist for trusted self-hosted reuse     #
# --------------------------------------------------------------------------- #
def engine_version(binary: str) -> str | None:
    try:
        proc = subprocess.run([binary, "version"], capture_output=True, text=True,
                              timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def selftest_ok(binary: str) -> bool:
    try:
        proc = subprocess.run([binary, "selftest"], capture_output=True, text=True,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def ensure_native(repo_root: str, persist_dir: str) -> tuple[str | None, str]:
    candidate = os.path.join(persist_dir, "nse-ci-engine")
    if os.path.isfile(candidate) and engine_version(candidate) == ENGINE_VERSION:
        if selftest_ok(candidate):
            return candidate, "trusted persistent binary verified"
        log("persistent native binary failed selftest — rebuilding")

    native_dir = os.path.join(repo_root, "scripts", "ci", "native")
    build_dir = os.path.join(native_dir, "build")
    jobs = max(1, min(os.cpu_count() or 2, 4))
    try:
        cfg, _ = sh(["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"],
                    cwd=native_dir, timeout=180)
        bld, _ = (None, 0.0)
        if cfg.returncode == 0:
            bld, _ = sh(["cmake", "--build", "build", "-j", str(jobs)],
                        cwd=native_dir, timeout=600)
        if cfg.returncode != 0 or bld.returncode != 0:
            err = (cfg.stderr or bld.stderr if bld else cfg.stderr) or ""
            return None, f"native build failed: {err.strip()[-200:]}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"native build error: {exc}"

    fresh = os.path.join(build_dir, "nse-ci-engine")
    if not os.path.isfile(fresh) or engine_version(fresh) != ENGINE_VERSION \
            or not selftest_ok(fresh):
        return None, "freshly built binary failed verification"
    try:
        os.makedirs(persist_dir, exist_ok=True)
        shutil.copy2(fresh, candidate)
        shutil.copymode(fresh, candidate)
    except OSError:
        pass
    return fresh, "built from source this run"


# --------------------------------------------------------------------------- #
# Planning through the C engine (primary) and the Python reference (parity)    #
# --------------------------------------------------------------------------- #
def c_plan(binary: str, repo_root: str, files: list[str], head: str, event: str,
           full: bool) -> dict:
    argv = [binary, "plan", "--repo-root", repo_root, "--files", " ".join(files),
            "--commit-sha", head, "--event", event]
    if full:
        argv.append("--full-suite")
    proc, _ = sh(argv, cwd=repo_root, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"native planner exit {proc.returncode}: "
                           f"{proc.stderr.strip()[-200:]}")
    obj = json.loads(proc.stdout)
    # validate-plan is ALSO executed by the C engine itself (belt+braces):
    plan_file = os.path.join(repo_root, ".nse-plan-under-validation.json")
    with open(plan_file, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    vproc, _ = sh([binary, "validate-plan", plan_file], cwd=repo_root, timeout=30)
    os.unlink(plan_file)
    if vproc.returncode != 0:
        raise RuntimeError(f"native validate-plan rejected its own output: "
                           f"{vproc.stderr.strip()[-200:]}")
    return obj


def py_plan(repo_root: str, files: list[str], head: str, event: str,
            full: bool) -> dict:
    argv = [sys.executable, os.path.join(repo_root, "scripts", "ci",
                                         "test_planner.py"),
            "--files", " ".join(files), "--repo-root", repo_root,
            "--commit-sha", head, "--event", event]
    if full:
        argv.append("--full-suite")
    proc, _ = sh(argv, cwd=repo_root, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"python reference planner exit {proc.returncode}: "
                           f"{proc.stderr.strip()[-200:]}")
    return json.loads(proc.stdout)


def body_without_digest(plan_obj: dict) -> dict:
    return {k: v for k, v in plan_obj.items() if k != "plan_digest"}


def parity_check(native_obj: dict, python_obj: dict) -> tuple[str, str | None]:
    """C/Python parity contract.

    Returns (mode, failure). Modes:
      exact     — canonical bodies identical (digest equality).
      superset  — C selected a STRICT SUPERSET of the Python reference with
                  identical fallback decisions: the C engine legitimately
                  owns extra reachability the reference does not model
                  (pytest fixture/conftest edges). A superset is MORE
                  conservative — never a narrowing — so it is accepted and
                  recorded, not treated as a drift.
      mismatch  — anything else (narrowed selection, changed fallback
                  policy, differing shared reasons) -> planner-integrity
                  failure upstream.
    """
    if body_without_digest(native_obj) == body_without_digest(python_obj):
        return "exact", None
    if (native_obj.get("fallback_reason") == python_obj.get("fallback_reason")
            and native_obj.get("full_suite_required")
            == python_obj.get("full_suite_required")
            and native_obj.get("selection_mode") == python_obj.get("selection_mode")):
        c_entries = {t["id"]: t for t in native_obj["selected_tests"]}
        p_entries = {t["id"]: t for t in python_obj["selected_tests"]}
        if set(p_entries) <= set(c_entries):
            for tid, p_entry in p_entries.items():
                c_entry = c_entries[tid]
                if set(p_entry["reason"]) - set(c_entry["reason"]):
                    return "mismatch", (
                        f"shared test {tid} lost reasons in the native plan: "
                        f"{sorted(set(p_entry['reason']) - set(c_entry['reason']))}")
            if set(c_entries) - set(p_entries):
                return "superset", None
            return "exact", None
    return "mismatch", (
        f"parity mismatch: native digest {native_obj['plan_digest'][:16]}... vs "
        f"python {python_obj['plan_digest'][:16]}...")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--event", default="")
    ap.add_argument("--mode", choices=("targeted", "full"), default="targeted")
    ap.add_argument("--native", choices=("auto", "on", "off"), default="auto")
    ap.add_argument("--xdist", type=int, default=0)
    ap.add_argument("--pytest-extra", default="")
    ap.add_argument("--persist-dir", default=os.path.expanduser("~/.nse-native"))
    ap.add_argument("--out-dir", default="ci-results/test-plan")
    ap.add_argument("--runner-name", default=os.environ.get("RUNNER_NAME", "unknown"))
    ap.add_argument("--attempt", default="1")
    args = ap.parse_args()

    repo_root = os.path.abspath(args.repo_root)
    out_dir = os.path.join(repo_root, args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    full = args.mode == "full"

    # 1. changed files — through the C engine
    changed: list[str] = []
    native_binary: str | None = None
    native_reason = "native disabled by caller"
    if args.native != "off":
        native_binary, native_reason = ensure_native(repo_root, args.persist_dir)
    log(f"native engine: {native_reason}")

    if native_binary:
        proc, _ = sh([native_binary, "changed-files", "--base", args.base,
                      "--head", args.head], cwd=repo_root, timeout=60)
        if proc.returncode == 0:
            changed = [f for f in proc.stdout.split("\n") if f]
        else:
            log("C changed-files failed — conservative full mode")
            full = True

    # 2. plan: native primary, Python reference for parity
    integrity_failed = False
    integrity_reason = ""
    degraded = native_binary is None
    plan_obj: dict = {}
    planner_impl = "python-reference"
    parity_mode = "none"

    if native_binary:
        try:
            native_obj = c_plan(native_binary, repo_root, changed, args.head,
                                args.event, full)
            python_obj = py_plan(repo_root, changed, args.head, args.event, full)
            mode, failure = parity_check(native_obj, python_obj)
            if failure is None:
                plan_obj = native_obj
                planner_impl = f"native-c17-{ENGINE_VERSION}"
                log(f"parity OK ({mode}) — digest {plan_obj['plan_digest'][:16]}...")
                parity_mode = mode
            else:
                integrity_failed = True
                integrity_reason = failure
                log(f"PLANNER-INTEGRITY FAILURE: {integrity_reason}")
        except (RuntimeError, json.JSONDecodeError, OSError) as exc:
            degraded = True
            log(f"NATIVE FALLBACK ({exc}) — using the Python reference planner")
            native_binary = None

    if not plan_obj:
        try:
            plan_obj = py_plan(repo_root, changed, args.head, args.event, full)
        except (RuntimeError, json.JSONDecodeError, OSError) as exc:
            log(f"PYTHON REFERENCE FAILURE — emergency manifest")
            integrity_failed = True
            integrity_reason = str(exc)
            plan_obj = {}

    # 3. fail-safe expansions
    if integrity_failed:
        try:
            plan_obj = c_plan(native_binary, repo_root, changed, args.head,
                              args.event, True) if native_binary else \
                py_plan(repo_root, changed, args.head, args.event, True)
        except (RuntimeError, json.JSONDecodeError, OSError):
            plan_obj = {}
    selected_ids = [t["id"] for t in plan_obj.get("selected_tests", [])]
    if not selected_ids:
        log("empty selection — conservative expansion (empty never passes)")
        try:
            plan_obj = (c_plan(native_binary, repo_root, changed, args.head,
                               args.event, True) if native_binary else
                        py_plan(repo_root, changed, args.head, args.event, True))
        except (RuntimeError, json.JSONDecodeError, OSError):
            plan_obj = {}
        selected_ids = [t["id"] for t in plan_obj.get("selected_tests", [])]
        if not selected_ids:
            log("zero tests after expansion — failing closed")
            return 91

    # 4. execute with REAL pytest
    pytest_argv = [sys.executable, "-m", "pytest"] + selected_ids
    if args.xdist > 0:
        pytest_argv += ["-n", str(args.xdist), "--dist", "loadgroup"]
    if args.pytest_extra:
        pytest_argv += args.pytest_extra.split()
    log(f"pytest: {len(selected_ids)} test files | argv: {' '.join(pytest_argv)}")
    t0 = time.time()
    proc = subprocess.run(pytest_argv, cwd=repo_root)
    pytest_rc, pytest_seconds = proc.returncode, time.time() - t0
    log(f"pytest exit: {pytest_rc}")

    # 5. evidence
    manifest = {
        "commit_head": args.head, "commit_base": args.base,
        "plan_digest": plan_obj.get("plan_digest"),
        "plan_selection_mode": plan_obj.get("selection_mode"),
        "plan_fallback_reason": plan_obj.get("fallback_reason"),
        "planner": {
            "primary_impl": planner_impl, "engine_version": ENGINE_VERSION,
            "degraded": degraded, "parity_checked": not degraded,
            "parity_mode": parity_mode,
            "integrity_failed": integrity_failed,
            "integrity_reason": integrity_reason,
            "native_reason": native_reason,
        },
        "selected_tests": selected_ids,
        "selected_count": len(selected_ids),
        "changed_file_count": len(changed),
        "pytest_argv": pytest_argv, "pytest_exit_code": pytest_rc,
        "pytest_seconds": round(pytest_seconds, 2),
        "runner_name": args.runner_name, "attempt": args.attempt,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "ended_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(out_dir, "execution-manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    with open(os.path.join(out_dir, "plan.json"), "w", encoding="utf-8") as fh:
        json.dump(plan_obj, fh, indent=2)

    gout = os.environ.get("GITHUB_OUTPUT")
    if gout:
        with open(gout, "a", encoding="utf-8") as fh:
            fh.write(f"plan_digest={manifest['plan_digest']}\n")
            fh.write(f"planner_impl={planner_impl}\n")
            fh.write(f"degraded={str(degraded).lower()}\n")
            fh.write(f"integrity_failed={str(integrity_failed).lower()}\n")
            fh.write(f"selected_count={len(selected_ids)}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Test Plan Execution\n\n| field | value |\n|---|---|\n")
            fh.write(f"| planner | `{planner_impl}` |\n")
            fh.write(f"| plan digest | `{manifest['plan_digest']}` |\n")
            fh.write(f"| selected tests | {len(selected_ids)} |\n")
            fh.write(f"| changed files | {len(changed)} |\n")
            fh.write(f"| pytest exit | {pytest_rc} |\n")
            fh.write(f"| degraded | {degraded} |\n")
            if degraded:
                fh.write(f"| native status | {native_reason} |\n")
            if integrity_failed:
                fh.write(f"| **PLANNER INTEGRITY** | **FAILED: {integrity_reason}** |\n")
            fh.write(f"| runner | {args.runner_name} (attempt {args.attempt}) |\n")

    if integrity_failed:
        log("PLANNER-INTEGRITY FAILURE — conservative evidence collected, gate RED")
        return EXIT_PLANNER_INTEGRITY
    return pytest_rc


if __name__ == "__main__":
    sys.exit(main())
