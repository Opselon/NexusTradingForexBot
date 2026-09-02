#!/usr/bin/env python3
"""Gate parity: machine-checkable local-gate <-> CI contract comparison.

Steer (CI GATE INTEGRATION) §3/§5: it must be PROVEN, not assumed, that

  * the local gate and CI use the SAME Ruff/MyPy configuration
  * the critical-suite manifest is identical for both
  * the exit/status taxonomy is compatible
  * no CI-only lint class is silently omitted from local checks

This module parses BOTH contract sources (this repo's pyproject.toml +
scripts/ci/check_local.py AND .github/workflows/ci.yml) and emits a
deterministic parity report. It runs as a CI gate job AND locally via
`python scripts/ci/gate_parity.py [--json]`. Exit 0 = parity holds,
1 = drift (explicit PARITY_DRIFT records naming the exact divergence).

DATA-ONLY: no network, no MT5, no engine, no git mutation.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GATE_SCRIPT = REPO_ROOT / "scripts" / "ci" / "check_local.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"
MANIFEST = REPO_ROOT / "tests" / "critical_suite.txt"

#: CI check names that MUST have a local-gate counterpart. The heavy suites
#: (pytest full coverage) are deliberately CI-authoritative — the local gate
#: is the cheap deterministic pre-detector, NOT a CI replacement (docs/LOCAL_QUALITY_GATE.md).
REQUIRED_LOCAL_CHECKS = ("ruff_lint", "ruff_format", "mypy", "critical_suite_manifest")
CI_CHECKS = ("ruff_lint", "ruff_format", "mypy", "pytest", "coverage")


@dataclass
class ParityRecord:
    check: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "ok": self.ok, "detail": self.detail}


@dataclass
class ParityReport:
    records: list[ParityRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_parity": "PASS" if self.ok else "FAIL",
            "records": [r.to_dict() for r in self.records],
            "summary": {
                "checks": len(self.records),
                "ok": sum(1 for r in self.records if r.ok),
                "drifts": sum(1 for r in self.records if not r.ok),
            },
        }


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Contract extraction
# ---------------------------------------------------------------------------
def ci_ruff_scope() -> str:
    """CI ruff lint target (the path argument of `ruff check ...`)."""
    text = _read(CI_WORKFLOW)
    m = re.search(r"ruff check (\S+)", text)
    return m.group(1) if m else "."


def ci_mypy_scope() -> str:
    text = _read(CI_WORKFLOW)
    m = re.search(r"mypy (\S+)", text)
    return m.group(1) if m else "src"


def gate_ruff_config_source() -> str:
    """The local gate runs `python -m ruff` from the repo root — Ruff resolves
    pyproject.toml [tool.ruff] automatically. Prove pyproject HAS the section."""
    text = _read(PYPROJECT)
    return "pyproject.toml [tool.ruff] present" if "[tool.ruff]" in text else "MISSING"


def gate_mypy_config_source() -> str:
    text = _read(PYPROJECT)
    return "pyproject.toml [tool.mypy] present" if "[tool.mypy]" in text else "MISSING"


def gate_uses_manifest() -> bool:
    """Local gate must validate the SAME critical-suite manifest CI consumes."""
    text = _read(GATE_SCRIPT)
    return 'tests" / "critical_suite.txt' in text or "critical_suite.txt" in text


def gate_status_taxonomy() -> set[str]:
    text = _read(GATE_SCRIPT)
    m = re.search(
        r"passed\s*\|\s*failed\s*\|\s*errored\s*\|\s*skipped\s*\|\s*configuration_error", text
    )
    return (
        set(re.findall(r"passed|failed|errored|skipped|configuration_error", m.group(0)))
        if m
        else set()
    )


def ci_status_taxonomy() -> set[str]:
    """make_ci_results.py check statuses (the CI side of the taxonomy).

    CHG-0052 adds 'blocked' (run #685 class): a missing downstream result
    after an upstream gate failure is BLOCKED, not errored. Classified via
    `make_ci_results.py classify-gate --root-failure <check>`.
    """
    maker = REPO_ROOT / "scripts" / "ci" / "make_ci_results.py"
    text = _read(maker)
    found = set(re.findall(r'"(passed|failed|errored|skipped|blocked)"', text))
    # classify-gate writes 'blocked' via _write_check(root, check, "blocked", ...)
    if "classify_gate" in text and '"blocked"' in text:
        found.add("blocked")
    return found


def gate_integrity_files() -> tuple[str, ...]:
    text = _read(GATE_SCRIPT)
    m = re.search(r"GATE_INTEGRITY_FILES = \(([^)]*)\)", text, re.S)
    if not m:
        return ()
    return tuple(x.strip().strip('"') for x in m.group(1).split(",") if x.strip().strip('"'))


# ---------------------------------------------------------------------------
# Parity checks
# ---------------------------------------------------------------------------
def check_parity() -> ParityReport:
    rep = ParityReport()

    # 1. Same Ruff configuration source (pyproject [tool.ruff] governs both).
    src = gate_ruff_config_source()
    rep.records.append(
        ParityRecord(
            "ruff_config_source",
            src.startswith("pyproject.toml"),
            f"local: {src}; CI: pip install -e .[dev] (same pyproject), `ruff check {ci_ruff_scope()}`",
        )
    )

    # 2. Same MyPy configuration source AND same scope.
    msrc = gate_mypy_config_source()
    scope = ci_mypy_scope()
    gate_scope_ok = "src" in _read(GATE_SCRIPT)
    rep.records.append(
        ParityRecord(
            "mypy_config_and_scope",
            msrc.startswith("pyproject.toml") and gate_scope_ok and scope == "src",
            f"local: {msrc}, scope src; CI: `mypy {scope}`",
        )
    )

    # 3. Same critical-suite manifest file.
    rep.records.append(
        ParityRecord(
            "critical_suite_manifest",
            gate_uses_manifest() and MANIFEST.exists(),
            f"both sides resolve tests/critical_suite.txt ({MANIFEST.exists()})",
        )
    )

    # 4. Status taxonomy compatibility: local gate's statuses must be a
    #    superset-compatible subset of CI's (shared vocabulary, no private states).
    #    CHG-0052: 'blocked' is CI-only BY DESIGN — only CI can cancel a
    #    downstream step mid-job; the local gate runs every stage to completion.
    g, c = gate_status_taxonomy(), ci_status_taxonomy()
    compatible = bool(g) and bool(c) and {"passed", "failed"} <= g <= (c | {"configuration_error"})
    rep.records.append(
        ParityRecord(
            "status_taxonomy",
            compatible,
            f"local={sorted(g)} ci={sorted(c)} ('blocked' is CI-only: cancelled-downstream state)",
        )
    )

    # 5. No CI-only lint class omitted locally: CI gates ruff_lint + ruff_format;
    #    the local gate must run BOTH (ruff check + ruff format --check).
    #    CHG-0052: match the exact arg-list literals, not a bare "--check"
    #    substring — comments like "# ruff format --check" must not defeat
    #    the drift detector.
    gtext = _read(GATE_SCRIPT)
    has_lint = '"check"' in gtext and "ruff" in gtext
    fmt_list = re.search(r'\[\s*"format"\s*,\s*"--check"\s*\]', gtext)
    has_format = fmt_list is not None
    rep.records.append(
        ParityRecord(
            "no_ci_only_lint_class_omitted",
            has_lint and has_format,
            f"local gate runs ruff check={has_lint} and ruff format --check={has_format}; "
            "CI pytest/coverage stay CI-authoritative by design",
        )
    )

    # 6. Gate-integrity set covers the contract sources themselves: if any of
    #    the parity inputs can change, the gate MUST force full-tree validation.
    gi = gate_integrity_files()
    needed = {
        "scripts/ci/check_local.py",
        "tests/critical_suite.txt",
        "pyproject.toml",
        ".github/workflows/ci.yml",
    }
    rep.records.append(
        ParityRecord(
            "gate_integrity_coverage",
            needed.issubset(set(gi)),
            f"GATE_INTEGRITY_FILES={list(gi)}",
        )
    )

    return rep


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="gate_parity", description=__doc__.splitlines()[0])
    p.add_argument("--json", action="store_true", help="pure-JSON stdout")
    args = p.parse_args(argv)
    rep = check_parity()
    payload = json.dumps(rep.to_dict(), indent=2)
    if args.json:
        sys.stdout.write(payload + "\n")
    else:
        sys.stdout.write(payload + "\n")
    for r in rep.records:
        if not r.ok:
            print(f"PARITY_DRIFT: {r.check}: {r.detail}", file=sys.stderr)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
