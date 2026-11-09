#!/usr/bin/env python3
"""Planning-to-execution pipeline: C17 engine plans, pytest executes.

Architecture contract (C plans, Python executes — no Python planner):
  * scripts/ci/native/nse-ci-engine  — the SOLE test planner (C17):
    changed files, selection, validation. There is no Python planner
    and no parity twin; a missing or failed engine fails the lane.
  * scripts/ci/ast_extract.py       — minimal AST metadata extraction
    ONLY (stdlib `ast`: imports, fixtures, test args, dynamic-import
    targets). Python extracts structure; C owns dependency resolution,
    graph construction, impact analysis and final selection. The
    extractor runs on every plan and its JSON is passed to the engine
    via --metadata; a failed extraction fails the lane closed — the
    engine NEVER silently plans from the weaker line scanner in
    production.
  * THIS FILE                        — subprocess glue: build/verify the C
    engine, extract AST metadata, run its plan (digest-checked), execute
    the selected tests with real pytest, write evidence, fail closed.
    Python never selects tests; it only runs the files the C engine
    found. The ONE classification Python performs is the semantic scope
    of a pyproject.toml change (classify_pyproject_diff, parsed-toml
    diff); the engine owns the final invalidation verdict.
    There is no Python-side global-invalidation list: the canonical
    policy lives in the C engine (test_selector.c) and nowhere else.

Gate semantics:
  * Native C engine MUST build & pass selftest. Any failure -> exit non-zero.
    No fallback of any kind.
  * The plan JSON is digest-checked (validate_plan) before execution.
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
import pathlib
import shutil
import subprocess
import sys
import time
import tomllib

ENGINE_VERSION = "0.2.0"


def log(msg: str) -> None:
    print(f"[run-test-plan] {msg}", flush=True)


def sh(argv: list[str], cwd: str | None = None, timeout: int = 600):
    started = time.time()
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    return proc, time.time() - started


def classify_pyproject_diff(repo_root: str, base: str, head: str) -> str:
    """Classify a pyproject.toml change by semantic section.

    Parses the base and head versions of the file (tomllib) and compares
    them section by section, instead of trusting the file name. Returns a
    comma-joined scope set drawn from:
      pytest — [tool.pytest.ini_options] (collection/behaviour for every test)
      deps   — [project] dependencies / requires-python / optional-dependencies
      build  — [build-system] / [tool.setuptools.*]
      scripts — [project.scripts] (console entry points; the CLI tests must
        run — no finer selection exists, so fail closed to global)
      mypy   — [tool.mypy.*] only
      lint   — [tool.ruff.*] only
      meta   — [project] name/version/description only
      unknown — an unrecognised section, or the diff could not be classified
    "" (empty) means the two versions are semantically identical
    (comment/whitespace-only diff). The C engine owns the final verdict:
    pytest/deps/build/scripts/unknown (or an absent scope) -> global
    invalidation; mypy/lint/meta-only -> CI-tooling scope, no full suite.
    A git or parse failure returns "unknown" (fail closed, same as today's
    behaviour).
    """

    def _load(rev: str) -> dict | None:
        proc, _ = sh(["git", "show", f"{rev}:pyproject.toml"], cwd=repo_root, timeout=60)
        if proc.returncode != 0:
            return None
        try:
            return tomllib.loads(proc.stdout)
        except (tomllib.TOMLDecodeError, ValueError):
            return None

    old, new = _load(base), _load(head)
    if old is None or new is None:
        return "unknown"
    scopes: set[str] = set()

    def _project_scope(op: dict, np: dict) -> None:
        for key in set(op) | set(np):
            if op.get(key) != np.get(key):
                if key in ("dependencies", "requires-python", "optional-dependencies"):
                    scopes.add("deps")
                elif key == "scripts":
                    # Console entry points: the installed CLI changes, so
                    # tests/cli/* must run. The engine has no "cli"
                    # selection, so fail closed to global (review 2026-11-09:
                    # scripts used to land in "meta" and run tests/ci only,
                    # which would never exercise the CLI).
                    scopes.add("scripts")
                else:
                    scopes.add("meta")

    op, np = old.get("project"), new.get("project")
    if isinstance(op, dict) and isinstance(np, dict):
        _project_scope(op, np)
    elif op != np:
        scopes.add("deps")  # structural change to [project] -> conservative
    if old.get("build-system") != new.get("build-system"):
        scopes.add("build")
    ot, nt = old.get("tool"), new.get("tool")
    if not isinstance(ot, dict):
        ot = {}
    if not isinstance(nt, dict):
        nt = {}
    for key in set(ot) | set(nt):
        if ot.get(key) == nt.get(key):
            continue
        if key == "pytest":
            scopes.add("pytest")
        elif key == "mypy":
            scopes.add("mypy")
        elif key == "ruff":
            scopes.add("lint")
        elif key == "setuptools":
            scopes.add("build")
        else:
            scopes.add("unknown")
    for key in (set(old) | set(new)) - {"project", "build-system", "tool"}:
        if old.get(key) != new.get(key):
            scopes.add("unknown")
    return ",".join(sorted(scopes))


def extract_ast_metadata(repo_root: str, out_path: str) -> dict | None:
    """Run the minimal AST extractor and write its JSON to out_path.

    Returns the parsed metadata dict, or None on ANY failure. The caller
    fails closed on None — a plan is never built without metadata (the
    line scanner is a weaker graph and must not silently substitute).
    """
    proc, _ = sh(
        [
            sys.executable,
            os.path.join(repo_root, "scripts", "ci", "ast_extract.py"),
            repo_root,
        ],
        cwd=repo_root,
        timeout=300,
    )
    if proc.returncode != 0:
        log(f"AST extraction failed (rc={proc.returncode}): {proc.stderr[-200:]}")
        return None
    try:
        meta = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        log(f"AST extraction produced invalid JSON: {exc}")
        return None
    if (
        not isinstance(meta, dict)
        or meta.get("schema") != "nse-ast-meta-v1"
        or not isinstance(meta.get("files"), dict)
    ):
        log("AST extraction produced unexpected schema (want nse-ast-meta-v1)")
        return None
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    return meta


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


def _ensure_cmake() -> str:
    """Return a cmake executable, bootstrapping it if absent.

    A fresh runner may have no cmake (tier-1, 2026-11-09: the Quality
    job died before the planner, so cmake presence there is unproven).
    The C engine bootstraps its own toolchain: install the `cmake` wheel
    (a pip package shipping a cmake binary) into THIS interpreter's
    environment via uv (`--python sys.executable`: a bare `uv pip
    install` fails with "No virtual environment found" because the
    persistent venv lives in ~/.cache, not ./.venv, and VIRTUAL_ENV is
    unset), falling back to the interpreter's pip. Returns the cmake
    command (bare name if on PATH, else the absolute venv-bin path).
    Raises RuntimeError with the manual install command if all fails.
    """
    found = shutil.which("cmake")
    if found:
        return found
    log("cmake not on PATH — bootstrapping the cmake wheel")
    candidates = []
    if shutil.which("uv"):
        candidates.append(["uv", "pip", "install", "--quiet", "--python", sys.executable, "cmake"])
    candidates.append([sys.executable, "-m", "pip", "install", "--quiet", "cmake"])
    installed = False
    for cmd in candidates:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode == 0:
            installed = True
            break
    if not installed:
        raise RuntimeError(
            "cmake not found and bootstrap failed; install cmake "
            "(apt-get install -y cmake, or `uv pip install cmake`) and retry"
        )
    found = shutil.which("cmake")
    if found:
        return found
    for cand in (
        os.path.join(os.path.dirname(sys.executable), "cmake"),
        os.path.join(os.path.dirname(sys.executable), "cmake.exe"),
    ):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise RuntimeError(
        "cmake installed but its binary is not on PATH; "
        "install cmake (apt-get install -y cmake) and retry"
    )


def ensure_native_engine(repo_root: str, persist_dir: str) -> str:
    """Return path to verified native C17 engine. Fail hard if unavailable."""
    # Windows binaries carry the .exe suffix; the build output name differs.
    exe = ".exe" if sys.platform == "win32" else ""
    candidate = os.path.join(persist_dir, "nse-ci-engine" + exe)
    if os.path.isfile(candidate) and engine_version(candidate) == ENGINE_VERSION:
        if selftest_ok(candidate):
            return candidate
        log("persistent native binary failed selftest — rebuilding")

    # Toolchain preflight: cmake bootstraps itself (see _ensure_cmake);
    # a C compiler cannot be pip-installed, so fail fast with the exact
    # install command instead of a confusing cmake-configure error.
    if not (shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")):
        raise RuntimeError(
            "no C compiler found (cc/gcc/clang); install build-essential "
            "(apt-get install -y build-essential) on the runner and retry"
        )
    cmake_cmd = _ensure_cmake()

    native_dir = os.path.join(repo_root, "scripts", "ci", "native")
    build_dir = os.path.join(native_dir, "build")
    jobs = max(1, min(os.cpu_count() or 2, 4))
    try:
        # sh() returns (proc, elapsed_seconds) — the build step below
        # already indexes [0]; this line did not, so the first build from
        # a cold persist dir died with AttributeError ('tuple' object has
        # no attribute 'returncode') on the OS lane (2026-11-09). The
        # bug hid locally because the persisted binary verified and the
        # build branch never ran.
        cfg = sh(
            [cmake_cmd, "-B", "build", "-DCMAKE_BUILD_TYPE=Release"], cwd=native_dir, timeout=180
        )[0]
        if cfg.returncode != 0:
            raise RuntimeError(f"cmake configure failed: {cfg.stderr[-200:]}")
        bld = sh([cmake_cmd, "--build", "build", "-j", str(jobs)], cwd=native_dir, timeout=600)[0]
        if bld.returncode != 0:
            raise RuntimeError(f"cmake build failed: {bld.stderr[-200:]}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"native build error: {exc}") from exc

    fresh = os.path.join(build_dir, "nse-ci-engine" + exe)
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


def c_plan(
    binary: str,
    repo_root: str,
    files: list[str],
    head: str,
    event: str,
    full: bool,
    metadata_path: str | None = None,
    pyproject_scope: str | None = None,
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
    if metadata_path:
        argv += ["--metadata", metadata_path]
    if pyproject_scope is not None:
        argv += ["--pyproject-scope", pyproject_scope]
    proc, _ = sh(argv, cwd=repo_root, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"native planner exit {proc.returncode}: {proc.stderr[-200:]}")
    obj = json.loads(proc.stdout)
    err = validate_plan(obj)
    if err:
        raise RuntimeError(f"native plan invalid: {err}")
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

    # Semantic scope of a pyproject.toml change (parsed-toml diff). The
    # C engine owns the invalidation verdict; Python only classifies.
    # NOTE: there is deliberately NO Python-side global-invalidation list
    # here — the canonical policy lives in the C engine (test_selector.c).
    pyproject_scope = None
    if "pyproject.toml" in changed:
        pyproject_scope = classify_pyproject_diff(repo_root, args.base, args.head)
        log(
            f"C17 PYPROJECT SCOPE: {pyproject_scope if pyproject_scope else '(no semantic change)'}"
        )

    # AST metadata: extracted on every plan and passed to the engine.
    # A failed extraction fails the lane closed (exit 90) — never plan
    # from the weaker line scanner in production.
    metadata_path = os.path.join(out_dir, "ast-metadata.json")
    meta = extract_ast_metadata(repo_root, metadata_path)
    if meta is None:
        log("AST metadata extraction failed — failing closed (no line-scan fallback)")
        return 90
    log(f"C17 METADATA: {len(meta['files'])} files, schema nse-ast-meta-v1 -> {metadata_path}")

    # 2. plan: C native ONLY — the sole planner. No Python fallback.
    planner_impl = f"native-c17-{ENGINE_VERSION}"
    try:
        native_obj = c_plan(
            native_binary,
            repo_root,
            changed,
            args.head,
            args.event,
            full,
            metadata_path=metadata_path,
            pyproject_scope=pyproject_scope,
        )
    except (RuntimeError, json.JSONDecodeError, OSError) as exc:
        log(f"NATIVE PLANNER FAILED: {exc}")
        return 90  # planner integrity failure — no fallback, fail closed

    plan_obj = native_obj  # C engine is the sole planner
    # --- C17 impact summary (visible in the job log) -------------------
    _sel = plan_obj["selected_tests"]
    _reasons = {}
    for _t in _sel:
        for _r in _t.get("reason", []):
            _reasons[_r] = _reasons.get(_r, 0) + 1
    _crit = sum(1 for _t in _sel if _t.get("priority") == "critical")
    # Total test files in the tree, so the log reads "7 of 860" instead of
    # a bare count: the whole point of the planner is selecting a few
    # from many, and the log must show that ratio at a glance.
    _tests_dir = pathlib.Path(repo_root) / "tests"
    total_tests = (
        sum(
            1
            for _p in _tests_dir.rglob("*.py")
            if _p.name.startswith("test_") or _p.name == "conftest.py"
        )
        if _tests_dir.is_dir()
        else 0
    )
    log(
        f"C17 PLAN: mode={plan_obj['selection_mode']} "
        f"full_suite={plan_obj['full_suite_required']} "
        f"selected={len(_sel)} of {total_tests} test files critical={_crit}"
    )
    log(
        f"C17 METADATA USED: {plan_obj.get('metadata_used')} "
        f"pyproject_scope={plan_obj.get('pyproject_scope')}"
    )
    if plan_obj.get("fallback_reason"):
        log(f"C17 FALLBACK: {plan_obj['fallback_reason']}")
    else:
        # Say so explicitly: silence here would leave the reader guessing
        # whether the fallback check ran at all.
        log("C17 FALLBACK: none — pure change-based selection")
    log("C17 REASONS: " + ", ".join(f"{k}={v}" for k, v in sorted(_reasons.items())))
    log(f"C17 CHANGED: {len(changed)} file(s) -> {len(_sel)} test file(s)")
    _shown = ", ".join(changed[:20])
    if len(changed) > 20:
        _shown += f", ... and {len(changed) - 20} more"
    log(f"C17 CHANGED FILES: {_shown}" if _shown else "C17 CHANGED FILES: (none)")
    log(f"C17 DIGEST: {plan_obj['plan_digest'][:16]}...")

    # 3. selected tests
    selected_ids = [t["id"] for t in plan_obj["selected_tests"]]
    if not selected_ids:
        log("empty selection — conservative expansion (empty never passes)")
        try:
            plan_obj = c_plan(
                native_binary,
                repo_root,
                changed,
                args.head,
                args.event,
                True,
                metadata_path=metadata_path,
                pyproject_scope=pyproject_scope,
            )
        except (RuntimeError, json.JSONDecodeError, OSError) as exc:
            log(f"conservative re-plan failed: {exc}")
            return 90
        selected_ids = [t["id"] for t in plan_obj["selected_tests"]]
        if not selected_ids:
            log("zero tests after expansion — failing closed")
            return 91
        log(
            f"C17 EXPANDED: selected={len(selected_ids)} "
            f"(fallback: {plan_obj.get('fallback_reason') or 'none'})"
        )

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
        "plan_metadata_used": plan_obj.get("metadata_used"),
        "plan_pyproject_scope": plan_obj.get("pyproject_scope"),
        "metadata_path": metadata_path,
        "metadata_schema": "nse-ast-meta-v1",
        "metadata_files": len(meta["files"]),
        "pyproject_scope": pyproject_scope,
        "planner": {
            "primary_impl": planner_impl,
            "engine_version": ENGINE_VERSION,
            "degraded": False,
            "native_reason": args.native_binary,
        },
        "selected_tests": selected_ids,
        "selected_count": len(selected_ids),
        "total_test_files": total_tests,
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

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Test Plan Execution\n\n")
            fh.write("| field | value |\n| --- | --- |\n")
            fh.write(f"| planner | `{planner_impl}` |\n")
            fh.write(f"| plan digest | `{manifest['plan_digest']}` |\n")
            fh.write(f"| selection mode | {manifest['plan_selection_mode']} |\n")
            fh.write(
                f"| fallback | {manifest.get('plan_fallback_reason') or 'none — pure change-based selection'} |\n"
            )
            fh.write(
                f"| selected tests | {len(selected_ids)} of {manifest['total_test_files']} |\n"
            )
            fh.write(f"| changed files | {len(changed)} |\n")
            fh.write(f"| pytest exit | {pytest_rc} |\n")
            fh.write(f"| runner | {args.runner_name} (attempt {args.attempt}) |\n")
            fh.write("\n### Selected tests\n\n")
            for tid in selected_ids:
                fh.write(f"- `{tid}`\n")

    return pytest_rc


if __name__ == "__main__":
    sys.exit(main())
