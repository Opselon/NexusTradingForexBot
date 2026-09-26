#!/usr/bin/env python3
"""PostgreSQL test-arm gate — silence is not coverage.

Lane O task 3. The PG integration arms of the suite skip silently when their
connection env var is unset (``NSE_PG_TEST_URL`` / ``NSE_TEST_PG_DSN``):

    tests/unit/test_pg_safety_persistence.py    NSE_PG_TEST_URL
    tests/unit/test_pg_store_paths.py           (no PG arm)
    tests/unit/test_fabric_guards.py            NSE_TEST_PG_DSN
    tests/unit/test_database_portability.py     NSE_PG_TEST_URL
    tests/unit/test_rtf001_real_postgresql_schema.py  NSE_PG_TEST_URL
    ...

That is how the empty live tables hid behind a green suite: the real
end-to-end write path never ran in CI, so a dead-lettered INSERT produced a
green run and a 0-row table.

CI now installs the ``postgres`` extra and provisions a throwaway PG service
container, so the env var IS set and these arms MUST run. This gate proves
it: it runs the PG-armed tests and FAILS when any of them reports ``skipped``
— a skip on a runner with a provisioned PG means the readiness probe lied
or the env var never reached pytest (the same silent-bypass class the CI
gate-integrity audit hunts).

Usage:
    python scripts/ci/check_pg_arm.py [--pytest PYTEST_ARGS...] [--json]

Exit codes: 0 = PG arm ran and passed; 1 = skipped/failed; 2 = tooling error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The connection env vars the suite's PG arms read. CI must export BOTH:
#: NSE_PG_TEST_URL is the URL convention (most arms) and NSE_TEST_PG_DSN is
#: the libpq-DSN convention (test_fabric_guards.py).
PG_ENV_VARS = ("NSE_PG_TEST_URL", "NSE_TEST_PG_DSN")

#: Test files with a real-PostgreSQL arm gated on a PG env var. Adding a
#: file here is a governance act: it means CI MUST provision PG for it.
PG_ARM_FILES: tuple[str, ...] = (
    "tests/unit/test_pg_safety_persistence.py",
    "tests/unit/test_pg_store_paths.py",
    "tests/unit/test_fabric_guards.py",
    "tests/unit/test_database_portability.py",
    "tests/unit/test_rtf001_real_postgresql_schema.py",
    "tests/unit/test_pg_schema_convergence.py",
    "tests/unit/test_candle_intel_pg_persistence.py",
    "tests/unit/test_rt003_guard_telemetry_counter.py",
    "tests/unit/test_sqlite_runtime_trap.py",
    "tests/unit/test_strategy_research_store.py",
)

_SKIP_REASONS_RE = re.compile(
    r"SKIPPED(?:\s*\[(?P<n>\d+)\])?\s*\[(?P<file>[^\]]+):(?P<line>\d+):\s*(?P<reason>[^\]]+)\]"
)


def _env_ok() -> list[str]:
    return [v for v in PG_ENV_VARS if os.environ.get(v, "").strip()]


def _junit_skips(junit: Path) -> list[dict[str, str]]:
    """Every skipped case with its reason, from the junit xml."""
    out: list[dict[str, str]] = []
    if not junit.is_file():
        return out
    try:
        tree = ET.parse(junit)
    except ET.ParseError:
        return out
    root = tree.getroot()
    nodes = root.findall("testsuite") if root.tag == "testsuites" else [root]
    for node in nodes:
        for tc in node.findall("testcase"):
            skipped = tc.find("skipped")
            if skipped is None:
                continue
            out.append(
                {
                    "name": f"{tc.attrib.get('classname', '')}::{tc.attrib.get('name', '')}",
                    "reason": (skipped.attrib.get("message") or "").strip(),
                }
            )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_pg_arm")
    ap.add_argument("--pytest", nargs=argparse.REMAINDER, default=[], help="extra pytest args")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--junit", type=Path, default=REPO_ROOT / "pg-arm-junit.xml")
    ap.add_argument(
        "--no-run", action="store_true", help="only verify env + files, do not run pytest"
    )
    args = ap.parse_args(argv)

    report: dict[str, object] = {"env": {}, "files": [], "skipped": [], "summary": {}}

    present = _env_ok()
    report["env"] = {v: ("set" if v in present else "UNSET") for v in PG_ENV_VARS}

    # 1. The PG env var must be present — otherwise every arm skips and the
    #    gate has nothing to prove (this is the CI-wiring contract).
    if not present:
        report["summary"] = {"status": "env-missing", "error": True}
        print(
            "PG ARM GATE: no PostgreSQL connection env var is set "
            f"({', '.join(PG_ENV_VARS)}). CI must install the postgres extra "
            "and provision a throwaway PG service container before this gate.",
            file=sys.stderr,
        )
        if args.json:
            print(json.dumps(report, indent=2))
        return 1

    files = [REPO_ROOT / f for f in PG_ARM_FILES]
    missing = [str(f.relative_to(REPO_ROOT)) for f in files if not f.is_file()]
    report["files"] = [str(f.relative_to(REPO_ROOT)) for f in files]
    if missing:
        report["summary"] = {"status": "files-missing", "missing": missing, "error": True}
        print(f"PG ARM GATE: listed PG-arm files are absent: {missing}", file=sys.stderr)
        if args.json:
            print(json.dumps(report, indent=2))
        return 2

    if args.no_run:
        report["summary"] = {"status": "env-ok-no-run", "error": False}
        print(f"PG ARM GATE: env ok ({', '.join(present)}); {len(files)} PG-arm files present")
        if args.json:
            print(json.dumps(report, indent=2))
        return 0

    # 2. Run the PG arm. psycopg must be importable (the postgres extra).
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *[str(f) for f in files],
        "--junitxml",
        str(args.junit),
        "-p",
        "no:cacheprovider",
        "-q",
        *args.pytest,
    ]
    print("PG ARM GATE: running", " ".join(cmd[:6]), "...", file=sys.stderr)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)

    skipped = _junit_skips(args.junit)
    report["skipped"] = skipped
    report["summary"] = {
        "pytest_exit_code": proc.returncode,
        "skipped_count": len(skipped),
        "stdout_tail": proc.stdout[-2000:],
    }

    # 3. A SKIP on a runner with a provisioned PG is a silent bypass.
    pg_skips = [
        s for s in skipped if "PG" in s["reason"].upper() or "POSTGRES" in s["reason"].upper()
    ]
    if pg_skips:
        report["summary"]["status"] = "pg-arm-skipped"
        report["summary"]["error"] = True
        print(
            "PG ARM GATE FAILED — PostgreSQL tests SKIPPED although a PG service "
            "was provisioned (the readiness/env wiring lied). Skipped:",
            file=sys.stderr,
        )
        for s in pg_skips:
            print(f"  - {s['name']}: {s['reason']}", file=sys.stderr)
        if args.json:
            print(json.dumps(report, indent=2))
        return 1

    if proc.returncode != 0:
        report["summary"]["status"] = "pg-arm-failed"
        report["summary"]["error"] = True
        print(f"PG ARM GATE FAILED — pytest rc={proc.returncode}", file=sys.stderr)
        print(proc.stdout[-4000:], file=sys.stderr)
        if args.json:
            print(json.dumps(report, indent=2))
        return 1

    report["summary"]["status"] = "pg-arm-ran"
    report["summary"]["error"] = False
    print(
        f"PG ARM GATE: PASS — PostgreSQL arm ran to green with "
        f"{', '.join(present)} ({len(files)} files, {len(skipped)} non-PG skip(s))"
    )
    if args.json:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
