#!/usr/bin/env python3
"""Deterministic dependency-aware test-impact planner for NSE CI.

The SINGLE canonical test-selection engine (extends scripts/ci/classify_changes.py,
never replaces it). Implements the first shipping slice of the five-algorithm
architecture from the CI/CD architecture mission:

  ALGORITHM 1 — static AST import graph + reverse reachability (IMPLEMENTED)
  ALGORITHM 2 — dynamic coverage / pytest-testmon          (roadmap: shadow mode)
  ALGORITHM 3 — predictive ML ranking                      (roadmap: shadow mode)
  ALGORITHM 4 — risk-weighted set cover                    (v1: conservative union;
                                                            per-test weighting = roadmap)
  ALGORITHM 5 — content-addressed result reuse             (roadmap; the plan digest
                                                            emitted here is its key basis)

Design contract (non-negotiable):
  * Deterministic and auditable: identical inputs -> identical plan + digest.
  * Fail-safe: any graph/analysis uncertainty EXPANDS the selection, never shrinks it.
  * Mandatory safety expansion overrides every optimisation (trading-safety paths).
  * A changed production module with NO reachable test is reported as a
    missing mapping AND triggers the conservative fallback suite.
  * Global invalidation: conftest.py / pytest config / dependency manifests /
    core configuration always expand to the FULL critical suite.
  * The emitted plan carries a sha256 digest over its canonical JSON so a
    worker can recompute and PROVE it executes the exact same plan.

CLI:
  python scripts/ci/test_planner.py --files "a.py b.py" [--repo-root .]
         [--commit-sha <sha>] [--out plan.json]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

PLAN_VERSION = "1"

#: Repo-relative directories whose change activates the MANDATORY SAFETY
#: expansion (trading safety-critical: risk, execution, broker adapters,
#: state transitions, PAPER/LIVE contracts). These may never be narrowed away.
_SAFETY_SOURCE_PREFIXES = (
    "src/nexus_scalp/execution/",
    "src/nexus_scalp/adapters/",
    "src/nexus_scalp/domain/",
    "src/nexus_scalp/accounting/",
)

#: Files whose change invalidates ANY narrow selection (global invalidation).
_GLOBAL_INVALIDATION_FILES = frozenset(
    {
        "pyproject.toml",
        "requirements.lock",
        "requirements.txt",
        "uv.lock",
        "tests/conftest.py",
        "pytest.ini",
        "setup.cfg",
        "tox.ini",
    }
)

#: Prefixes treated as global configuration surfaces (same effect as above).
_GLOBAL_INVALIDATION_PREFIXES = ("configs/",)

#: The conservative fallback suite used when analysis is uncertain or a
#: changed production module has no reachable test.
_FALLBACK_SUITE_FILES = ("tests/critical_suite.txt", "tests/fast_suite.txt")

#: Only repo-internal modules participate in the graph (these top-level names).
_SOURCE_ROOTS = ("src/nexus_scalp", "tests")


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./").lstrip("/")


def _module_name_for_file(path: str) -> str | None:
    """Map a repo-relative .py path to its importable module name (best effort)."""
    p = _norm(path)
    if not p.endswith(".py"):
        return None
    if p.startswith("src/nexus_scalp/"):
        parts = p[len("src/") : -3].split("/")
    elif p.startswith("src/") and p.endswith(".py"):
        parts = p[len("src/") : -3].split("/")
    elif p.startswith("tests/"):
        # Test modules are addressed by their repo path, not dotted names.
        return None
    else:
        return None
    name = ".".join(x for x in parts if x and x != "__init__")
    return name or None


def _repo_module_from_import(module: str) -> str | None:
    """Normalise an absolute import target to a repo module name, or None."""
    if module.startswith("nexus_scalp"):
        return module
    return None


def _own_module_and_package(rel_path: str) -> tuple[str, str]:
    """Return (own_module, own_package) dotted names for a repo .py path.

    tests/unit/test_x.py   -> ("tests.unit.test_x", "tests.unit")
    src/nexus_scalp/a/b.py -> ("nexus_scalp.a.b", "nexus_scalp.a")
    src/nexus_scalp/a/__init__.py -> ("nexus_scalp.a", "nexus_scalp.a")
    """
    parts = rel_path[:-3].split("/")  # strip .py
    if parts and parts[0] == "src":
        parts = parts[1:]  # the package tree starts at the src/ children
    if parts and parts[-1] == "__init__":
        module = ".".join(parts[:-1]) or parts[-1]
        package = module
    else:
        module = ".".join(parts)
        package = ".".join(parts[:-1])
    return module, package


def _register_module_chain(found: set[str], module: str) -> None:
    """Register a module target plus its parent-package chain edges."""
    found.add(module)
    parts = module.split(".")
    for i in range(1, len(parts)):
        found.add(".".join(parts[:i]))


def _parse_imports(
    rel_path: str, path: Path, test_paths: set[str], repo_root: Path
) -> tuple[set[str], bool]:
    """Extract repo-internal import targets via AST, resolving RELATIVE imports.

    Returns (targets, uncertain). Relative imports are resolved against the
    file's own package. Any relative import that cannot be resolved to a node
    we actually know about marks the graph UNCERTAIN (which the planner turns
    into conservative expansion) — a silently missing dependency edge is
    never acceptable.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError, ValueError):
        # Unparseable file: conservative — treat as importing EVERYTHING.
        return {"<unknown>"}, True

    _own_module, own_package = _own_module_and_package(rel_path)
    own_pkg_parts = own_package.split(".") if own_package else []

    def resolve_relative(level: int, module: str | None, alias: str) -> str | None:
        base_parts = own_pkg_parts[: len(own_pkg_parts) - (level - 1)] if level > 1 else list(own_pkg_parts)
        if level - 1 > len(own_pkg_parts):
            return None  # escapes the repo tree
        parts = base_parts + ([module] if module else []) + ([alias] if alias else [])
        return ".".join(x for x in parts if x)

    found: set[str] = set()
    uncertain = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                m = _repo_module_from_import(alias.name)
                if m:
                    _register_module_chain(found, m)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                targets: list[str] = []
                if node.module:
                    # `from .pkg.mod import sym` — only pkg.mod is a module;
                    # the symbols are names INSIDE it, never module paths.
                    targets.append(resolve_relative(node.level, node.module, ""))
                else:
                    # `from . import mod1, mod2` — the aliases ARE submodules.
                    for alias in node.names:
                        if alias.name != "*":
                            targets.append(resolve_relative(node.level, None, alias.name))
                for cand in targets:
                    if not cand:
                        uncertain = True
                        continue
                    if cand.startswith("nexus_scalp"):
                        # Existence check: a RELATIVE import naming a module
                        # that is not in the tree is an unresolved edge —
                        # mark the graph uncertain rather than dropping it.
                        # nexus_scalp lives under src/ in the repo layout.
                        cand_base = repo_root / "src" / cand.replace(".", "/")
                        if (cand_base.with_suffix(".py")).is_file() or (
                            cand_base / "__init__.py"
                        ).is_file():
                            _register_module_chain(found, cand)
                        else:
                            uncertain = True
                    elif cand.replace(".", "/") + ".py" in test_paths:
                        # Edge to a known test-tree file (helpers/conftest), keyed
                        # by its repo path — the graph keys test nodes by path.
                        found.add(cand.replace(".", "/") + ".py")
                    elif cand + ".py" in test_paths:
                        found.add(cand + ".py")
                    else:
                        # Repo-internal relative import that does not resolve to
                        # any known node — the graph is uncertain.
                        uncertain = True
            else:
                m = _repo_module_from_import(node.module or "")
                if m:
                    _register_module_chain(found, m)
    return found, uncertain


def build_graph(
    repo_root: Path,
) -> tuple[dict[str, set[str]], set[str], bool]:
    """Return (module -> set(targets it imports), test file paths, uncertain)."""
    imports: dict[str, set[str]] = {}
    test_files: set[str] = set()
    uncertain = False
    # First pass: collect test-tree paths (relative imports inside tests
    # resolve to these path-keyed nodes).
    for base in _SOURCE_ROOTS:
        root = repo_root / base
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            rel = _norm(str(path.relative_to(repo_root)))
            if "__pycache__" in rel:
                continue
            if rel.startswith("tests/"):
                test_files.add(rel)
    for base in _SOURCE_ROOTS:
        root = repo_root / base
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            rel = _norm(str(path.relative_to(repo_root)))
            if "__pycache__" in rel:
                continue
            mod = _module_name_for_file(rel) or rel
            targets, u = _parse_imports(rel, path, test_files, repo_root)
            if u:
                uncertain = True
            if rel.startswith("tests/"):
                test_files.add(rel)
                imports[rel] = targets
            else:
                imports[mod] = targets
    return imports, test_files, uncertain


def _reachable_tests(
    changed_modules: set[str], imports: dict[str, set[str]], test_files: set[str]
) -> tuple[set[str], set[str]]:
    """Reverse reachability: tests whose import chain reaches a changed module.

    Returns (selected_tests, covered_changed_modules). A module is "covered"
    when at least one selected test's import chain reaches it — direct import
    OR transitive through other repo modules.
    """
    # Build module -> importers index (reverse edges), including test edges.
    importers: dict[str, set[str]] = {}
    for node, targets in imports.items():
        for t in targets:
            importers.setdefault(t, set()).add(node)

    selected: set[str] = set()
    covered: set[str] = set()
    frontier = set(changed_modules)
    visited: set[str] = set()
    while frontier:
        mod = frontier.pop()
        if mod in visited:
            continue
        visited.add(mod)
        if mod.startswith("tests/"):
            # A changed test file covers itself (it always runs).
            covered.add(mod)
        for importer in importers.get(mod, ()):
            if importer.startswith("tests/"):
                selected.add(importer)
                covered.add(mod)
            else:
                covered.add(mod)
                if importer not in visited:
                    frontier.add(importer)
    return selected, covered


def _fallback_tests(repo_root: Path) -> set[str]:
    out: set[str] = set()
    for f in _FALLBACK_SUITE_FILES:
        p = repo_root / f
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    out.add(line)
    return out


def plan(
    changed_files: list[str],
    repo_root: Path,
    commit_sha: str = "",
    event: str = "",
    full_suite_required: bool = False,
) -> dict[str, Any]:
    """Build the deterministic test plan. See module docstring for contract."""
    changed = [_norm(f) for f in changed_files if f and f.strip()]
    selected: dict[str, set[str]] = {}  # test -> reasons
    skipped_groups: list[dict[str, str]] = []
    missing_mappings: list[str] = []
    notes: list[str] = []

    python_changed = sorted(
        f for f in changed if f.endswith(".py") and f.startswith("src/")
    )
    tests_changed = sorted(f for f in changed if f.startswith("tests/") and f.endswith(".py"))
    global_invalidation = sorted(
        f
        for f in changed
        if f in _GLOBAL_INVALIDATION_FILES
        or any(f.startswith(p) for p in _GLOBAL_INVALIDATION_PREFIXES)
    )
    safety_changed = sorted(
        f for f in python_changed if any(f.startswith(p) for p in _SAFETY_SOURCE_PREFIXES)
    )

    fallback_reason: str | None = None
    if full_suite_required or event in ("schedule",):
        fallback_reason = "full validation requested by event/policy"
    elif global_invalidation:
        fallback_reason = "global invalidation: " + ", ".join(global_invalidation[:4])
    else:
        imports, _test_files, graph_uncertain = build_graph(repo_root)
        if "<unknown>" in {t for v in imports.values() for t in v}:
            fallback_reason = (
                fallback_reason
                or "unparseable python file encountered — conservative expansion"
            )
        if graph_uncertain:
            fallback_reason = (
                fallback_reason
                or "unresolved relative imports — graph uncertain, conservative expansion"
            )
        changed_modules = {
            m for m in (_module_name_for_file(f) for f in python_changed) if m
        }
        # Changed test-tree files are graph nodes too (path-keyed): their
        # importers (tests using a shared helper) must be selected as well.
        for t in tests_changed:
            if t in imports:
                changed_modules.add(t)
        if changed_modules:
            reach, covered = _reachable_tests(changed_modules, imports, _test_files)
            for t in reach:
                selected.setdefault(t, set()).add("static_dependency")
            uncovered = sorted(changed_modules - covered)
            if uncovered:
                # Report the changed FILE PATHS (not dotted names) so the gap
                # is directly actionable for a developer.
                module_to_path = {
                    _module_name_for_file(f): f for f in python_changed
                }
                missing_mappings.extend(
                    module_to_path.get(m, m) for m in uncovered
                )
                fallback_reason = fallback_reason or (
                    "missing test mapping for: "
                    + ", ".join(module_to_path.get(m, m) for m in uncovered[:6])
                )
        for t in tests_changed:
            selected.setdefault(t, set()).add("test_changed")

    if fallback_reason:
        for t in _fallback_tests(repo_root):
            selected.setdefault(t, set()).add("conservative_fallback")
        notes.append(fallback_reason)

    # MANDATORY SAFETY expansion — overrides any optimisation (mission rule).
    if safety_changed:
        for t in _fallback_tests(repo_root):
            selected.setdefault(t, set()).add("mandatory_safety")
        notes.append(
            "safety-critical modules changed: " + ", ".join(safety_changed[:6])
        )

    # CI tooling (mirrored byte-identically in the C engine): scripts/ci/*.py
    # map to their tests/ci/test_<name>.py; workflow changes select the whole
    # tests/ci integrity suite.
    ci_tooling = sorted(
        f for f in changed if f.startswith("scripts/ci/") and f.endswith(".py")
    )
    workflows_changed = any(f.startswith(".github/workflows/") for f in changed)
    if ci_tooling or workflows_changed:
        for f in ci_tooling:
            cand = "tests/ci/test_" + os.path.basename(f)
            if (repo_root / cand).is_file():
                selected.setdefault(cand, set()).add("ci_tooling")
        if workflows_changed:
            ci_dir = repo_root / "tests" / "ci"
            if ci_dir.is_dir():
                for t in sorted(ci_dir.glob("test_*.py")):
                    selected.setdefault(
                        _norm(str(t.relative_to(repo_root))), set()
                    ).add("ci_tooling")

    if not python_changed and not tests_changed:
        skipped_groups.append(
            {"name": "python-tests", "reason": "no python source or test files changed"}
        )

    selected_tests = sorted(
        (
            {
                "id": t,
                "reason": sorted(reasons),
                "priority": "critical"
                if "mandatory_safety" in reasons or "conservative_fallback" in reasons
                else "normal",
            }
            for t, reasons in selected.items()
        ),
        key=lambda entry: entry["id"],
    )

    plan_obj: dict[str, Any] = {
        "commit_sha": commit_sha,
        "plan_version": PLAN_VERSION,
        "selection_mode": "targeted" if not fallback_reason else "expanded",
        "selected_tests": selected_tests,
        "cached_results": [],
        "skipped_groups": skipped_groups,
        "missing_mappings": sorted(missing_mappings),
        "full_suite_required": bool(fallback_reason),
        "fallback_reason": fallback_reason,
        "notes": notes,
    }
    canonical = json.dumps(plan_obj, sort_keys=True, separators=(",", ":"))
    plan_obj["plan_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return plan_obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--files", required=True, help="space-separated changed file list")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--commit-sha", default="")
    ap.add_argument("--event", default="")
    ap.add_argument("--full-suite", action="store_true")
    ap.add_argument("--out", default="", help="write plan JSON here (also stdout)")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    obj = plan(
        changed_files=args.files.split(),
        repo_root=repo_root,
        commit_sha=args.commit_sha,
        event=args.event,
        full_suite_required=args.full_suite,
    )
    text = json.dumps(obj, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    # Machine-readable outputs for GITHUB_OUTPUT consumers.
    gout = os.environ.get("GITHUB_OUTPUT")
    if gout:
        with open(gout, "a", encoding="utf-8") as fh:
            fh.write(f"plan_digest={obj['plan_digest']}\n")
            fh.write(f"selection_mode={obj['selection_mode']}\n")
            fh.write(f"full_suite_required={str(obj['full_suite_required']).lower()}\n")
            fh.write("selected_tests=" + json.dumps([t["id"] for t in obj["selected_tests"]]) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
