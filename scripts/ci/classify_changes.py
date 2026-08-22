#!/usr/bin/env python3
"""Canonical change classifier for NexusTradingForexBot (NSE) CI.

SINGLE SOURCE OF TRUTH for "what changed in this repository".

Every workflows/ CI job used to re-derive "did Python change?" / "did the
frontend change?" with its own ad-hoc `git diff --name-only` + glob formula.
That drifts: one YAML greps `src/`, another greps `src/nexus_scalp`, a third
misses `scripts/`. This module is the ONE formula. Workflows call it once,
consume its JSON, and branch on lane booleans.

LANES (derived from the ACTUAL repository architecture, not imported):
  python   - Python package / test / training / ML / config (the domain core)
  web      - vanilla-JS Web UI (Web/, tests/js/)
  js       - (alias of `web`; kept so callers may use either name)
  docker   - container build/runtime path (Dockerfile, compose, docker/)
  ci       - CI/CD itself (workflows, this script, CI scripts)
  docs     - documentation-only changes
  deps     - dependency manifest changes (pyproject/requirements/uv.lock)
  scripts  - repository build/packaging/doctor PowerShell + shell scripts
  release  - installer + release packaging bits

A file may map to MORE THAN ONE lane (e.g. a new Web/foo.js that is also
documented touches both `web` and `docs`). The classifier is deterministic:
same input always yields the same set of lanes.

Usage:
  classify_changes.py --base BASE_SHA --head HEAD_SHA [--format json|lines|gha]
  classify_changes.py --files "a b c"            # explicit file list
  classify_changes.py --changed-since-ref REF   # diff HEAD vs REF

Exit code 0 always (classification is advisory; the caller decides what to run).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# All lanes the classifier can emit. Workflows must only branch on these names.
KNOWN_LANES = (
    "python",
    "web",
    "js",
    "docker",
    "ci",
    "docs",
    "deps",
    "scripts",
    "release",
)

# Documentation-only prefixes — when ONLY these changed, `docs` is set and
# `python` is NOT (so a doc-only PR does not trigger the heavy Python gate).
_DOC_DIRS = ("docs/", "agents/")
_DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt")
# Markdown files that are NOT "documentation" for gating purposes: this very
# contract file is code-adjacent but treated as docs; README stays docs.
_DOC_NAMES = ("README.md", "PROJECT_GRAPH.md", "AGENTS.md")


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def _match_any(path: str, prefixes: tuple[str, ...], suffixes: tuple[str, ...]) -> bool:
    if any(path.startswith(p) for p in prefixes):
        return True
    return any(path.endswith(s) for s in suffixes)


def classify_file(path: str) -> set[str]:
    """Return the set of lanes a single changed path belongs to."""
    p = _norm(path)
    lanes: set[str] = set()

    # ---- CI / CD itself -------------------------------------------------
    if (
        p.startswith(".github/")
        or p in ("beforePush.ps1", "beforePush.sh")
        or p.startswith("scripts/ci/")
        or p.endswith(".github/workflows")
    ):
        lanes.add("ci")

    # ---- Docker / container path ----------------------------------------
    if (
        p in ("Dockerfile", "docker-compose.yml")
        or p.startswith("docker/")
        or p.startswith(".dockerignore")
        or p.startswith(".devcontainer/")
    ):
        lanes.add("docker")

    # ---- Dependency manifests ------------------------------------------
    if p in (
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "Directory.Packages.props",
    ):
        lanes.add("deps")
        # uv.lock / pyproject changes also affect Python resolution.
        lanes.add("python")

    # ---- Web / vanilla JS frontend --------------------------------------
    if p.startswith("Web/") or p.startswith("tests/js/"):
        lanes.add("web")
        lanes.add("js")

    # ---- Release / installer / packaging --------------------------------
    if (
        p.startswith("installer/")
        or p.startswith("release/")
        or p.startswith("src/nexus_scalp/release/")
        or p == "NexusTradingForexBot.iss"
        or p.startswith("scripts/build/")
    ):
        lanes.add("release")
        # install/package scripts are also PowerShell/shell scripts
        if p.endswith(".ps1") or p.endswith(".sh"):
            lanes.add("scripts")

    # ---- Repository scripts (doctor/start/reset PowerShell+sh, excluding
    #      the CI tooling dir and the build/package dir handled above) ------
    if (
        p.startswith("scripts/")
        and (p.endswith(".ps1") or p.endswith(".sh") or p.endswith(".py"))
        and not p.startswith("scripts/ci/")
        and not p.startswith("scripts/build/")
    ):
        lanes.add("scripts")

    # ---- Python package / tests / training / config ---------------------
    if (
        p.startswith("src/")
        or (p.startswith("tests/") and not p.startswith("tests/js/"))
        or p.startswith("configs/")
        or p in ("pyproject.toml", "requirements.txt")
        or p.startswith("data/")
        or p == "main.py"
    ) or (
        p.endswith(".py") and not p.startswith("scripts/ci/") and not p.startswith("scripts/build/")
    ):
        lanes.add("python")

    # ---- Documentation-only ---------------------------------------------
    if (
        (p.startswith("docs/") and p.endswith(_DOC_SUFFIXES))
        or (p in _DOC_NAMES)
        or (p.startswith("agents/") and p.endswith(_DOC_SUFFIXES))
    ):
        lanes.add("docs")

    return lanes


def classify_files(paths: list[str]) -> dict[str, bool]:
    """Classify many files into a lane -> bool map.

    Also derives `docs_only`: True iff at least one doc path changed AND
    every changed path is documentation-only (so callers can skip Python).
    """
    lanes: set[str] = set()
    doc_only = True
    any_doc = False
    non_doc_lanes: set[str] = set()
    for path in paths:
        fl = classify_file(path)
        lanes |= fl
        # A path contributes to "docs-only" only if it is pure docs.
        pure_doc = fl == {"docs"} or (fl.issubset({"docs", "ci"}) and "docs" in fl)
        if pure_doc:
            any_doc = True
        else:
            # remember which non-doc lanes this path would trigger
            non_doc_lanes |= fl
            doc_only = False
    result = {lane: (lane in lanes) for lane in KNOWN_LANES}
    result["docs_only"] = bool(any_doc and doc_only and not non_doc_lanes)
    return result


def _git_diff_names(base: str, head: str, root: Path) -> list[str]:
    """Return changed file paths between base..head (added/modified/deleted/
    renamed). Deleted files still matter (removing a Python file is a Python
    change)."""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-status", "--no-renames", f"{base}...{head}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        return []
    names: list[str] = []
    for raw in out.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t")
        # status TAB path  (or TAB old TAB new for renames — but we disabled
        # rename detection so it's status TAB path)
        if len(parts) >= 2:
            names.append(parts[-1])
    return names


def _git_changed_since_ref(ref: str, root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", ref, "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify repository changes into NSE CI lanes")
    parser.add_argument("--base", default="", help="base SHA for git diff")
    parser.add_argument("--head", default="HEAD", help="head SHA for git diff")
    parser.add_argument("--changed-since-ref", default="", help="diff HEAD against this ref")
    parser.add_argument("--files", default="", help="space-separated explicit file list")
    parser.add_argument(
        "--format",
        choices=["json", "lines", "gha"],
        default="json",
        help="json (default), lines (space-joined lanes), gha (set $GITHUB_OUTPUT 'lanes=...')",
    )
    parser.add_argument("--root", default=str(REPO_ROOT), help="repo root (for tests)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    if args.files:
        paths = [p for p in args.files.split() if p]
    elif args.changed_since_ref:
        paths = _git_changed_since_ref(args.changed_since_ref, root)
    elif args.base:
        paths = _git_diff_names(args.base, args.head, root)
    else:
        # No selector: default to working-tree diff vs HEAD (local dev).
        paths = _git_changed_since_ref("HEAD", root)

    result = classify_files(paths)
    active = [k for k, v in result.items() if v and k != "docs_only"]

    if args.format == "lines":
        print(" ".join(active))
    elif args.format == "gha":
        # Write to $GITHUB_OUTPUT for a later step to read via fromJSON.
        out_path = os.environ.get("GITHUB_OUTPUT", "")
        lane_json = json.dumps(result)
        if out_path:
            with open(out_path, "a", encoding="utf-8") as fh:
                fh.write(f"lanes<<EOF\n{lane_json}\nEOF\n")
                fh.write(f"active_lanes={' '.join(active)}\n")
        print(lane_json)
    else:
        print(json.dumps(result, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
