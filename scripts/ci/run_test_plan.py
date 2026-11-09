#!/usr/bin/env python3
"""Planning-to-execution pipeline: C17 engine plans, pytest executes.

Architecture contract (95% C / 5% Python):
  * scripts/ci/native/nse-ci-engine  — changed files, planning, validation (C17).
  * scripts/ci/test_planner.py       — trusted Python reference (parity ONLY).
  * THIS FILE                        — subprocess glue: build/verify C engine,
    run the C planner, parity-check against the reference, execute selected
    tests with real pytest, write evidence, fail closed.

Gate semantics:
  * Native C engine MUST build & pass selftest. Any failure -> exit non-zero.
    No silent fallback to Python planning.
  * Python reference is used ONLY for differential parity verification.
  * Empty selected set never becomes green (conservative expansion).
  * pytest exit code propagates unchanged.
  * Evidence manifest binds: SHAs, plan digest, planner impl+version,
    selected tests, pytest argv, exit code, runner identity, timestamps.

CLI:
  python scripts/ci/run_test_plan.py --repo-root . --base SHA --head SHA
         --event pull_request --mode targeted --native-binary PATH
         --xdist 0 --pytest-extra "..." --out-dir ci-results/test-plan
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ENGINE_VERSION = "0.1.0"
EXIT_PLANNER_INTEGRITY = 90


def log(msg: str) -> None:
    print(f"[run-test-plan] {msg}", flush=True)


def sh(argv: list[str], cwd: str | None = None, timeout: int = 600):
    started = time.time()
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    return proc, time.time() - started


def engine_version(binary: str) -> str | None:
    try:
        proc = subprocess.run(
            [binary, "version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def selftest_ok(binary: str) -> bool:
    try:
        proc = subprocess.run(
            [binary, "selftest"], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def ensure_native_engine(repo_root: str, persist_dir: str) -> str:
    """Return path to verified native C17 engine. Fail hard if unavailable."""
    candidate = os.path.join(persist_dir, "nse-ci-engine")
    if os.path.isfile(candidate) and engine_version(candidate) == ENGINE_VERSION:
        if selftest_ok(candidate):
            return candidate
        log("persistent native binary failed selftest — rebuilding")

    native_dir = os.path.join(repo_root, "scripts", "ci", "native")
    build_dir = os.path.join(native_dir, "build")
    jobs = max(1, min(os.cpu_count() or 2, 4))
    try:
        cfg = sh(
            ["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"], cwd=native_dir, timeout=180
        )
        if cfg.returncode != 0:
            raise RuntimeError(f"cmake configure failed: {cfg.stderr[-200:]}")
        bld = sh(["cmake", "--build", "build", "-j", str(jobs)], cwd=native_dir, timeout=600)[0]
        if bld.returncode != 0:
            raise RuntimeError(f"cmake build failed: {bld.stderr[-200:]}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"native build error: {exc}") from exc

    fresh = os.path.join(build_dir, "nse-ci-engine")
    if (
        not os.path.isfile(fresh)
        or engine_version(fresh) != ENGINE_VERSION
        or not selftest_ok(fresh)
    ):
        raise RuntimeError("freshly built native engine failed verification")

    try:
        os.makedirs(persist_dir, exist_ok=True)
        shutil.copy2(fresh, candidate)
        shutil.copymode(fresh, candidate)
    except OSError:
        pass  # persistence best-effort; fresh binary still works
    return candidate


def body_without_digest(plan_obj: dict) -> dict:
    return {k: v for k, v in plan_obj.items() if k != "plan_digest"}


def digest_of(plan_obj: dict) -> str:
    body = body_without_digest(plan_obj)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_plan(plan_obj: dict) -> str | None:
    if not isinstance(plan_obj, dict):
        return "plan is not an object"
    for key in (
        "commit_sha",
        "plan_version",
        "selection_mode",
        "selected_tests",
        "plan_digest",
        "full_suite_required",
    ):
        if key not in plan_obj:
            return f"missing key: {key}"
    if not isinstance(plan_obj["selected_tests"], list):
        return "selected_tests is not a list"
    for entry in plan_obj["selected_tests"]:
        if (
            not isinstance(entry, dict)
            or not entry.get("id")
            or not isinstance(entry.get("reason"), list)
            or not entry["reason"]
        ):
            return "malformed selected_tests entry"
    if not isinstance(plan_obj["plan_digest"], str) or len(plan_obj["plan_digest"]) != 64:
        return "plan_digest is not 64 hex chars"
    if digest_of(plan_obj) != plan_obj["plan_digest"]:
        return "plan digest mismatch"
    return None


def parity_check(native_obj: dict, python_obj: dict) -> tuple[str, str | None]:
    """Return (mode, failure). mode: exact | superset | mismatch."""
    if body_without_digest(native_obj) == body_without_digest(python_obj):
        return "exact", None
    if (
        native_obj.get("fallback_reason") == python_obj.get("fallback_reason")
        and native_obj.get("full_suite_required") == python_obj.get("full_suite_required")
        and native_obj.get("selection_mode") == python_obj.get("selection_mode")
    ):
        c_entries = {t["id"]: t for t in native_obj["selected_tests"]}
        p_entries = {t["id"]: t for t in python_obj["selected_tests"]}
        if set(p_entries) <= set(c_entries):
            for tid, p_entry in p_entries.items():
                c_entry = c_entries[tid]
                if set(p_entry["reason"]) - set(c_entry["reason"]):
                    return "mismatch", (
                        f"shared test {tid} lost reasons in native plan: "
                        f"{sorted(set(p_entry['reason']) - set(c_entry['reason']))}"
                    )
            if set(c_entries) - set(p_entries):
                return "superset", None
            return "exact", None
    return "mismatch", (
        f"parity mismatch: native digest {native_obj['plan_digest'][:16]}... "
        f"vs python {python_obj['plan_digest'][:16]}..."
    )


def c_plan(
    binary: str, repo_root: str, files: list[str], head: str, event: str, full: bool
) -> dict:
    argv = [
        binary,
        "plan",
        "--repo-root",
        repo_root,
        "--files",
        " ".join(files),
        "--commit-sha",
        head,
        "--event",
        event,
    ]
    if full:
        argv.append("--full-suite")
    proc, _ = sh(argv, cwd=repo_root, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"native planner exit {proc.returncode}: {proc.stderr[-200:]}")
    obj = json.loads(proc.stdout)
    err = validate_plan(obj)
    if err:
        raise RuntimeError(f"native plan invalid: {err}")
    return obj


def py_plan(repo_root: str, files: list[str], head: str, event: str, full: bool) -> dict:
    argv = [
        sys.executable,
        os.path.join(repo_root, "scripts", "ci", "test_planner.py"),
        "--files",
        " ".join(files),
        "--repo-root",
        repo_root,
        "--commit-sha",
        head,
        "--event",
        event,
    ]
    if full:
        argv.append("--full-suite")
    proc, _ = sh(argv, cwd=repo_root, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"python reference planner exit {proc.returncode}: {proc.stderr[-200:]}")
    obj = json.loads(proc.stdout)
    err = validate_plan(obj)
    if err:
        raise RuntimeError(f"python plan invalid: {err}")
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--event", default="")
    ap.add_argument("--mode", choices=("targeted", "full"), default="targeted")
    ap.add_argument("--native-binary", required=True, help="path to verified nse-ci-engine binary")
    ap.add_argument(
        "--xdist", type=int, default=0, help="pytest-xdist workers (0 = do not pass -n)"
    )
    ap.add_argument("--pytest-extra", default="")
    ap.add_argument("--out-dir", default="ci-results/test-plan")
    ap.add_argument("--runner-name", default=os.environ.get("RUNNER_NAME", "unknown"))
    ap.add_argument("--attempt", default="1")
    args = ap.parse_args()

    repo_root = os.path.abspath(args.repo_root)
    out_dir = os.path.join(repo_root, args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    full = args.mode == "full"

    # 1. changed files — via C engine change detector
    native_binary = args.native_binary
    if not os.path.isfile(native_binary) or engine_version(native_binary) != ENGINE_VERSION:
        # fall back to building if the CI step did not produce one (local dev)
        native_binary = ensure_native_engine(repo_root, os.path.expanduser("~/.nse-native"))
    log(f"native engine: {native_binary} ({ENGINE_VERSION})")

    proc, _ = sh(
        [native_binary, "changed-files", "--base", args.base, "--head", args.head],
        cwd=repo_root,
        timeout=60,
    )
    if proc.returncode != 0:
        log(f"C changed-files failed (rc={proc.returncode}): {proc.stderr[-200:]}")
        return 2
    changed = [f for f in proc.stdout.split("\n") if f]
    log(f"changed files: {len(changed)}")

    # Global invalidation forces full mode (policy, mirrored from planner).
    if not full:
        for f in changed:
            if f in {
                "pyproject.toml",
                "requirements.lock",
                "requirements.txt",
                "uv.lock",
                "tests/conftest.py",
                "pytest.ini",
                "setup.cfg",
                "tox.ini",
            } or f.startswith(("configs/",)):
                log(f"global invalidation via {f} — full critical suite")
                full = True
                break

    # 2. plan: C native PRIMARY; Python reference for parity ONLY
    planner_impl = f"native-c17-{ENGINE_VERSION}"
    try:
        native_obj = c_plan(native_binary, repo_root, changed, args.head, args.event, full)
    except (RuntimeError, json.JSONDecodeError, OSError) as exc:
        log(f"NATIVE PLANNER FAILED: {exc}")
        return 90  # planner integrity failure — never fall back to Python planning

    try:
        python_obj = py_plan(repo_root, changed, args.head, args.event, full)
    except (RuntimeError, json.JSONDecodeError, OSError) as exc:
        log(f"PYTHON REFERENCE FAILURE: {exc}")
        return 90

    parity_mode, failure = parity_check(native_obj, python_obj)
    if failure is not None:
        log(f"PLANNER-INTEGRITY FAILURE: {failure}")
        return EXIT_PLANNER_INTEGRITY

    plan_obj = native_obj  # C engine is primary
    # --- C17 impact summary (visible in the job log) -------------------
    _sel = plan_obj["selected_tests"]
    _reasons = {}
    for _t in _sel:
        for _r in _t.get("reason", []):
            _reasons[_r] = _reasons.get(_r, 0) + 1
    _crit = sum(1 for _t in _sel if _t.get("priority") == "critical")
    log(
        f"C17 PLAN: mode={plan_obj['selection_mode']} "
        f"full_suite={plan_obj['full_suite_required']} "
        f"selected={len(_sel)} critical={_crit}"
    )
    if plan_obj.get("fallback_reason"):
        log(f"C17 FALLBACK: {plan_obj['fallback_reason']}")
    log("C17 REASONS: " + ", ".join(f"{k}={v}" for k, v in sorted(_reasons.items())))
    log(f"C17 CHANGED: {len(changed)} file(s) -> {len(_sel)} test file(s)")

    log(f"parity OK ({parity_mode}) — digest {plan_obj['plan_digest'][:16]}...")

    # 3. selected tests
    selected_ids = [t["id"] for t in plan_obj["selected_tests"]]
    if not selected_ids:
        log("empty selection — conservative expansion (empty never passes)")
        try:
            plan_obj = c_plan(native_binary, repo_root, changed, args.head, args.event, True)
        except (RuntimeError, json.JSONDecodeError, OSError) as exc:
            log(f"conservative re-plan failed: {exc}")
            return 90
        selected_ids = [t["id"] for t in plan_obj["selected_tests"]]
        if not selected_ids:
            log("zero tests after expansion — failing closed")
            return 91

    # 4. execute with REAL pytest
    pytest_argv = [sys.executable, "-m", "pytest", *selected_ids]
    if args.xdist > 0:
        pytest_argv += ["-n", str(args.xdist), "--dist", "loadgroup"]
    if args.pytest_extra:
        pytest_argv += args.pytest_extra.split()
    log(f"pytest: {len(selected_ids)} test files | argv: {' '.join(pytest_argv)}")
    log("PYTEST SELECTED (first 15): " + ", ".join(selected_ids[:15]))
    if len(selected_ids) > 15:
        log(f"PYTEST SELECTED: ... and {len(selected_ids) - 15} more")

    t0 = time.time()
    proc = subprocess.run(pytest_argv, cwd=repo_root, check=False)
    pytest_rc, pytest_seconds = proc.returncode, time.time() - t0
    log(f"pytest exit: {pytest_rc} after {pytest_seconds:.1f}s")

    # 5. evidence
    manifest = {
        "commit_head": args.head,
        "commit_base": args.base,
        "plan_digest": plan_obj["plan_digest"],
        "plan_selection_mode": plan_obj["selection_mode"],
        "plan_fallback_reason": plan_obj.get("fallback_reason"),
        "planner": {
            "primary_impl": planner_impl,
            "engine_version": ENGINE_VERSION,
            "degraded": False,
            "parity_checked": True,
            "parity_mode": parity_mode,
            "native_reason": args.native_binary,
        },
        "selected_tests": selected_ids,
        "selected_count": len(selected_ids),
        "changed_file_count": len(changed),
        "pytest_argv": pytest_argv,
        "pytest_exit_code": pytest_rc,
        "pytest_seconds": round(pytest_seconds, 2),
        "runner_name": args.runner_name,
        "attempt": args.attempt,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "ended_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(out_dir, "execution-manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    with open(os.path.join(out_dir, "plan.json"), "w", encoding="utf-8") as fh:
        json.dump(plan_obj, fh, indent=2)

    gout = os.environ.get("GITHUB_OUTPUT")
    if gout:
        with open(gout, "a", encoding="utf-8") as fh:
            fh.write(f"plan_digest={manifest['plan_digest']}\n")
            fh.write(f"planner_impl={planner_impl}\n")
            fh.write("degraded=False\n")
            fh.write("integrity_failed=False\n")
            fh.write(f"selected_count={len(selected_ids)}\n")
            fh.write(f"parity_mode={parity_mode}\n")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Test Plan Execution\n\n")
            fh.write("| field | value |\n| --- | --- |\n")
            fh.write(f"| planner | `{planner_impl}` |\n")
            fh.write(f"| plan digest | `{manifest['plan_digest']}` |\n")
            fh.write(f"| selection mode | {manifest['plan_selection_mode']} |\n")
            fh.write(f"| parity mode | `{parity_mode}` |\n")
            fh.write(f"| selected tests | {len(selected_ids)} |\n")
            fh.write(f"| changed files | {len(changed)} |\n")
            fh.write(f"| pytest exit | {pytest_rc} |\n")
            fh.write(f"| runner | {args.runner_name} (attempt {args.attempt}) |\n")
            fh.write("\n### Selected tests\n\n")
            for tid in selected_ids:
                fh.write(f"- `{tid}`\n")

    return pytest_rc


if __name__ == "__main__":
    sys.exit(main())
