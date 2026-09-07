"""CRITICAL-PATH MANIFEST ENFORCEMENT (QA hardening mission, P0).

The critical suite (tests/critical_suite.txt) is a list of TEST files.
This module inverts the view: it maps CRITICAL SOURCE modules to the
test files that guard them, and FAILS when a critical source module has
no critical-suite coverage mapping.

Policy (hybrid, automation-first):
  * REQUIRED_CRITICAL_SOURCES - explicit, human-owned floor of
    execution-critical modules that must ALWAYS map to the critical
    suite (live_engine, order_manager, risk_engine, policy, ...).
  * Hot-path discovery from the dependency-intelligence graph
    (artifacts/dependency_intelligence/graph.json): any module whose
    hotspot score exceeds HOTSPOT_SCORE_FLOOR is treated as a
    discovered-critical candidate. Missing mappings for those surface
    as WARNINGS (drift visibility) while explicit floor gaps FAIL.
  * A source module is "covered" when at least one critical-suite test
    file EXISTS and imports/exercises the module (measured by reading
    the test file for the module's dotted path or file name).

Exit codes (when run as a script):
  0  manifest holds
  1  drift: an explicit critical source has no critical-suite mapping

Usage:
  python scripts/ci/critical_manifest.py            # enforce (exit 1 on drift)
  python scripts/ci/critical_manifest.py --json     # machine report
  python scripts/ci/critical_manifest.py --coverage coverage.xml --thresholds ...
  python scripts/ci/critical_manifest.py --report-only   # never fails
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "nexus_scalp"
MANIFEST = REPO_ROOT / "tests" / "critical_suite.txt"
SLOW_MANIFEST = REPO_ROOT / "tests" / "slow_suite.txt"
DEP_GRAPH = REPO_ROOT / "artifacts" / "dependency_intelligence" / "graph.json"

#: Modules whose failure modes move money. THE FLOOR - every entry MUST
#: keep at least one critical-suite test that exercises it. Adding a
#: module here is a governance act (taskboard row), not a drive-by.
REQUIRED_CRITICAL_SOURCES: dict[str, str] = {
    "nexus_scalp.application.live_engine": "tick pipeline / model hot-swap / state sync",
    "nexus_scalp.execution.order_manager": "60-scenario dispatch router, 11-state machine",
    "nexus_scalp.risk.risk_engine": "sizing, margin clamp, kill switch, spread gate",
    "nexus_scalp.signals.policy": "confidence semantics, guardian gate, NO_TRADE authority",
    "nexus_scalp.execution.position_state_machine": "hysteresis debounce / emergency bypass",
    "nexus_scalp.execution.recovery_budget": "recovery budget + horizon clamp",
    "nexus_scalp.features.schema_contract": "70D dimension/range/NaN contract",
    "nexus_scalp.application.live_freshness": "stale-market freshness gate",
    "nexus_scalp.research.splitting": "purge/embargo anti-leakage defaults",
    "nexus_scalp.model_generation.replay": "replay causality (no future data)",
    "nexus_scalp.strategies.factory.provider_gate": "single-flight provider broadcast",
    "nexus_scalp.release.update_engine.orchestrator": "update state machine + rollback",
    "nexus_scalp.release.update_engine.discovery": "release selection + SHA trust chain",
    "nexus_scalp.release.update_engine.rollback_state": "rollback engine + crash recovery",
}


#: Facade modules that re-export a whole subpackage. Tests importing the
#: facade exercise those submodules (linkage resolved from source by
#: _facade_modules_importing, never trusted blindly).
FACADE_MODULES: tuple[str, ...] = ("nexus_scalp.release.updater",)


def parse_manifest(path: Path = MANIFEST) -> list[str]:
    """critical_suite.txt lines: strip comments/blanks, keep order."""
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def critical_test_files() -> list[Path]:
    """Absolute paths of every test file in the critical suite that exists."""
    files = []
    for entry in parse_manifest(MANIFEST):
        p = REPO_ROOT / entry
        if p.exists():
            files.append(p)
    return files


def module_guarded_by(test_text: str, dotted: str) -> bool:
    """True when test text references the module (dotted path or leaf file).

    Matching is deliberately INCLUSIVE: importing the module, patching
    one of its attributes, or referencing its leaf file name all count
    as exercising it. The floor contract is "some critical test knows
    this module exists", not "reaches 100% of its branches".
    """
    leaf = dotted.rsplit(".", 1)[-1] + ".py"
    rel = dotted.replace(".", "/") + ".py"
    if dotted in test_text:
        return True
    if leaf in test_text:
        return True
    if rel in test_text:
        return True
    # `from nexus_scalp.signals import policy` style
    parent, _, last = dotted.rpartition(".")
    if parent and re.search(rf"from {re.escape(parent)} import .*\b{re.escape(last)}\b", test_text):
        return True
    return False


def _facade_modules_importing(target: str, facades: tuple[str, ...]) -> set[str]:
    """Facades whose ImportFrom statements resolve INTO ``target`` (AST).

    NSE owns facade modules (e.g. ``release.updater`` re-exports the
    ``release.update_engine`` package). A test importing the facade DOES
    exercise the submodules; this resolves that linkage from source instead
    of a hand-maintained alias table.
    """
    parts = target.split(".")
    if parts and parts[0] == "nexus_scalp":
        parts = parts[1:]
    if not parts:
        return set()
    mod_path = SRC_ROOT.joinpath(*parts)
    mod_path = (mod_path / "__init__.py") if mod_path.is_dir() else mod_path.with_suffix(".py")
    if not mod_path.exists():
        return set()
    hitting: set[str] = set()
    for facade in facades:
        fparts = facade.split(".")
        if fparts and fparts[0] == "nexus_scalp":
            fparts = fparts[1:]
        fpath = SRC_ROOT.joinpath(*fparts)
        fpath = (fpath / "__init__.py") if fpath.is_dir() else fpath.with_suffix(".py")
        if not fpath.exists():
            continue
        try:
            tree = ast.parse(fpath.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == target or node.module.startswith(target + "."))
            ):
                hitting.add(facade)
                break
    return hitting


def build_report() -> dict[str, Any]:
    crit_tests = critical_test_files()
    test_texts: dict[Path, str] = {}
    for t in crit_tests:
        try:
            test_texts[t] = t.read_text(encoding="utf-8", errors="replace")
        except OSError:
            test_texts[t] = ""

    def guards(dotted: str) -> list[str]:
        # Facade linkage: a test importing `release.updater as upd` exercises
        # every submodule the facade imports from (update_engine.*).
        facades = _facade_modules_importing(dotted, FACADE_MODULES)

        def hit(text: str) -> bool:
            if module_guarded_by(text, dotted):
                return True
            return any(module_guarded_by(text, f) for f in facades)

        return sorted(
            str(t.relative_to(REPO_ROOT)).replace("\\", "/")
            for t, text in test_texts.items()
            if hit(text)
        )

    # --- explicit floor ---
    floor_rows = []
    missing_floor: list[str] = []
    for dotted, why in sorted(REQUIRED_CRITICAL_SOURCES.items()):
        g = guards(dotted)
        row = {
            "module": dotted,
            "why": why,
            "tier": "explicit",
            "guardians": g,
        }
        if not g:
            row["status"] = "MISSING"
            missing_floor.append(dotted)
        else:
            row["status"] = "OK"
        floor_rows.append(row)

    # --- graph-discovered hotspots (drift visibility) ---
    # The dependency-intelligence graph is the repo's authoritative
    # hot-path inventory; nodes flagged criticality=HIGH there are
    # "discovered critical". A discovered-critical module with no
    # critical-suite guardian surfaces as drift (report-only WARNING:
    # the heuristic graph must not hard-fail CI; the explicit floor
    # above is the hard contract). Add a floor entry or a guardian to
    # clear the warning.
    discovered_rows = []
    missing_discovered: list[str] = []
    if DEP_GRAPH.exists():
        try:
            graph = json.loads(DEP_GRAPH.read_text(encoding="utf-8"))
            nodes = graph.get("nodes") or []
            for node in nodes:
                nid = node.get("id") or ""
                if not nid.startswith("mod:nexus_scalp."):
                    continue
                dotted = nid[len("mod:") :]
                if dotted in REQUIRED_CRITICAL_SOURCES:
                    continue
                if node.get("criticality") != "HIGH":
                    continue
                g = guards(dotted)
                row = {
                    "module": dotted,
                    "tier": "discovered",
                    "criticality": node.get("criticality"),
                    "guardians": g,
                }
                if not g:
                    row["status"] = "UNGUARDED_HOTSPOT"
                    missing_discovered.append(dotted)
                else:
                    row["status"] = "OK"
                discovered_rows.append(row)
        except (OSError, ValueError):
            discovered_rows.append(
                {
                    "module": "<dependency-graph>",
                    "status": "GRAPH_UNREADABLE",
                    "guardians": [],
                }
            )

    ok = not missing_floor
    return {
        "tool": "scripts/ci/critical_manifest.py",
        "ok": ok,
        "critical_test_files": len(crit_tests),
        "explicit_floor": floor_rows,
        "missing_explicit": missing_floor,
        "discovered_hotspots": discovered_rows,
        "unguarded_discovered": missing_discovered,
        "policy": (
            "explicit-floor modules with zero critical-suite guardians FAIL; "
            "graph-discovered hotspots (criticality=HIGH in the "
            "dependency-intelligence graph) without guardians surface as "
            "drift warnings (report-only)"
        ),
    }


# ---------------------------------------------------------------------------
# File-level coverage gate (P1)
# ---------------------------------------------------------------------------
#: Per-file line-coverage floors for the explicit floor modules. Values
#: measured from the critical-suite xdist run (2026-09-07) and set just
#: below the measured baseline so the gate is enforceable today and
#: ratchets up as coverage improves. RATCHET RULE: raising a threshold
#: here is the mechanism; there is no permanent "temporary" bypass.
COVERAGE_THRESHOLDS: dict[str, float] = {
    "nexus_scalp.application.live_engine": 20.0,
    "nexus_scalp.execution.order_manager": 55.0,
    "nexus_scalp.risk.risk_engine": 55.0,
    "nexus_scalp.signals.policy": 65.0,
    "nexus_scalp.execution.position_state_machine": 55.0,
    "nexus_scalp.features.schema_contract": 80.0,
    "nexus_scalp.application.live_freshness": 40.0,
    "nexus_scalp.research.splitting": 80.0,
}


def coverage_from_xml(xml_path: Path) -> dict[str, float]:
    """module dotted path -> line coverage % from a coverage.py XML report."""
    tree = ET.parse(xml_path)
    out: dict[str, float] = {}
    for cls in tree.iter("class"):
        filename = (cls.get("filename") or "").replace("\\", "/")
        if "nexus_scalp/" not in filename:
            continue
        dotted = filename.split("nexus_scalp/", 1)[1]
        if dotted.endswith(".py"):
            dotted = dotted[: -len(".py")]
        dotted = "nexus_scalp." + dotted.replace("/", ".")
        try:
            rate = float(cls.get("line-rate") or 0.0) * 100.0
        except ValueError:
            rate = 0.0
        out[dotted] = max(out.get(dotted, 0.0), rate)
    return out


def coverage_gate(xml_path: Path) -> dict[str, Any]:
    cov = coverage_from_xml(xml_path)
    rows = []
    failures: list[str] = []
    for dotted, threshold in sorted(COVERAGE_THRESHOLDS.items()):
        measured = cov.get(dotted)
        row = {
            "module": dotted,
            "threshold": threshold,
            "measured": measured,
        }
        if measured is None:
            row["status"] = "NO_DATA"
            failures.append(f"{dotted}: no coverage data (gate cannot verify)")
        elif measured + 1e-9 < threshold:
            row["status"] = "FAIL"
            failures.append(f"{dotted}: {measured:.1f}% < threshold {threshold:.1f}%")
        else:
            row["status"] = "PASS"
        rows.append(row)
    return {
        "tool": "scripts/ci/critical_manifest.py --coverage",
        "ok": not failures,
        "rows": rows,
        "failures": failures,
        "note": (
            "thresholds are the measured critical-suite baseline (2026-09-07) "
            "rounded down; ratchet upward via COVERAGE_THRESHOLDS, never by "
            "excluding a file"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="critical-path manifest enforcement")
    parser.add_argument("--json", action="store_true", help="machine-readable report only")
    parser.add_argument("--report-only", action="store_true", help="never exit non-zero")
    parser.add_argument(
        "--coverage", default="", help="coverage.py XML path for the file-level gate"
    )
    args = parser.parse_args(argv)

    report = build_report()
    cov_report = None
    if args.coverage:
        cov_path = Path(args.coverage)
        cov_report = (
            coverage_gate(cov_path)
            if cov_path.exists()
            else {"ok": False, "failures": [f"coverage XML not found: {cov_path}"]}
        )

    if args.json:
        payload = {"manifest": report, "coverage": cov_report}
        print(json.dumps(payload, indent=2))
    else:
        print(f"critical test files: {report['critical_test_files']}")
        for row in report["explicit_floor"]:
            mark = "OK " if row["status"] == "OK" else "MISS"
            print(f"  [{mark}] {row['module']}  guardians={len(row['guardians'])}")
        for m in report["missing_explicit"]:
            print(f"  DRIFT: explicit critical module unmapped: {m}")
        for row in report["discovered_hotspots"]:
            if row["status"] != "OK":
                print(f"  WARN: unguarded hotspot: {row['module']}")
        if cov_report is not None:
            for row in cov_report["rows"]:
                measured = row.get("measured")
                meas = f"{measured:.1f}%" if measured is not None else "NO_DATA"
                print(f"  COV[{row['status']}] {row['module']}: {meas} >= {row['threshold']:.1f}")
            for f in cov_report["failures"]:
                print(f"  COV-DRIFT: {f}")

    if args.report_only:
        return 0
    failed = (not report["ok"]) or (cov_report is not None and not cov_report["ok"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
