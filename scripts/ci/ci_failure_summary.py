#!/usr/bin/env python3
"""CI failure summarizer — print structured failure records to the job log.

The quality job's pytest/ruff/mypy steps write their full output ONLY into
``$CI_RESULTS_DIR`` and then the artifact is uploaded. A red job therefore
showed in the GitHub Actions log as a single status line
(``CHECK pytest rc=1 status=failed``) while the actual failing test names,
assertions, durations and failure stages lived exclusively inside a zip the
developer had to download before knowing anything. That download is a TLS
round trip that often times out from this network, and even when it works it
is a minute of context-switching to learn a test name.

This tool reads the SAME results tree (never the network, never a second
tool invocation) and renders the failure record directly into the step log:
the failing tests with durations, the first meaningful assertion/error line
of each, the check that failed, and where the deep-dive artifact lives.

Two output modes:
  --mode log    human-readable grouped records (the default; for the step log)
  --mode json   machine-readable records (for aggregators / trend analysis)

Design rules:
  * ZERO network. Everything comes from files already on the runner.
  * Bounded output (default 25 records, 2000 chars per message excerpt) so a
    400-failure cascade never floods the log and hides the first failure.
  * Secrets are never present in these files, but every excerpt is passed
    through a conservative redactor anyway (defense in depth).
  * Never raises: a broken summary must not be able to turn a green run red
    or mask a real one. Errors degrade to a partial report.

Usage (called from ci.yml after the results tree is populated):

    python scripts/ci/ci_failure_summary.py --results "$CI_RESULTS_DIR"
    python scripts/ci/ci_failure_summary.py --results "$CI_RESULTS_DIR" --mode json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

#: Bounded output — a cascade must not flood the log and hide the FIRST
#: failure, which is almost always the root cause.
MAX_FAILURES = 25
#: Per-record text excerpt ceiling (a full traceback belongs in the artifact,
#: not the step log; the first meaningful line is what points at the cause).
MAX_EXCERPT_CHARS = 2000
#: Fields scanned in a JUnit failure/element; ``message`` is the assertion.
_JUNIT_TEXT_FIELDS = ("message", "text")

#: Never echoed even if they reached a results file (defense in depth — these
#: are not written there today, and this keeps it that way).
_REDACT_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(token=)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(password=)[^\s&'\"]+"),
    re.compile(r"(?i)(postgresql://[^:@/\s]+:)[^@/\s]+(@)"),
)


def redact(text: str) -> str:
    """Conservative scrub for the excerpt path. Never raises."""
    try:
        for pattern in _REDACT_PATTERNS:
            text = pattern.sub(r"\1***\2", text)
    except Exception:
        # Preserve the original text over a failed scrub: a lost secret-scan
        # is not a reason to destroy the diagnostic the developer needs.
        # These files are written by the repo's own gate scripts and never
        # carry credentials (scan_secrets.py blocks that upstream), so this
        # branch is defense in depth, not the primary control.
        pass
    return text


def _read_text(path: Path, limit: int = MAX_EXCERPT_CHARS) -> str:
    """Best-effort bounded read of a log file (tail-biased for tracebacks)."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(raw) > limit:
        raw = "…[truncated head]…\n" + raw[-limit:]
    return raw


def _first_meaningful_line(text: str) -> str:
    """The line a developer reads first: the assertion or the raised error.

    pytest's report order puts the failing ``assert`` and the exception
    ``E  ...`` line near the bottom of a failure block, so we scan from the
    END backwards for the first line that names the defect.
    """
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if (
            ln.startswith(("assert ", "AssertionError"))
            or ln.startswith("E  ")
            or re.match(r"^[A-Za-z_][\w.]*Error[:\s]", ln)
            or ln.startswith("FAILED ")
        ):
            return ln
    # Fall back to the last non-blank line — still a pointer, never a guess.
    return lines[-1] if lines else ""


def _parse_junit(root_dir: Path) -> list[dict[str, Any]]:
    """Failing test cases from the pytest junit.xml, with durations.

    JUnit is the machine-readable source of truth for pytest; the text log is
    only the excerpt provider.
    """
    junit_path = root_dir / "pytest" / "junit.xml"
    if not junit_path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        tree = ET.parse(junit_path)
    except Exception:
        return []
    top = tree.getroot()
    nodes = top.findall("testsuite") if top.tag == "testsuites" else [top]
    for suite in nodes:
        suite_name = suite.attrib.get("name", "?")
        for case in suite.findall("testcase"):
            failure = case.find("failure")
            error = case.find("error")
            node = failure if failure is not None else error
            if node is None:
                continue
            # A skipped test with a message is not a failure.
            if case.find("skipped") is not None:
                continue
            message = ""
            for field in _JUNIT_TEXT_FIELDS:
                value = node.attrib.get(field) or (node.text or "")
                if value.strip():
                    message = value.strip()
                    break
            records.append(
                {
                    "test_id": case.attrib.get("name", "?"),
                    "test_file": case.attrib.get("classname", suite_name),
                    "kind": "failure" if failure is not None else "error",
                    "duration_sec": float(case.attrib.get("time", 0) or 0),
                    "message": redact(message)[:MAX_EXCERPT_CHARS],
                }
            )
    return records


def _pytest_log_failures(root_dir: Path, test_ids: set[str]) -> dict[str, str]:
    """First-meaningful-line per failing test, mined from pytest.txt.

    junit.xml carries the assertion message; the text log carries the richer
    report (captured stdout, the last successful fixture stage). pytest emits
    a "_____ TestClass.test_name _____" separator directly ABOVE each
    failure block, so we slice on those separators and match the block to its
    test id — never to an unrelated FAILED line elsewhere in the summary.
    """
    log = _read_text(root_dir / "pytest" / "pytest.txt")
    if not log:
        return {}
    out: dict[str, str] = {}
    # pytest failure blocks look like:
    #   _______________________ test_name[params] _______________________
    separators = [m for m in re.finditer(r"^_{10,}\s*(\S.*?)\s*_{10,}\s*$", log, re.M)]
    if not separators:
        return {}
    separators.append(None)  # sentinel: the final block runs to the summary
    for i, m in enumerate(separators):
        if m is None:
            continue
        next_pos = (
            separators[i + 1].start()
            if separators[i + 1] is not None and separators[i + 1] is not None
            else len(log)
        )
        header = m.group(1)
        block = log[m.end() : next_pos]
        for tid in test_ids:
            if tid in header and tid not in out:
                line = _first_meaningful_line(block)
                if line:
                    out[tid] = redact(line)
    return out


def _tail_lines(path: Path, count: int) -> str:
    """The last `count` non-empty lines — for ruff/mypy, the violations."""
    text = _read_text(path)
    if not text:
        return ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return redact("\n".join(lines[-count:]))


def collect(root_dir: Path, *, max_failures: int = MAX_FAILURES) -> dict[str, Any]:
    """Assemble the whole failure picture from the results tree."""
    info = root_dir / "run-info"
    checks: list[dict[str, Any]] = []
    if info.is_dir():
        for p in sorted(info.glob("*.json")):
            if p.name in ("ai-analysis.json", "secrets-present.json"):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            status = str(data.get("status", "")).lower()
            if status not in ("failed", "errored"):
                continue
            checks.append(
                {
                    "check": data.get("check", p.stem),
                    "status": status,
                    "exit_code": data.get("exit_code"),
                    "detail": data.get("detail", ""),
                    "result_file": p.name,
                }
            )

    tests = _parse_junit(root_dir)
    test_ids = {t["test_id"] for t in tests}
    excerpts = _pytest_log_failures(root_dir, test_ids)
    for record in tests:
        record["first_meaningful_line"] = excerpts.get(record["test_id"], "")

    # ruff/mypy: the violations are the excerpt (bounded, in the log).
    # Only emit an excerpt for a check that actually failed — format.txt on a
    # passing run ends with "N files already formatted", which is not a
    # violation list and must not be rendered as one.
    def _failed(check: str) -> bool:
        p = info / f"{check}.json"
        if not p.is_file():
            return False
        try:
            return str(json.loads(p.read_text(encoding="utf-8")).get("status", "")).lower() in (
                "failed",
                "errored",
            )
        except Exception:
            return False

    ruff_violations = (
        _tail_lines(root_dir / "ruff" / "lint.txt", 15) if _failed("ruff_lint") else ""
    )
    format_files = (
        _tail_lines(root_dir / "format" / "format.txt", 15) if _failed("ruff_format") else ""
    )
    mypy_errors = (
        [
            redact(ln)
            for ln in _read_text(root_dir / "mypy" / "mypy.txt").splitlines()
            if "error:" in ln
        ][-15:]
        if _failed("mypy")
        else []
    )

    return {
        "checks_failed": checks,
        "tests_failed": tests[:max_failures],
        "tests_failed_total": len(tests),
        "truncated": len(tests) > max_failures,
        "ruff_violations": ruff_violations,
        "ruff_format_files": format_files,
        "mypy_errors": mypy_errors,
    }


def render_log(report: dict[str, Any]) -> str:
    """Human-readable grouped record for the step log."""
    lines: list[str] = []
    checks = report.get("checks_failed", [])
    tests = report.get("tests_failed", [])
    ruff = report.get("ruff_violations", "")
    fmt = report.get("ruff_format_files", "")
    mypy = report.get("mypy_errors", [])
    if not checks and not tests and not ruff and not fmt and not mypy:
        return "CI FAILURE SUMMARY: no failures recorded in the results tree."
    lines.append("┌─ CI FAILURE SUMMARY ─────────────────────────────────────────────")
    for c in checks:
        lines.append(f"│ CHECK: {c['check']}  ->  {c['status'].upper()} (rc={c['exit_code']})")
        if c.get("detail"):
            lines.append(f"│   {c['detail']}")
    if tests:
        lines.append("│")
        lines.append(
            f"│ FAILING TESTS ({report.get('tests_failed_total', len(tests))}"
            + (f" — first {MAX_FAILURES}" if report.get("truncated") else "")
            + "):"
        )
        for t in tests:
            dur = t.get("duration_sec") or 0.0
            lines.append(f"│ • {t['test_file']}::{t['test_id']}   [{dur:.2f}s]")
            if t.get("first_meaningful_line"):
                lines.append(f"│     {t['first_meaningful_line']}")
            elif t.get("message"):
                lines.append(f"│     {t['message']}")
    if mypy:
        lines.append("│")
        lines.append("│ MYPY ERRORS (last 15):")
        for ln in mypy:
            lines.append(f"│   {ln}")
    if ruff:
        lines.append("│")
        lines.append("│ RUFF LINT (last 15 lines):")
        for ln in ruff.splitlines():
            lines.append(f"│   {ln}")
    if fmt:
        lines.append("│")
        lines.append("│ RUFF FORMAT (files that would be reformatted):")
        for ln in fmt.splitlines():
            lines.append(f"│   {ln}")
    lines.append("│")
    lines.append("│ Deep dive: the ci-results artifact holds full logs + junit.xml")
    lines.append("│   pytest/<check>.txt, pytest/junit.xml, mypy/mypy.txt, ruff/lint.txt")
    lines.append("└──────────────────────────────────────────────────────────────────")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", required=True, help="ci-results tree root")
    ap.add_argument("--mode", choices=("log", "json"), default="log")
    ap.add_argument("--max", type=int, default=0, help="max failure records to print")
    args = ap.parse_args(argv)

    root = Path(args.results)
    if not root.is_dir():
        print(f"ci_failure_summary: results tree {root} not found; nothing to summarize")
        return 0
    # A bounded cascade: never let a 400-failure run flood the log and hide
    # the FIRST failure, which is almost always the root cause.
    ceiling = max(1, args.max) if args.max > 0 else MAX_FAILURES
    try:
        report = collect(root, max_failures=ceiling)
    except Exception as e:  # a broken summary must never mask a real failure
        print(f"ci_failure_summary: FAILED to build the report ({type(e).__name__}: {e})")
        return 0

    if args.mode == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_log(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
