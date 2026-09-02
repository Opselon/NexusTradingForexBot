#!/usr/bin/env python3
"""CI result pipeline for the NexusTradingForexBot GitHub Actions workflow.

This script runs INSIDE the GitHub Actions runner (and locally for testing).
It is the single authority for:

  * resetting the ci-results/ tree (one run = one clean result set)
  * writing run metadata (run-info/metadata.json)
  * writing the result manifest (run-info/manifest.json)
  * writing SHA256 checksums (run-info/SHA256SUMS.txt)
  * writing the human-readable summary (run-info/summary.md)

It never inspects or writes secrets. It only records the PRESENCE of
configured secrets (true/false), never their values. Secret presence markers
come from environment variables whose value is NEVER printed.

CLI (used by ci.yml):
  make_ci_results.py init <results-root> <json>   -> clean + init tree, write metadata
  make_ci_results.py manifest <results-root>      -> scan tree, write manifest.json + SHA256SUMS.txt
  make_ci_results.py summary <results-root> <json>-> write run-info/summary.md
  make_ci_results.py secret-check <results-root> <json> -> record secret PRESENCE booleans
  make_ci_results.py list-checks <results-root>   -> print installed tool versions + formats (debug)

Exit codes: 0 on success, 1 on any error unless --continue-on-error is passed
(used for reporting steps so failures there never hide real test results).
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
from pathlib import Path

# Tools that must NOT be invoked from inside this script in a way that could
# ever surface a secret value. Secret presence is checked via
# "is-configured" style flags only.
SECRET_PRESENCE_ENV = [
    "NEXUS_TELEGRAM_BOT_TOKEN",
    "NEXUS_TELEGRAM_ADMIN_ID",
    "CODECOV_TOKEN",
]

TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUTHY


def _clean_init(root: Path) -> None:
    """Remove any stale local result tree, then create the canonical skeleton."""
    if root.exists():
        shutil.rmtree(root)
    for sub in ("run-info", "ruff", "format", "mypy", "pytest", "github"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "pytest" / "htmlcov").mkdir(parents=True, exist_ok=True)


def _env_bool(name: str, default: bool = False) -> bool:
    return _is_truthy(os.environ.get(name)) if os.environ.get(name) is not None else default


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _tool_version(tool: str) -> str:
    try:
        out = subprocess.run(
            [tool, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
        return (
            (out.stdout or out.stderr).strip().splitlines()[0]
            if (out.returncode == 0)
            else "unavailable"
        )
    except Exception:
        return "unavailable"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    _clean_init(root)

    metadata = {
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "sha": os.environ.get("GITHUB_SHA", ""),
        "ref": os.environ.get("GITHUB_REF", ""),
        "branch": os.environ.get("GITHUB_REF_NAME", ""),
        "event": os.environ.get("GITHUB_EVENT_NAME", ""),
        "actor": os.environ.get("GITHUB_ACTOR", ""),
        "server_url": os.environ.get("GITHUB_SERVER_URL", ""),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": os.environ.get("PYTHON_VERSION", ""),
        "runner_os": os.environ.get("RUNNER_OS", ""),
        "status": "running",
        "artifact": os.environ.get("CI_ARTIFACT_NAME", ""),
    }
    _write_json(root / "run-info" / "metadata.json", metadata)

    # Canonical env digest used by report steps (JSON).
    env_digest = {
        "workflow": metadata["workflow"],
        "run_id": metadata["run_id"],
        "run_number": metadata["run_number"],
        "sha": metadata["sha"],
        "ref": metadata["ref"],
        "branch": metadata["branch"],
        "event": metadata["event"],
        "actor": metadata["actor"],
        "timestamp": metadata["timestamp"],
        "python_version": metadata["python_version"],
        "runner_os": metadata["runner_os"],
        "to": str(root),
        "continue": _env_bool("CONTINUE_ON_ERROR"),
        "secrets_present": _env_bool("SECRETS_PRESENT", True),
        "artifact": metadata["artifact"],
    }
    if args.json:
        _write_json(Path(args.json), env_digest)
        print(f"WROTE {args.json}")
    else:
        print(json.dumps(env_digest, indent=2, sort_keys=True))
    print(f"INIT {root} OK")
    return 0


def _write_check(root: Path, check: str, status: str, detail: str = "", exit_code: int = 0) -> None:
    """Write a check-status file: ci-results/run-info/<check>.json.

    Status values: passed | failed | errored | skipped. exit_code is the
    tool's real exit code (0 for passed); failures are NEVER hidden.
    """
    _write_json(
        root / "run-info" / f"{check}.json",
        {
            "check": check,
            "status": status,
            "exit_code": int(exit_code),
            "detail": detail,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def cmd_manifest(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: {root} does not exist", file=sys.stderr)
        return 1
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_info = root / "run-info"
    files: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        files.append(
            {
                "path": rel,
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )

    manifest = {
        "run_id": run_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": files,
    }
    _write_json(run_info / "manifest.json", manifest)

    # Checksums cover every file EXCEPT SHA256SUMS.txt and manifest.json
    # (self-referential hashes would be unstable). LF-only newlines so the
    # file verifies on Linux runners with plain `sha256sum -c` (CRLF would
    # stick a \r to every filename).
    lines: list[str] = []
    for entry in files:
        if entry["path"] in ("run-info/SHA256SUMS.txt", "run-info/manifest.json"):
            continue
        lines.append(f"{entry['sha256']}  {entry['path']}")
    (run_info / "SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )

    print(f"MANIFEST {len(files)} files, {len(lines)} checksummed -> {run_info}")
    return 0


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _junit_stats(path: Path) -> dict:
    """Parse tests/errors/failures/skipped/time from pytest's JUnit XML.

    Handles both root <testsuite> and pytest's <testsuites> wrapper (the
    aggregate counts sit on the child <testsuite> in xunit2 output).
    """
    stats = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(path)
        root = tree.getroot()
        if root.tag == "testsuites":
            nodes = root.findall("testsuite")
        else:
            nodes = [root]
        for node in nodes:
            for attr in ("tests", "failures", "errors", "skipped", "time"):
                if attr in node.attrib:
                    try:
                        stats[attr] += float(node.attrib[attr])
                    except ValueError:
                        pass
    except Exception:
        pass
    return stats


def _coverage_pct(path: Path) -> float | None:
    """Read line-rate from a Cobertura coverage.xml (package-level average)."""
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(path)
        root = tree.getroot()
        rate = root.attrib.get("line-rate")
        if rate is None:
            # Sum over <class> elements if the root has no aggregate rate.
            classes = root.findall(".//class")
            if classes:
                total = 0.0
                covered = 0.0
                for c in classes:
                    total += float(c.attrib.get("lines-valid", 0) or 0)
                    covered += float(c.attrib.get("lines-covered", 0) or 0)
                if total > 0:
                    return covered / total * 100.0
            return None
        return float(rate) * 100.0
    except Exception:
        return None


def _read_check(root: Path, check: str) -> dict:
    data = _load_json(root / "run-info" / f"{check}.json")
    if data is None:
        return {"check": check, "status": "skipped", "exit_code": 0, "detail": "no result recorded"}
    return data


def cmd_summary(args: argparse.Namespace) -> int:
    root = Path(args.root)
    md_path = root / "run-info" / "summary.md"
    if not root.exists():
        print(f"ERROR: {root} does not exist", file=sys.stderr)
        return 1

    meta = _load_json(root / "run-info" / "metadata.json") or {}
    checks = {
        "Ruff Lint": _read_check(root, "ruff_lint"),
        "Ruff Format": _read_check(root, "ruff_format"),
        "Mypy": _read_check(root, "mypy"),
        "Pytest": _read_check(root, "pytest"),
        "Coverage": _read_check(root, "coverage"),
    }
    # Heavy suites only exist when the matrix ran; include rows conditionally.
    heavy = [
        ("Integration", "integration"),
        ("E2E", "e2e"),
        ("Research/Backtest", "research"),
        ("Model Validation", "model"),
    ]
    heavy_rows = []
    for label, key in heavy:
        # In merged aggregate runs, per-arm status files live under
        # heavy/<suite>/run-info/<suite>.json; in direct runs they are at the
        # top level. Try both so the summary always reflects real suite results.
        candidates = (
            root / "run-info" / f"{key}.json",
            root / "heavy" / key / "run-info" / f"{key}.json",
        )
        data = None
        for cand in candidates:
            data = _load_json(cand)
            if data is not None and data.get("status") in ("passed", "failed", "errored"):
                break
        if data is not None:
            heavy_rows.append((label, data))

    junit = _load_json(root / "run-info" / "pytest-extra.json") or {}
    stats = _junit_stats(root / "pytest" / "junit.xml")
    coverage_pct = _coverage_pct(root / "pytest" / "coverage.xml")
    if coverage_pct is None:
        cov_extra = _load_json(root / "run-info" / "coverage-extra.json") or {}
        coverage_pct = cov_extra.get("percent")

    overall = (
        "FAILED"
        if any(c.get("status") in ("failed", "errored") for c in checks.values())
        or any(d.get("status") in ("failed", "errored") for _, d in heavy_rows)
        else "PASSED"
    )

    lines: list[str] = []
    lines.append("# CI Validation Summary")
    lines.append("")
    lines.append(f"- **Run:** {meta.get('run_number', '?')} (id {meta.get('run_id', '?')})")
    lines.append(f"- **Commit:** `{meta.get('sha', '?')}`")
    lines.append(f"- **Branch:** `{meta.get('ref', meta.get('branch', '?'))}`")
    lines.append(f"- **Workflow:** {meta.get('workflow', '?')} — **Overall: {overall}**")
    lines.append(f"- **Timestamp (UTC):** {meta.get('timestamp', '?')}")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Check | Status | Details |")
    lines.append("|---|---|---|")
    for label, c in checks.items():
        lines.append(f"| {label} | {c.get('status', '?').upper()} | {c.get('detail', '')} |")
    for label, d in heavy_rows:
        lines.append(
            f"| {label} (heavy) | {d.get('status', '?').upper()} | {d.get('detail', '')} |"
        )
    lines.append("")
    # Blocked-state contract (CHG-0052): cancelled-downstream transparency.
    # A 'blocked' row means the check NEVER RAN because an upstream gate
    # failed — it is not a failure of that check and must not read as one.
    blocked_rows = [(label, c) for label, c in checks.items() if c.get("status") == "blocked"]
    if blocked_rows:
        names = ", ".join(label for label, _ in blocked_rows)
        lines.append("**DOWNSTREAM: BLOCKED BY ROOT FAILURE** — the following checks never ran")
        lines.append(f"because an upstream gate failed (cancelled upstream, NOT errored): {names}.")
        lines.append("")
    repair_report = _load_json(root / "ruff-repair-report.json")
    if repair_report:
        src_clean = bool(repair_report.get("source_tree_was_clean"))
        lines.append("## Ruff Format Source State (CHG-0052)")
        lines.append("")
        lines.append("- ROOT FAILURE: ruff format --check . (source tree)")
        lines.append(
            f"- SOURCE TREE FORMAT STATUS: "
            f"{'CLEAN' if src_clean else 'DIRTY — committed tree was malformed'}"
        )
        lines.append(f"- AUTO-REPAIR: {str(repair_report.get('repair_status', 'unknown')).upper()}")
        lines.append(
            "- POST-REPAIR CHECK: "
            + ("PASS" if repair_report.get("post_repair_format_exit_code") == 0 else "FAIL/NOT RUN")
        )
        lines.append(f"- COMMITTED TREE WAS ALREADY CLEAN: {str(src_clean).upper()}")
        lines.append(
            "- PERSISTENCE: NOT COMMITTED — patch artifact: "
            f"{repair_report.get('patch_file') or 'n/a'}"
        )
        files_off = repair_report.get("files_offending") or []
        if files_off:
            shown = ", ".join(str(f) for f in files_off[:20])
            lines.append(f"- FILES: {shown}")
        lines.append("")

    lines.append("## Test Statistics")
    lines.append("")
    tests = int(stats.get("tests", 0))
    failed = int(stats.get("failures", 0)) + int(stats.get("errors", 0))
    skipped = int(stats.get("skipped", 0))
    passed = tests - failed - skipped
    lines.append(f"- Tests: {tests}")
    lines.append(f"- Passed: {passed}")
    lines.append(f"- Failed: {failed}")
    lines.append(f"- Skipped: {skipped}")
    lines.append(
        f"- Coverage: {coverage_pct:.1f}%" if coverage_pct is not None else "- Coverage: n/a"
    )
    if junit.get("detail"):
        lines.append(f"- Note: {junit['detail']}")
    lines.append("")
    lines.append("## Build Environment")
    lines.append("")
    lines.append(f"- Python: {meta.get('python_version', '?')}")
    lines.append(f"- OS: {meta.get('runner_os', '?')}")
    lines.append(f"- Event: {meta.get('event', '?')} / Actor: {meta.get('actor', '?')}")
    lines.append(
        "- Tools: "
        + ", ".join(
            f"{name} {ver}"
            for name, ver in [
                ("ruff", _tool_version("ruff")),
                ("mypy", _tool_version("mypy")),
                ("pytest", _tool_version("pytest")),
            ]
        )
    )
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    lines.append("Full machine-readable results are in this artifact: `ci-results/`.")
    lines.append(
        "- `run-info/summary.md` (this file) · `run-info/manifest.json` (all files + sha256)"
    )
    lines.append(
        "- `run-info/SHA256SUMS.txt` (checksums) · `run-info/*.json` (per-check status + exit codes)"
    )
    lines.append("- `ruff/lint.json` + `lint.txt` · `format/format.txt` · `mypy/mypy.txt`")
    lines.append(
        "- `pytest/junit.xml` + `pytest.txt` + `coverage.xml` (+ `pytest/htmlcov/` when generated)"
    )
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"SUMMARY -> {md_path}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Record one check's outcome from its real exit code (workflow helper).

    usage: make_ci_results.py check <root> <check-name> <rc> [detail]
    status derived: rc==0 -> passed; rc==1 -> failed; else -> errored.
    """
    root = Path(args.root)
    (root / "run-info").mkdir(parents=True, exist_ok=True)
    try:
        rc = int(args.rc)
    except (TypeError, ValueError):
        _write_check(
            root, args.check_name, "errored", "no rc captured (tool did not exit normally)", -1
        )
        print(f"CHECK {args.check_name} rc=<empty> status=errored")
        return 0
    if getattr(args, "blocked", False):
        # CHG-0052: explicit blocked record — the step did NOT run because an
        # upstream gate failed. rc stays as evidence of the blocker context;
        # a blocked step is never "passed" just because rc==0 was recorded.
        _write_check(
            root, args.check_name, "blocked", args.detail or "blocked by upstream gate", rc
        )
        print(f"CHECK {args.check_name} rc={rc} status=blocked")
        return 0
    status = "passed" if rc == 0 else "failed" if rc == 1 else "errored"
    detail = args.detail or ("passed" if rc == 0 else "failed (see artifact)")
    _write_check(root, args.check_name, status, detail, rc)
    print(f"CHECK {args.check_name} rc={rc} status={status}")
    return 0


#: Blocked-state contract (CHG-0052, run #685 class): an upstream gate failure
#: CANCELS the downstream steps, so their result JSONs never appear. A missing
#: JSON is NOT an "errored" check — it is BLOCKED by the named root failure
#: when one exists, and only "skipped" when nothing failed upstream.
DOWNSTREAM_CHECKS = ("mypy", "pytest", "coverage")


def classify_gate(root: Path, root_failure: str = "") -> int:
    """Re-classify missing downstream results after an upstream failure.

    usage: make_ci_results.py classify-gate <root> [--root-failure ruff_format]

    For every check in DOWNSTREAM_CHECKS with no result JSON:
      * a root failure was named   -> write status=blocked
        (detail: "BLOCKED_BY_<ROOT> - upstream gate failed; step never ran")
      * no root failure was named  -> write status=skipped
        ("no result recorded - no upstream failure named")

    Existing result files are NEVER touched (a real failed/errored check
    keeps its own status). Exit 0 always: classification must not fail the
    job it is describing.
    """
    run_info = root / "run-info"
    run_info.mkdir(parents=True, exist_ok=True)
    root_failure = (root_failure or "").strip()
    for check in DOWNSTREAM_CHECKS:
        target = run_info / f"{check}.json"
        if target.exists():
            # An existing BLOCKED record (written via `check ... --blocked` in
            # the workflow fallback) must stay blocked, never be re-interpreted.
            continue
        if root_failure:
            _write_check(
                root,
                check,
                "blocked",
                f"BLOCKED_BY_{root_failure.upper()} - upstream gate failed; step never ran",
                0,
            )
            print(f"CLASSIFY {check}: blocked by {root_failure}")
        else:
            _write_check(
                root, check, "skipped", "no result recorded - no upstream failure named", 0
            )
            print(f"CLASSIFY {check}: skipped (no upstream failure named)")
    return 0


def cmd_secret_check(args: argparse.Namespace) -> int:
    """Record secret PRESENCE only (true/false). Values are never read or printed."""
    root = Path(args.root)
    run_info = root / "run-info"
    run_info.mkdir(parents=True, exist_ok=True)
    presence: dict[str, bool] = {}
    for name in SECRET_PRESENCE_ENV:
        present = bool(os.environ.get(name, "").strip())
        presence[name] = present
    # Report whether ANY secret is configured (the workflow uses this to
    # decide whether to mark coverage as errored when the upload is skipped).
    _write_json(
        run_info / "secrets-present.json",
        {
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "present": {k: v for k, v in presence.items()},
            "any": any(presence.values()),
        },
    )
    print(f"SECRET-CHECK stored presence only -> {run_info / 'secrets-present.json'}")
    return 0


def cmd_list_checks(args: argparse.Namespace) -> int:
    """Debug aid: print installed tool versions and supported output formats."""
    print(
        json.dumps(
            {
                "ruff": _tool_version("ruff"),
                "mypy": _tool_version("mypy"),
                "pytest": _tool_version("pytest"),
                "python": _tool_version("python"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(
        "Ruff output formats: concise, full, json, json-lines, junit, grouped, github, gitlab, pylint, rdjson, azure, sarif"
    )
    print(
        "Mypy output: text (--no-error-summary), JUnit XML (--junit-xml), JSON via --junit-format=global"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NSE CI results pipeline (run inside GitHub Actions / locally)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="reset ci-results/ and write run metadata")
    p_init.add_argument("root")
    p_init.add_argument(
        "--json", default="", help="also write env digest JSON (used by report steps)"
    )
    p_init.set_defaults(func=cmd_init)

    p_man = sub.add_parser(
        "manifest", help="scan ci-results/ and write manifest.json + SHA256SUMS.txt"
    )
    p_man.add_argument("root")
    p_man.set_defaults(func=cmd_manifest)

    p_sum = sub.add_parser("summary", help="write run-info/summary.md from per-check JSONs")
    p_sum.add_argument("root")
    p_sum.set_defaults(func=cmd_summary)

    p_sec = sub.add_parser("secret-check", help="record secret PRESENCE booleans (no values)")
    p_sec.add_argument("root")
    p_sec.set_defaults(func=cmd_secret_check)

    p_chk = sub.add_parser("check", help="record one check outcome from its exit code")
    p_chk.add_argument("root")
    p_chk.add_argument("check_name")
    p_chk.add_argument("rc", type=int)
    p_chk.add_argument("detail", nargs="?", default="")
    p_chk.add_argument(
        "--blocked",
        action="store_true",
        help="record status=blocked (step never ran - upstream gate failed)",
    )
    p_chk.set_defaults(func=cmd_check)

    p_cls = sub.add_parser(
        "classify-gate",
        help="re-classify missing downstream results as blocked/skipped (never errored)",
    )
    p_cls.add_argument("root")
    p_cls.add_argument(
        "--root-failure",
        default="",
        help="check name of the upstream root failure (e.g. ruff_format)",
    )
    p_cls.set_defaults(func=lambda a: classify_gate(Path(a.root), a.root_failure))

    sub.add_parser("list-checks", help="print installed tool versions/formats").set_defaults(
        func=cmd_list_checks
    )

    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
